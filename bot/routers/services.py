# bot/routers/services.py
from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.repositories.services import ServicesRepository
from app.repositories.users import UsersRepository
from app.services.controld import ControlDService  # ControlD dynamic profile wrapper
from bot import menu_actions
from bot import texts
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.services import ServiceActionCallback

router = Router(name="services")


def _get_service_manage_keyboard(service_id: int) -> InlineKeyboardMarkup:
    """
    Generates a unified active service management keyboard.
    Appends the dynamic location changer option to the bottom [1].
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔗 لینک‌های اتصال",
        callback_data=ServiceActionCallback(action="link", service_id=service_id)
    )
    builder.button(
        text="📊 وضعیت سرویس",
        callback_data=ServiceActionCallback(action="status", service_id=service_id)
    )
    builder.button(
        text="🗺 تغییر سرور / لوکیشن",
        callback_data=f"change_location_select:{service_id}"
    )
    builder.adjust(1)
    return builder.as_markup()


@router.message(F.text == texts.BTN_MY_SERVICES)
async def my_services(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await menu_actions.show_my_services(message, session)


@router.callback_query(ServiceActionCallback.filter(F.action.in_({"link", "status", "renew"})))
async def service_action(
    callback: CallbackQuery,
    callback_data: ServiceActionCallback,
    session: AsyncSession,
) -> None:
    await callback.answer()
    if callback.from_user is None:
        return

    user = await UsersRepository(session).get_by_telegram_id(callback.from_user.id)
    if user is None:
        await _safe_answer(callback, "ابتدا /start را ارسال کنید.")
        return

    service = await ServicesRepository(session).get_user_service(callback_data.service_id, user.id)
    if service is None:
        await _safe_answer(callback, "این سرویس پیدا نشد یا متعلق به حساب شما نیست.")
        return

    if callback_data.action == "renew":
        await _safe_answer(
            callback,
            "♻️ تمدید مستقیم سرویس در حال حاضر فعال نیست.\n\nبرای تمدید، لطفاً از بخش «خرید اشتراک» همان پلن را مجدداً خریداری کنید تا زمان آن به این سرویس افزوده شود.",
        )
        return

    if callback_data.action == "link":
        text = f"""🔗 لینک‌های سرویس <code>{escape(service.username)}</code>

<b>لینک اشتراک DoT:</b>
<code>{escape(service.subscription_link or "ثبت نشده")}</code>

<b>لینک کانفیگ DoH:</b>
<code>{escape(service.config_link or "ثبت نشده")}</code>"""
        
        await callback.message.edit_text(text, reply_markup=_get_service_manage_keyboard(service.id), parse_mode="HTML")
        return

    # Default action: status
    text = menu_actions.format_service_summary(service)
    await callback.message.edit_text(text, reply_markup=_get_service_manage_keyboard(service.id), parse_mode="HTML")


# ============================================================================
# DYNAMIC LOCATION CHANGER ACTION HANDLERS [1]
# ============================================================================

@router.callback_query(F.data.startswith("change_location_select:"), StateFilter("*"))
async def change_location_select(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if callback.message is None:
        return
        
    service_id = int(callback.data.split(":")[1])
    service = await ServicesRepository(session).get(service_id)
    if service is None:
        await callback.message.answer("❌ سرویس پیدا نشد.")
        return

    # Fetch available profiles/locations in real-time from Control D API [1]
    controld_service = ControlDService(settings)
    profiles = await controld_service.fetch_controld_profiles()
    
    if not profiles:
        await callback.message.answer("❌ خطایی در بارگذاری سرورهای معتبر رخ داد یا سروری تعریف نشده است.")
        return

    builder = InlineKeyboardBuilder()
    for p in profiles:
        p_name = p.get("name") or "لوکیشن"
        builder.button(
            text=f"📍 {p_name}",
            callback_data=f"apply_loc_change:{service_id}:{p['id']}:{p_name}"
        )
    # Allows returning back to the active service status summary
    builder.button(text="↩️ بازگشت", callback_data=ServiceActionCallback(action="status", service_id=service_id))
    builder.adjust(1)

    await callback.message.edit_text(
        f"🗺 تعویض سرور / لوکیشن سرویس <b>{escape(service.username)}</b>\n\n"
        f"یک سرور از لیست زیر انتخاب کنید. تغییر لوکیشن بلافاصله اعمال می‌شود و نیاز به تغییر تنظیمات یا کپی مجدد لینک در گوشی خود ندارید:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("apply_loc_change:"), StateFilter("*"))
async def apply_location_change(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if callback.message is None:
        return

    parts = callback.data.split(":")
    service_id = int(parts[1])
    profile_id = parts[2]
    profile_name = parts[3]

    service = await ServicesRepository(session).get(service_id)
    if service is None or not service.controld_device_id:
        await callback.message.answer("❌ سرویس یا شناسه دستگاه معتبر یافت نشد.")
        return

    await callback.message.edit_text("⚙️ در حال تغییر لوکیشن سرور شما...")

    # Instantly update device's linked Profile/Location inside Control D backend [1]
    controld_service = ControlDService(settings)
    success = await controld_service.update_device_profile(service.controld_device_id, profile_id)

    if success:
        await callback.message.answer(
            f"✅ سرور دستگاه <code>{escape(service.username)}</code> با موفقیت به <b>{escape(profile_name)}</b> تغییر یافت!\n\n"
            f"تغییرات به صورت آنی روی دی‌ان‌اس اختصاصی شما اعمال شد.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML"
        )
    else:
        await callback.message.answer("❌ خطا در ارتباط با سرورهای Control D. تغییر لوکیشن انجام نشد.")


async def _safe_answer(callback: CallbackQuery, text: str) -> None:
    if callback.message:
        try:
            await callback.message.edit_text(text)
        except Exception:
            await callback.message.answer(text)