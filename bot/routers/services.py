# bot/routers/services.py
from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import httpx
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

# Dynamic Category Labels [1]
CATEGORY_MAP_FA = {
    "gaming": "🎮 بازی‌ها (Gaming)",
    "video": "🎬 رسانه و استریم (Video/Streaming)",
    "social": "💬 شبکه‌های اجتماعی (Social)",
    "ai": "🤖 هوش مصنوعی (AI & Tech)",
    "music": "🎵 موسیقی (Music)",
    "other": "🧩 سایر سرویس‌ها (Other)"
}


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
        callback_data=f"service_routing_menu:{service_id}"  # Triggers category selection [1]
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

@router.callback_query(F.data.startswith("service_routing_menu:"), StateFilter("*"))
async def service_routing_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    """Displays Category Selector for location management [1]."""
    await callback.answer()
    if callback.message is None:
        return
        
    service_id = int(callback.data.split(":")[1])
    service = await ServicesRepository(session).get(service_id)
    if service is None:
        await callback.message.answer("❌ سرویس پیدا نشد.")
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 کل ترافیک اینترنت (Default)", callback_data=f"select_srv_loc:{service_id}:default")
    
    # Generate Category selections
    for key, label in CATEGORY_MAP_FA.items():
        if key == "other":
            continue
        builder.button(text=label, callback_data=f"srv_manage_cat:{service_id}:{key}:0")
    builder.button(text="🧩 سایر سرویس‌ها (Other)", callback_data=f"srv_manage_cat:{service_id}:other:0")
    builder.button(text="↩️ بازگشت", callback_data=ServiceActionCallback(action="status", service_id=service_id))
    builder.adjust(1)

    await callback.message.edit_text(
        f"🗺 <b>تنظیم لوکیشن سرویس‌ها</b> | دستگاه: <code>{escape(service.username)}</code>\n\n"
        f"🗺 ابتدا دسته‌بندی ترافیکی مورد نظر خود را انتخاب کنید:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("srv_manage_cat:"), StateFilter("*"))
async def handle_srv_manage_cat(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    service_id = int(parts[1])
    category_key = parts[2]
    page = int(parts[3])
    
    # Query all services dynamically from Control D [1]
    # Inside bot/routers/services.py -> handle_srv_manage_cat()

    # Find the dynamic profile_id linked to this device
    controld = ControlDService(settings)
    
    device_url = f"https://api.controld.com/devices/{services.controld_device_id}"
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

    # Query all services dynamically from Control D [1]
    services = await controld.fetch_controld_services(profile_id)  # <-- Pass profile_id [1]
    if not services:
        await callback.message.answer("❌ خطایی در بارگذاری سرویس‌ها رخ داد.")
        return
        
    filtered = [s for s in services if s["category"] == category_key]
    filtered.sort(key=lambda x: (x["name"] or "").lower())
    
    # Apply clean pagination (10 items per page) [1]
    limit = 10
    start_idx = page * limit
    end_idx = start_idx + limit
    page_items = filtered[start_idx:end_idx]
    has_next = len(filtered) > end_idx

    builder = InlineKeyboardBuilder()
    for s in page_items:
        builder.button(
            text=s["name"] or s["pk"],
            callback_data=f"select_srv_loc:{service_id}:{s['pk']}"  # Selected game [1]
        )
    
    # Navigation Buttons [1]
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"srv_manage_cat:{service_id}:{category_key}:{page - 1}"))
    if has_next:
        nav_buttons.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"srv_manage_cat:{service_id}:{category_key}:{page + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)
        
    builder.row(InlineKeyboardButton(text="🔙 بازگشت به دسته‌بندی‌ها", callback_data=f"service_routing_menu:{service_id}"))
    builder.adjust(2)
    
    category_label = CATEGORY_MAP_FA.get(category_key, "سایر")
    await callback.message.edit_text(
        f"📂 دسته‌بندی انتخاب شده: <b>{category_label}</b> | صفحه {page + 1}\n\n"
        f"🎮 لطفاً سرویس مورد نظر خود را برای انتقال ترافیک انتخاب کنید:",
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

    builder = InlineKeyboardBuilder()
    for p in proxies[:12]:  # Show first 12 popular worldwide locations [1]
        p_name = f"{p['country_name']} ({p['code']})"
        builder.button(
            text=f"📍 {p_name}",
            callback_data=f"apply_loc_change:{service_id}:{service_pk}:{p['code']}:{p_name}"  # Apply routing [1]
        )
    builder.button(text="↩️ بازگشت", callback_data=f"service_routing_menu:{service_id}")
    builder.adjust(2)

    await callback.message.edit_text(
        f"🗺 تنظیم لوکیشن برای این سرویس\n\n"
        f"لطفاً کشوری که می‌خواهید ترافیک این سرویس از طریق آن عبور کند انتخاب کنید:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("apply_loc_change:"), StateFilter("*"))
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
    if service_pk == "default":
        success = await controld_service.update_profile_default(profile_id, pop_code) [1]
    else:
        success = await controld_service.update_service_route(profile_id, service_pk, pop_code) [1]

    if success:
        await callback.message.answer(
            f"✅ ترافیک سرویس شما با موفقیت به سرور <b>{escape(pop_name)}</b> هدایت شد!\n\n"
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