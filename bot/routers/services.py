# bot/routers/services.py
from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Plan, VPNService
from app.repositories.services import ServicesRepository
from app.repositories.users import UsersRepository
from app.services.controld import ControlDService  # ControlD dynamic profile wrapper
from bot import menu_actions
from bot import texts
from bot.keyboards.main_menu import main_menu_keyboard
from bot.keyboards.services import ServiceActionCallback

router = Router(name="services")


def _get_service_manage_keyboard(service_id: int) -> InlineKeyboardMarkup:
    """Generates active service management keyboard."""
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
        text="🗺 تنظیم لوکیشن سرویس‌ها",
        callback_data=f"service_routing_menu:{service_id}"  # Triggers service selection [1]
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
# SERVICE ROUTING CONTROLLER (Interactive Redirection Menu) [1]
# ============================================================================

# Define popular services supported by Control D
POPULAR_SERVICES = [
    {"pk": "netflix", "name": "🎬 Netflix"},
    {"pk": "youtube", "name": "📹 YouTube"},
    {"pk": "spotify", "name": "🎵 Spotify"},
    {"pk": "disney", "name": "🏰 Disney+"},
    {"pk": "twitch", "name": "🎮 Twitch"},
    {"pk": "chatgpt", "name": "🤖 ChatGPT / OpenAI"}
]


@router.callback_query(F.data.startswith("service_routing_menu:"), StateFilter("*"))
async def service_routing_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    """Lists popular web services the user can customize [1]."""
    await callback.answer()
    if callback.message is None:
        return
        
    service_id = int(callback.data.split(":")[1])
    service = await ServicesRepository(session).get(service_id)
    if service is None:
        await callback.message.answer("❌ سرویس پیدا نشد.")
        return

    builder = InlineKeyboardBuilder()
    for s in POPULAR_SERVICES:
        builder.button(
            text=s["name"],
            callback_data=f"select_srv_loc:{service_id}:{s['pk']}"  # Selected service [1]
        )
    builder.button(text="↩️ بازگشت", callback_data=ServiceActionCallback(action="status", service_id=service_id))
    builder.adjust(2)

    await callback.message.edit_text(
        f"🗺 <b>تنظیم لوکیشن سرویس‌ها</b> | دستگاه: <code>{escape(service.username)}</code>\n\n"
        f"لطفاً برنامه‌ای که می‌خواهید لوکیشن آن را اختصاصی تغییر دهید انتخاب کنید:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("select_srv_loc:"), StateFilter("*"))
async def select_service_location(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    """Fetches and displays available Control D proxies as buttons [1]."""
    await callback.answer()
    if callback.message is None:
        return

    parts = callback.data.split(":")
    service_id = int(parts[1])
    service_pk = parts[2]

    # Fetch available POP proxies from Control D API [1]
    controld_service = ControlDService(settings)
    proxies = await controld_service.fetch_controld_proxies()
    
    if not proxies:
        await callback.message.answer("❌ خطایی در بارگذاری لوکیشن‌های معتبر رخ داد.")
        return

    # Map target service display name
    service_display = next((s["name"] for s in POPULAR_SERVICES if s["pk"] == service_pk), service_pk)

    builder = InlineKeyboardBuilder()
    # Present popular proxy nodes (filter/limit if needed to keep buttons manageable)
    for p in proxies[:12]:
            p_name = f"{p['country_name']} ({p['code']})"  # <-- Ensure 'country_name' is used [1]
            builder.button(
                text=f"📍 {p_name}",
                callback_data=f"apply_loc_change:{service_id}:{service_pk}:{p['code']}:{p_name}"
            )
    builder.button(text="↩️ بازگشت", callback_data=f"service_routing_menu:{service_id}")
    builder.adjust(2)

    await callback.message.edit_text(
        f"🗺 تنظیم لوکیشن برای <b>{service_display}</b>\n\n"
        f"لطفاً کشوری که می‌خواهید ترافیک {service_display} از طریق آن عبور کند انتخاب کنید:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("apply_srv_route:"), StateFilter("*"))
async def apply_service_route(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    """Executes the PUT API call to redirect the service on Control D [1]."""
    await callback.answer()
    if callback.message is None:
        return

    parts = callback.data.split(":")
    service_id = int(parts[1])
    service_pk = parts[2]
    pop_code = parts[3]
    pop_name = parts[4]

    service = await ServicesRepository(session).get(service_id)
    if service is None or not service.controld_device_id:
        await callback.message.answer("❌ سرویس یا شناسه دستگاه معتبر یافت نشد.")
        return

    # Find the dynamic profile_id linked to this device
    controld_service = ControlDService(settings)
    device_data = await controld_service.fetch_controld_profiles()
    
    # Simple fallback check to extract profile_id from device details
    device_url = f"https://api.controld.com/devices/{service.controld_device_id}"
    headers = {
        "Authorization": f"Bearer {settings.controld_api_token}",
        "Content-Type": "application/json"
    }
    profile_id = None
    async with httpx.AsyncClient() as client:
        try:
            device_resp = await client.get(device_url, headers=headers, timeout=5.0)
            if device_resp.status_code == 200:
                profile_id = device_resp.json().get("body", {}).get("device", {}).get("profile_id")
        except Exception:
            pass

    if not profile_id:
        profile_id = settings.controld_profile_id

    if not profile_id:
        await callback.message.answer("❌ شناسه پروفایل این دستگاه یافت نشد.")
        return

    await callback.message.edit_text(f"⚙️ در حال انتقال لوکیشن سرویس شما به {pop_name}...")

    # Execute the PUT routing command using the dynamic profile_id [1]
    success = await controld_service.update_service_route(profile_id, service_pk, pop_code)

    service_display = next((s["name"] for s in POPULAR_SERVICES if s["pk"] == service_pk), service_pk)

    if success:
        await callback.message.answer(
            f"✅ ترافیک سرویس {service_display} شما با موفقیت به سرور <b>{escape(pop_name)}</b> هدایت شد!\n\n"
            f"تغییرات به صورت آنی و بدون نیاز به تغییر لینک دی‌ان‌اس روی دستگاه شما اعمال شد.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML"
        )
    else:
        await callback.message.answer("❌ خطا در ثبت لوکیشن در پنل Control D. مجدداً تلاش کنید.")


async def _safe_answer(callback: CallbackQuery, text: str) -> None:
    if callback.message:
        try:
            await callback.message.edit_text(text)
        except Exception:
            await callback.message.answer(text)