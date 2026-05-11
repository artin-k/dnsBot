from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape

from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Order, OrderKind, OrderStatus, VPNService
from app.repositories.dice_rolls import DiceRollsRepository
from app.repositories.orders import OrdersRepository
from app.repositories.plans import PlansRepository
from app.repositories.services import ServicesRepository
from app.repositories.test_accounts import TestAccountsRepository
from app.repositories.users import UsersRepository
from app.services.order_service import OrderService
from app.utils.codes import generate_discount_code
from app.utils.formatting import (
    format_datetime,
    format_money,
    format_order_status_fa,
    format_order_type_fa,
    format_remaining_time,
    format_service_status_fa,
)
from app.utils.tracking import generate_referral_code
from bot import texts
from bot.keyboards.buy import plans_keyboard
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.renewal import renewal_services_keyboard
from bot.keyboards.services import services_actions_keyboard
from bot.keyboards.tracking import orders_tracking_keyboard
from bot.keyboards.tutorials import tutorials_keyboard
from bot.keyboards.verification import phone_verification_keyboard
from bot.keyboards.wallet import wallet_keyboard
from bot.states.wallet import VerificationStates


async def show_main_menu(message: Message) -> None:
    await message.answer(texts.MAIN_MENU_TEXT, reply_markup=main_menu_keyboard())


async def show_buy_plans(message: Message, session: AsyncSession) -> None:
    plans = await PlansRepository(session).list_active()
    if not plans:
        await message.answer("در حال حاضر پلن فعالی برای خرید وجود ندارد.", reply_markup=main_menu_keyboard())
        return
    await message.answer(texts.BUY_PLANS_TEXT, reply_markup=plans_keyboard(plans))


async def show_renewal_services(message: Message, session: AsyncSession) -> None:
    user = await _get_current_user(message, session)
    if user is None:
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return

    services = await ServicesRepository(session).list_active_by_user(user.id)
    if not services:
        await message.answer("شما هنوز سرویس فعالی برای تمدید ندارید.", reply_markup=main_menu_keyboard())
        return

    await message.answer(
        "♻️ لطفاً سرویسی که می‌خواهید تمدید کنید را انتخاب کنید:",
        reply_markup=renewal_services_keyboard(services),
    )


async def show_my_services(message: Message, session: AsyncSession) -> None:
    user = await _get_current_user(message, session)
    if user is None:
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return

    services = await ServicesRepository(session).list_by_user(user.id)
    if not services:
        await message.answer("شما هنوز سرویس فعالی ندارید.", reply_markup=main_menu_keyboard())
        return

    lines = ["🛍 سرویس‌های شما"]
    for index, service in enumerate(services, start=1):
        lines.append(format_service_summary(service, index))

    await message.answer("\n".join(lines), reply_markup=services_actions_keyboard(services))


async def show_tariffs(message: Message, session: AsyncSession) -> None:
    plans = await PlansRepository(session).list_active()
    if not plans:
        await message.answer("در حال حاضر تعرفه فعالی ثبت نشده است.", reply_markup=main_menu_keyboard())
        return

    lines = ["💰 تعرفه اشتراک‌ها"]
    for index, plan in enumerate(plans, start=1):
        lines.append(
            f"""
{index}. {escape(plan.title)}
📦 حجم: {plan.volume_gb} گیگ
🗓 مدت اعتبار: {plan.duration_days} روز
💵 قیمت: {format_money(plan.price)} تومان"""
        )
        if plan.description:
            lines.append(f"📝 توضیحات: {escape(plan.description)}")

    lines.append("\nبرای خرید، از گزینه «🔐 خرید اشتراک» استفاده کنید.")
    await message.answer("\n".join(lines), reply_markup=main_menu_keyboard())


async def show_order_tracking(message: Message, session: AsyncSession, settings: Settings) -> None:
    user = await _get_current_user(message, session)
    if user is None:
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return

    orders = await OrdersRepository(session).list_by_user(user.id)
    order_service = OrderService(session, settings)
    for order in orders:
        if order.status == OrderStatus.PENDING_PAYMENT.value:
            await order_service.expire_order_if_unpaid(order)

    if not orders:
        await message.answer(
            "شما هنوز سفارشی ثبت نکرده‌اید.",
            reply_markup=orders_tracking_keyboard([], include_search=True),
        )
        return

    lines = ["📦 سفارش‌های شما", "آخرین سفارش‌ها به ترتیب جدیدترین:"]
    for index, order in enumerate(orders, start=1):
        lines.append(format_order_summary(order, index))

    await message.answer("\n".join(lines), reply_markup=orders_tracking_keyboard(orders))


