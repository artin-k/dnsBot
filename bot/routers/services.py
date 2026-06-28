# bot/routers/services.py
from html import escape
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import httpx

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import Settings
from app.models import Plan, VPNService
from app.repositories.services import ServicesRepository
from app.repositories.users import UsersRepository
from app.services.controld import ControlDService, get_country_name_fa, get_flag_emoji, get_city_name_fa  # Wrapper integrations
from app.utils.formatting import format_datetime  # Standard datetime formatter
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

# Define popular services supported by Control D
POPULAR_SERVICES = [
    {"pk": "default", "name": "🌐 کل ترافیک اینترنت (Default)"},
    {"pk": "callofduty", "name": "🎮 Call of Duty"},
    {"pk": "apexlegends", "name": "🎮 Apex Legends"},
    {"pk": "pubg", "name": "🎮 PUBG Mobile"},
    {"pk": "fortnite", "name": "🎮 Fortnite"},
    {"pk": "youtube", "name": "📹 YouTube"},
    {"pk": "netflix", "name": "🎬 Netflix"}
]


def format_service_item_display(service: VPNService, index: int) -> str:
    """
    Parses database metadata out of raw pipe-delimited username strings 
    and formats them into beautiful Persian representations with flags [1].
    """
    raw_username = service.username or ""
    service_display = "کل ترافیک اینترنت (Default)"
    country_display = "پیش‌فرض"
    username_part = raw_username
    
    if "|" in raw_username:
        parts = raw_username.split("|")
        username_part = parts[0]
        service_pk = parts[1] if len(parts) > 1 else "default"
        pop_code = parts[2] if len(parts) > 2 else None
        
        # Resolve Game Display
        if service_pk != "default":
            try:
                from bot.routers.buy import CATEGORIES
                for cat in CATEGORIES.values():
                    for s in cat["services"]:
                        if s["pk"] == service_pk:
                            service_display = s["name"]
                            break
            except Exception:
                service_display = service_pk.capitalize()
                
        # Resolve Country Flag & Name [1]
        if pop_code:
            country_display = f"{get_flag_emoji(pop_code)} {get_country_name_fa(pop_code)} ({pop_code})"
    
    status_fa = "🟢 فعال" if service.status == "active" else "🔴 منقضی شده"
    
    return f"""<b>{index}. 👤 نام دستگاه:</b> <code>{escape(username_part)}</code>
🎮 <b>برنامه/بازی:</b> {escape(service_display)}
🗺 <b>سرور (کشور):</b> {escape(country_display)}
⚡ <b>پلن:</b> {escape(service.plan.title if service.plan else "اکانت تست")}
🗓 <b>تاریخ انقضا:</b> {format_datetime(service.expire_at)}
📌 <b>وضعیت:</b> {status_fa}
"""


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
        text="🗺 تنظیمات لوکیشن سرور",
        callback_data=f"location_settings_menu:{service_id}"  # Triggers main settings menu [1]
    )
    builder.adjust(1)
    return builder.as_markup()


