# bot/notifications.py
from __future__ import annotations

from html import escape
import structlog
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Order, OrderKind, Payment, WalletTransaction, WalletWithdrawalRequest
from app.repositories.users import UsersRepository
from app.utils.formatting import format_money, format_order_type_fa
from app.utils.withdrawals import format_withdrawal_destination_fa, mask_destination
from bot.keyboards.admin import payment_review_keyboard, wallet_withdrawal_review_keyboard
from bot.keyboards.wallet import wallet_topup_review_keyboard

logger = structlog.get_logger(__name__)


def get_service_name_display(pk: str) -> str:
    """Helper to dynamically translate service keys to Persian titles [1]."""
    if pk == "default":
        return "کل ترافیک اینترنت (Default)"
    try:
        from bot.routers.buy import CATEGORIES
        for cat in CATEGORIES.values():
            for s in cat["services"]:
                if s["pk"] == pk:
                    return s["name"]
    except Exception:
        pass
    return pk.capitalize()


async def get_admin_ids(session: AsyncSession, settings: Settings) -> list[int]:
    admin_ids = set(settings.admin_ids)
    if settings.root_admin_telegram_id is not None:
        admin_ids.add(settings.root_admin_telegram_id)
    admin_ids.update(await UsersRepository(session).list_admin_telegram_ids())
    return sorted(admin_ids)


async def notify_admins_order_payment(
    *,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    payment: Payment,
    order: Order,
    receipt_file_id: str,
) -> int:
    caption = format_order_payment_admin_caption(order, payment)
    sent_count = 0
    for admin_id in await get_admin_ids(session, settings):
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=receipt_file_id,
                caption=caption,
                reply_markup=payment_review_keyboard(payment.id),
            )
            sent_count += 1
        except Exception as exc:
            logger.warning("admin_order_receipt_photo_failed", admin_id=admin_id, error=str(exc))
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=caption,
                    reply_markup=payment_review_keyboard(payment.id),
                )
                sent_count += 1
            except Exception as send_exc:
                logger.warning("admin_order_receipt_message_failed", admin_id=admin_id, error=str(send_exc))
    if sent_count == 0:
        logger.warning("no_admin_notified_for_order_payment", payment_id=payment.id, order_id=order.id)
    return sent_count


async def notify_admins_wallet_topup(
    *,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    transaction: WalletTransaction,
    receipt_file_id: str,
) -> int:
    caption = format_wallet_topup_admin_caption(transaction)
    sent_count = 0
    for admin_id in await get_admin_ids(session, settings):
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=receipt_file_id,
                caption=caption,
                reply_markup=wallet_topup_review_keyboard(transaction.id),
            )
            sent_count += 1
        except Exception as exc:
            logger.warning("admin_wallet_topup_photo_failed", admin_id=admin_id, error=str(exc))
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=caption,
                    reply_markup=wallet_topup_review_keyboard(transaction.id),
                )
                sent_count += 1
            except Exception as send_exc:
                logger.warning("admin_wallet_topup_message_failed", admin_id=admin_id, error=str(send_exc))
    if sent_count == 0:
        logger.warning("no_admin_notified_for_wallet_topup", transaction_id=transaction.id)
    return sent_count


async def notify_admins_wallet_withdrawal(
    *,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    withdrawal: WalletWithdrawalRequest,
) -> int:
    text = format_wallet_withdrawal_admin_text(withdrawal)
    sent_count = 0
    for admin_id in await get_admin_ids(session, settings):
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=wallet_withdrawal_review_keyboard(withdrawal.id),
            )
            sent_count += 1
        except Exception as exc:
            logger.warning("admin_wallet_withdrawal_message_failed", admin_id=admin_id, error=str(exc))
    if sent_count == 0:
        logger.warning("no_admin_notified_for_wallet_withdrawal", withdrawal_id=withdrawal.id)
    return sent_count


def format_order_payment_admin_caption(order: Order, payment: Payment) -> str:
    user = order.user
    username = f"@{user.telegram_username}" if user.telegram_username else "-"
    
    # --- FIXED: Parse custom metadata from custom_username cleanly ---
    raw_username = order.custom_username or "-"
    service_display = "-"
    country_display = "-"
    
    if "|" in raw_username:
        parts = raw_username.split("|")
        username_part = parts[0]
        service_pk = parts[1] if len(parts) > 1 else "default"
        pop_code = parts[2] if len(parts) > 2 else None
        
        service_display = get_service_name_display(service_pk)
        if pop_code:
            from app.services.controld import get_country_name_fa
            country_display = f"{get_country_name_fa(pop_code)} ({pop_code})"
        else:
            country_display = "پیش‌فرض"
    else:
        username_part = raw_username
        service_display = "کل ترافیک اینترنت (Default)"
        country_display = "پیش‌فرض"

    if order.renewal_service:
        username_part = order.renewal_service.username
        
    receipt_status = "رسید دریافت شده" if payment.receipt_file_id else "بدون رسید"
    inventory_line = ""
    if order.order_kind == OrderKind.PURCHASE.value:
        inventory_line = f"\n📦 کانفیگ رزرو شده: {'بله' if order.config_inventory_id else 'خیر'} | شناسه: {order.config_inventory_id or '-'}"
        
    return f"""🧾 پرداخت جدید در انتظار تایید

👤 کاربر: {escape(user.first_name or "-")}
🔗 یوزرنیم تلگرام: {escape(username)}
🆔 آیدی عددی: {user.telegram_id}
📱 موبایل: {escape(user.phone_number or "-")}
🛒 کد پیگیری: {order.tracking_code}
⚡ نوع سفارش: {format_order_type_fa(order.order_kind)}
⚡ پلن: {escape(order.plan.title if order.plan else "-")}
💵 مبلغ: {format_money(order.amount)} تومان
🔐 نام کاربری دستگاه: <code>{escape(username_part)}</code>
🎮 برنامه/بازی: <b>{escape(service_display)}</b>
🗺 سرور (کشور): <b>{escape(country_display)}</b>
📎 وضعیت رسید: {receipt_status}{inventory_line}"""


def format_wallet_topup_admin_caption(transaction: WalletTransaction) -> str:
    user = transaction.user
    username = f"@{user.telegram_username}" if user.telegram_username else "-"
    return f"""🏦 شارژ کیف پول جدید در انتظار تایید

👤 کاربر: {escape(user.first_name or "-")}
🔗 یوزرنیم تلگرام: {escape(username)}
🆔 آیدی عددی: {user.telegram_id}
📱 موبایل: {escape(user.phone_number or "-")}
💵 مبلغ: {format_money(transaction.amount)} تومان"""


def format_wallet_withdrawal_admin_text(withdrawal: WalletWithdrawalRequest) -> str:
    user = withdrawal.user
    username = f"@{user.telegram_username}" if user.telegram_username else "-"
    return f"""💸 درخواست برداشت جدید

👤 کاربر: {escape(user.first_name or "-")}
🔗 یوزرنیم: {escape(username)}
🆔 آیدی عددی: {user.telegram_id}
📱 موبایل: {escape(user.phone_number or "-")}

💵 مبلغ برداشت: {format_money(withdrawal.amount)} تومان
روش دریافت: {format_withdrawal_destination_fa(withdrawal.destination_type)}
شماره مقصد: {escape(mask_destination(withdrawal.destination_type, withdrawal.destination_number))}
نام صاحب حساب: {escape(withdrawal.account_holder_name or "-")}
توضیحات کاربر: {escape(withdrawal.user_note or "-")}

وضعیت: در انتظار بررسی"""