async def show_referral(message: Message, session: AsyncSession, settings: Settings) -> None:
    user = await _get_current_user(message, session)
    if user is None:
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return

    if not user.referral_code:
        user.referral_code = generate_referral_code(user.telegram_id)
        await session.commit()

    bot_info = await message.bot.get_me()
    count = await UsersRepository(session).count_referrals(user.id)
    reward = settings.referral_reward_amount
    reward_line = (
        f"💰 پاداش هر دعوت موفق: {format_money(reward)} تومان"
        if reward > 0
        else "در حال حاضر پاداش مالی توسط مدیریت تنظیم نشده است."
    )

    await message.answer(
        f"""👥 زیرمجموعه‌گیری

با دعوت دوستان خود می‌توانید اعتبار هدیه دریافت کنید.

🔗 لینک دعوت اختصاصی شما:
https://t.me/{bot_info.username}?start={user.referral_code}

👤 تعداد زیرمجموعه‌های شما: {count}
{reward_line}""",
        reply_markup=main_menu_keyboard(),
    )


async def show_tutorials(message: Message) -> None:
    await message.answer(
        """📚 بخش آموزش

لطفاً سیستم‌عامل یا برنامه مورد نظر خود را انتخاب کنید:""",
        reply_markup=tutorials_keyboard(),
    )


async def show_support(message: Message, settings: Settings) -> None:
    await message.answer(
        f"""☎️ پشتیبانی

برای ارتباط با پشتیبانی به آیدی زیر پیام دهید:
@{escape(settings.support_username)}""",
        reply_markup=main_menu_keyboard(),
    )


async def show_wallet(message: Message, session: AsyncSession, state: FSMContext | None = None) -> None:
    user = await _get_current_user(message, session)
    if user is None:
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return
    if not user.is_phone_verified:
        if state is not None:
            await state.set_state(VerificationStates.waiting_contact)
            await state.update_data(next_section="wallet")
        await message.answer(
            """برای استفاده از این بخش، ابتدا باید شماره موبایل خود را تایید کنید.

لطفاً با دکمه زیر شماره موبایل تلگرام خود را ارسال کنید 👇""",
            reply_markup=phone_verification_keyboard(),
        )
        return

    await message.answer(
        f"""🏦 کیف پول شما

💵 موجودی فعلی: {format_money(user.wallet_balance)} تومان
📱 شماره تایید شده: {escape(user.phone_number or "-")}

لطفاً یکی از گزینه‌های زیر را انتخاب کنید:""",
        reply_markup=wallet_keyboard(),
    )


async def show_test_account(message: Message, session: AsyncSession) -> None:
    user = await _get_current_user(message, session)
    if user is None:
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return

    repo = TestAccountsRepository(session)
    existing_claim = await repo.get_user_claim(user.id)
    if existing_claim is not None:
        account = existing_claim.test_account
        await message.answer(
            f"""شما قبلاً اکانت تست دریافت کرده‌اید.

🔑 {escape(account.title)}

🔗 کانفیگ:
{escape(account.config_link)}"""
            + (f"\n\n🔗 لینک اشتراک:\n{escape(account.subscription_link)}" if account.subscription_link else ""),
            reply_markup=main_menu_keyboard(),
        )
        return

    account = await repo.get_available()
    if account is None:
        await message.answer("در حال حاضر اکانت تستی موجود نیست.", reply_markup=main_menu_keyboard())
        return

    await repo.create_claim(user_id=user.id, test_account=account)
    await session.commit()
    text = f"""🔑 اکانت تست شما آماده است

{escape(account.title)}

⏳ مدت تست: {account.duration_hours} ساعت

🔗 کانفیگ:
{escape(account.config_link)}"""
    if account.subscription_link:
        text += f"\n\n🔗 لینک اشتراک:\n{escape(account.subscription_link)}"
    await message.answer(text, reply_markup=main_menu_keyboard())