async def _show_my_services_page(callback_or_message: CallbackQuery | Message, page: int, session: AsyncSession) -> None:
    """Renders 3 parsed services per page dynamically with custom navigations [1]."""
    user_id = callback_or_message.from_user.id
    user = await UsersRepository(session).get_by_telegram_id(user_id)
    if not user:
        return

    services = await ServicesRepository(session).list_by_user(user.id)
    if not services:
        msg = "شما هنوز هیچ سرویس یا اشتراکی تهیه نکرده‌اید."
        if isinstance(callback_or_message, CallbackQuery):
            await callback_or_message.message.answer(msg)
        else:
            await callback_or_message.answer(msg)
        return

    # Sort services: Active first, then Expired, then by expiration date descending [1]
    services.sort(key=lambda s: (0 if s.status == "active" else 1, s.expire_at), reverse=True)

    limit = 3  # Compact pagination [1]
    start_idx = page * limit
    end_idx = start_idx + limit
    page_services = services[start_idx:end_idx]
    has_next = len(services) > end_idx

    lines = [f"🛍 <b>اشتراک‌های DNS شما | صفحه {page + 1} از {((len(services) - 1) // limit) + 1}</b>\n"]
    
    builder = InlineKeyboardBuilder()
    for idx, service in enumerate(page_services, start=start_idx + 1):
        # Format parsed text card
        lines.append(format_service_item_display(service, idx))
        
        # Dedicated management button for each service on the page [1]
        raw_name = (service.username or "دستگاه").split("|")[0].strip()
        builder.button(
            text=f"🛠 مدیریت: {raw_name}",
            callback_data=ServiceActionCallback(action="status", service_id=service.id)
        )

    builder.adjust(1)  # Stacks management buttons cleanly

    # Append navigation controls [1]
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"my_services_page:{page - 1}"))
    if has_next:
        nav_buttons.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"my_services_page:{page + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="buy_back_to_menu"))

    text_content = "\n".join(lines)
    
    if isinstance(callback_or_message, CallbackQuery):
        await callback_or_message.message.edit_text(text_content, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await callback_or_message.answer(text_content, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.message(F.text == texts.BTN_MY_SERVICES)
async def my_services(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _show_my_services_page(message, page=0, session=session)


@router.callback_query(F.data.startswith("my_services_page:"), StateFilter("*"))
async def handle_my_services_page(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    page = int(callback.data.split(":")[1])
    await _show_my_services_page(callback, page, session)


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
        text = f"""🔗 لینک‌های سرویس <code>{escape(service.username.split("|")[0])}</code>

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
# MAIN LOCATION SETTINGS GATEWAY (Overall vs App Routing)
# ============================================================================

@router.callback_query(F.data.startswith("location_settings_menu:"), StateFilter("*"))
async def handle_location_settings_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    service_id = int(callback.data.split(":")[1])
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 تغییر لوکیشن کل اینترنت (Default)", callback_data=f"change_default_loc_select:{service_id}")
    builder.button(text="🎮 تغییر لوکیشن بازی‌ها و برنامه‌ها", callback_data=f"service_routing_menu:{service_id}")
    builder.button(text="↩️ بازگشت", callback_data=ServiceActionCallback(action="status", service_id=service_id))
    builder.adjust(1)
    
    await callback.message.edit_text(
        "🗺 <b>تنظیمات لوکیشن سرور</b>\n\n"
        "یکی از دو روش زیر را برای تغییر لوکیشن سرور دی‌ان‌اس خود انتخاب کنید:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


# ============================================================================
# 1. OVERALL DEFAULT ROUTING CHANGER
# ============================================================================

async def _show_default_loc_page(callback: CallbackQuery, service_id: int, page: int, settings: Settings) -> None:
    """Renders the paginated overall default location selector [cite: 1]."""
    controld_service = ControlDService(settings)
    proxies = await controld_service.fetch_controld_proxies()
    
    if not proxies:
        await callback.message.answer("❌ خطایی در بارگذاری سرورهای معتبر رخ داد.")
        return

    # Sort countries alphabetically
    proxies.sort(key=lambda x: x["country_name"].lower())

    limit = 10
    start_idx = page * limit
    end_idx = start_idx + limit
    page_proxies = proxies[start_idx:end_idx]
    has_next = len(proxies) > end_idx

    builder = InlineKeyboardBuilder()
    for p in page_proxies:
        p_name = f"{p['flag']} {p['city_name']} ({p['code']})"
        builder.button(
            text=p_name,
            callback_data=f"apply_def_loc:{service_id}:{p['code']}:{p['country_name']} - {p['city_name']}"
        )
    
    # 1. First adjust the 10 countries into rows of 2
    builder.adjust(2)

    # 2. Append Navigation Controls [cite: 1]
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"def_loc_page:{service_id}:{page - 1}"))
    if has_next:
        nav_buttons.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"def_loc_page:{service_id}:{page + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    # 3. Append Back Button [cite: 1]
    builder.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"location_settings_menu:{service_id}"))

    await callback.message.edit_text(
        f"🗺 <b>تغییر لوکیشن کل ترافیک اینترنت</b> | صفحه {page + 1}\n\n"
        f"کشوری که می‌خواهید لوکیشن کل ترافیک اینترنت شما به آن تغییر یابد انتخاب کنید:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("change_default_loc_select:"), StateFilter("*"))
async def handle_change_default_loc_select(callback: CallbackQuery, settings: Settings) -> None:
    await callback.answer()
    service_id = int(callback.data.split(":")[1])
    await _show_default_loc_page(callback, service_id, page=0, settings=settings)


@router.callback_query(F.data.startswith("def_loc_page:"), StateFilter("*"))
async def handle_def_loc_page(callback: CallbackQuery, settings: Settings) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    service_id = int(parts[1])
    page = int(parts[2])
    await _show_default_loc_page(callback, service_id, page, settings)


@router.callback_query(F.data.startswith("apply_def_loc:"), StateFilter("*"))
async def handle_apply_def_loc(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if callback.message is None:
        return

    parts = callback.data.split(":")
    service_id = int(parts[1])
    pop_code = parts[2]
    pop_name = parts[3]

    service = await ServicesRepository(session).get(service_id)
    if service is None or not service.controld_device_id:
        await callback.message.answer("❌ سرویس یا شناسه دستگاه معتبر یافت نشد.")
        return

    await callback.message.edit_text(f"⚙️ در حال انتقال لوکیشن کل اینترنت شما به {pop_name}...")

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

    # Call overall default profile routing PUT API [cite: 1]
    success = await controld_service.update_profile_default(profile_id, pop_code)

    if success:
        await callback.message.answer(
            f"✅ لوکیشن کل ترافیک اینترنت دستگاه <code>{escape(service.username.split('|')[0])}</code> با موفقیت به سرور <b>{escape(pop_name)}</b> تغییر یافت!\n\n"
            f"تغییرات به صورت آنی روی دی‌ان‌اس اختصاصی شما اعمال شد.",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML"
        )
    else:
        await callback.message.answer("❌ خطا در ثبت لوکیشن در پنل Control D. مجدداً تلاش کنید.")


# ============================================================================
# 2. FINE-GRAINED SERVICE ROUTING CONTROLLER
# ============================================================================

@router.callback_query(F.data.startswith("service_routing_menu:"), StateFilter("*"))
async def service_routing_menu(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    """Displays Category Selector for location management."""
    await callback.answer()
    if callback.message is None:
        return
        
    service_id = int(callback.data.split(":")[1])
    service = await ServicesRepository(session).get(service_id)
    if service is None:
        await callback.message.answer("❌ سرویس پیدا نشد.")
        return

    # Fetch device profile
    profile_id = settings.controld_profile_id
    device_url = f"https://api.controld.com/devices/{service.controld_device_id}"
    headers = {
        "Authorization": f"Bearer {settings.controld_api_token}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        try:
            device_resp = await client.get(device_url, headers=headers, timeout=5.0)
            if device_resp.status_code == 200:
                profile_id = device_resp.json().get("body", {}).get("device", {}).get("profile_id")
        except Exception:
            pass

    if not profile_id:
        profile_id = settings.controld_profile_id

    # Fetch active categories dynamically
    controld = ControlDService(settings)
    services = await controld.fetch_controld_services(profile_id)
    if not services:
        await callback.message.answer("❌ خطایی در بارگذاری سرویس‌ها رخ داد.")
        return

    unique_categories = sorted(list(set(s["category"] for s in services if s.get("category"))))

    from app.services.controld import get_category_label_fa

    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 کل ترافیک اینترنت (Default)", callback_data=f"select_srv_loc:{service_id}:default")
    
    for cat_key in unique_categories:
        label = get_category_label_fa(cat_key)
        builder.button(text=label, callback_data=f"srv_manage_cat:{service_id}:{cat_key}:0")
        
    builder.button(text="↩️ بازگشت", callback_data=f"location_settings_menu:{service_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        f"🗺 <b>تنظیم لوکیشن سرویس‌ها</b> | دستگاه: <code>{escape(service.username.split('|')[0])}</code>\n\n"
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
    
    service = await ServicesRepository(session).get(service_id)
    if service is None or not service.controld_device_id:
        await callback.message.answer("❌ سرویس معتبر یافت نشد.")
        return

    # Find profile_id
    profile_id = settings.controld_profile_id
    device_url = f"https://api.controld.com/devices/{service.controld_device_id}"
    headers = {
        "Authorization": f"Bearer {settings.controld_api_token}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        try:
            device_resp = await client.get(device_url, headers=headers, timeout=5.0)
            if device_resp.status_code == 200:
                profile_id = device_resp.json().get("body", {}).get("device", {}).get("profile_id")
        except Exception:
            pass

    if not profile_id:
        profile_id = settings.controld_profile_id

    controld = ControlDService(settings)
    services = await controld.fetch_controld_services(profile_id)
    if not services:
        await callback.message.answer("❌ خطایی در بارگذاری سرویس‌ها رخ داد.")
        return
        
    filtered = [s for s in services if s["category"] == category_key]
    filtered.sort(key=lambda x: (x["name"] or "").lower())
    
    limit = 10
    start_idx = page * limit
    end_idx = start_idx + limit
    page_items = filtered[start_idx:end_idx]
    has_next = len(filtered) > end_idx

    builder = InlineKeyboardBuilder()
    for s in page_items:
        builder.button(
            text=s["name"] or s["pk"],
            callback_data=f"select_srv_loc:{service_id}:{s['pk']}"
        )
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"srv_manage_cat:{service_id}:{category_key}:{page - 1}"))
    if has_next:
        nav_buttons.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"srv_manage_cat:{service_id}:{category_key}:{page + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)
        
    builder.row(InlineKeyboardButton(text="🔙 بازگشت به دسته‌بندی‌ها", callback_data=f"service_routing_menu:{service_id}"))
    builder.adjust(2)
    
    from app.services.controld import get_category_label_fa
    category_label = get_category_label_fa(category_key)
    await callback.message.edit_text(
        f"📂 دسته‌بندی انتخاب شده: <b>{category_label}</b> | صفحه {page + 1}\n\n"
        f"🎮 لطفاً سرویس مورد نظر خود را برای انتقال ترافیک انتخاب کنید:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("select_srv_loc:"), StateFilter("*"))
async def select_service_location(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    """Fetches and displays available Control D proxies as buttons."""
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
        p_name = f"{p['flag']} {p['city_name']} ({p['code']})"
        builder.button(
            text=p_name,
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
        success = await controld_service.update_profile_default(profile_id, pop_code)  
    else:
        success = await controld_service.update_service_route(profile_id, service_pk, pop_code)  

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