async def show_lucky_wheel(message: Message, session: AsyncSession, settings: Settings) -> None:
    user = await _get_current_user(message, session)
    if user is None:
        await message.answer("ابتدا /start را ارسال کنید.", reply_markup=main_menu_keyboard())
        return

    now = datetime.now(timezone.utc)
    repo = DiceRollsRepository(session)
    last_roll = await repo.get_last_by_user(user.id)
    if last_roll is not None:
        created_at = last_roll.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        next_roll_at = created_at + timedelta(hours=settings.dice_cooldown_hours)
        if now < next_roll_at:
            remaining = int((next_roll_at - now).total_seconds())
            await message.answer(
                f"""شما امروز شانس خود را امتحان کرده‌اید.

⏳ زمان باقی‌مانده تا تلاش بعدی: {format_remaining_time(remaining)}""",
                reply_markup=main_menu_keyboard(),
            )
            return

    await message.answer("🎲 تاس را می‌اندازیم...\nاگر عدد ۶ بیاورید، تخفیف هدیه می‌گیرید!")
    dice_message = await message.answer_dice(emoji="🎲")
    value = dice_message.dice.value if dice_message.dice else 1
    won = value == 6

    discount_code = None
    expires_at = None
    discount_percent = 0
    if won:
        discount_percent = settings.dice_win_discount_percent
        expires_at = now + timedelta(hours=settings.dice_discount_expire_hours)
        discount_code = await _generate_unique_discount_code(repo)

    await repo.create(
        user_id=user.id,
        dice_value=value,
        won=won,
        discount_percent=discount_percent,
        discount_code=discount_code,
        expires_at=expires_at,
    )
    await session.commit()

    if won and discount_code:
        await message.answer(
            f"""🎉 تبریک! عدد ۶ آوردید.

🎁 تخفیف شما: {discount_percent}٪
🎟 کد تخفیف: {discount_code}
⏳ اعتبار کد: {format_datetime(expires_at)}

در هنگام خرید می‌توانید از این کد استفاده کنید.""",
            reply_markup=main_menu_keyboard(),
        )
        return

    await message.answer(
        f"""متأسفانه این بار برنده نشدید.
عدد شما: {value}

فردا دوباره شانس خود را امتحان کنید 🍀""",
        reply_markup=main_menu_keyboard(),
    )


async def show_coming_soon(message: Message) -> None:
    await message.answer(texts.COMING_SOON_TEXT, reply_markup=main_menu_keyboard())


def format_service_summary(service: VPNService, index: int | None = None) -> str:
    prefix = f"\n{index}. " if index is not None else ""
    return f"""{prefix}{escape(service.username)}
⚡ پلن: {escape(service.plan.title if service.plan else "-")}
📦 حجم: {service.volume_gb} گیگ
🗓 تاریخ انقضا: {format_datetime(service.expire_at)}
📌 وضعیت: {format_service_status_fa(service.status)}
🔗 لینک اشتراک: {escape(service.subscription_link or "-")}
🔗 لینک کانفیگ: {escape(service.config_link or "-")}"""


def format_order_summary(order: Order, index: int | None = None) -> str:
    prefix = f"\n{index}. " if index is not None else "\n"
    lines = [
        f"{prefix}کد پیگیری: {order.tracking_code}",
        f"⚡ نوع سفارش: {format_order_type_fa(order.order_kind)}",
        f"⚡ پلن: {escape(order.plan.title if order.plan else '-')}",
        f"💵 مبلغ: {format_money(order.amount)} تومان",
        f"📌 وضعیت: {format_order_status_fa(order.status)}",
        f"🗓 تاریخ ثبت: {format_datetime(order.created_at)}",
    ]
    if order.status == OrderStatus.PENDING_PAYMENT.value:
        lines.append(f"⏳ مهلت پرداخت: {format_datetime(order.expires_at)}")
    return "\n".join(lines)


def format_order_detail(order: Order) -> str:
    service_username = order.custom_username or "-"
    if order.order_kind == OrderKind.RENEWAL.value and order.renewal_service:
        service_username = order.renewal_service.username

    receipt_status = "دریافت شده" if order.payment and order.payment.receipt_file_id else "ارسال نشده"
    lines = [
        "📦 جزئیات سفارش",
        "",
        f"🛒 کد پیگیری: {order.tracking_code}",
        f"⚡ نوع سفارش: {format_order_type_fa(order.order_kind)}",
        f"⚡ پلن: {escape(order.plan.title if order.plan else '-')}",
        f"💵 مبلغ: {format_money(order.amount)} تومان",
        f"📌 وضعیت سفارش: {format_order_status_fa(order.status)}",
        f"📎 وضعیت رسید: {receipt_status}",
        f"🔐 نام کاربری/سرویس: {escape(service_username)}",
        f"🗓 تاریخ ثبت: {format_datetime(order.created_at)}",
    ]
    if order.status == OrderStatus.PENDING_PAYMENT.value:
        lines.append(f"⏳ مهلت پرداخت: {format_datetime(order.expires_at)}")
    if order.paid_at:
        lines.append(f"💳 تاریخ پرداخت: {format_datetime(order.paid_at)}")
    if order.completed_at:
        lines.append(f"✅ تاریخ تکمیل: {format_datetime(order.completed_at)}")
    return "\n".join(lines)


async def _get_current_user(message: Message, session: AsyncSession):
    if message.from_user is None:
        return None
    return await UsersRepository(session).get_by_telegram_id(message.from_user.id)


async def _generate_unique_discount_code(repo: DiceRollsRepository) -> str:
    for _ in range(10):
        code = generate_discount_code()
        if await repo.get_by_discount_code(code) is None:
            return code
    raise RuntimeError("Could not generate unique discount code")
