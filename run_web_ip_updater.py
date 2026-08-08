# ip_server.py
import asyncio
import secrets
import logging
import re
import hmac
import hashlib
from datetime import datetime, timezone, timedelta
from html import escape
import jdatetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, Form, HTTPException, status, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import httpx
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from app.config import get_settings, SLOT_CONFIGS
from app.database import async_session_maker
from app.models import IPAuthToken, Order, Payment, VPNService, OrderStatus, PaymentStatus
from app.repositories.orders import OrdersRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.services import ServicesRepository
from app.services.controld import create_dns_device, ControlDService
from app.services.payment_service import PaymentApprovalError, PaymentAlreadyProcessedError, PaymentExpiredError, PaymentService
from app.services.vpn_panel import VPNPanelService
from app.services.paystar import PaystarService
from app.services.ip_manager import update_device_ip_safe
from bot.loader import create_bot

app = FastAPI(title="Control D Auto-IP & Payment Gateway")
settings = get_settings()
bot = create_bot(settings)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")

WEB_SERVER_BASE_URL = settings.public_web_base_url
security = httpx


def calculate_remaining_time_fa(expire_at: datetime | None) -> str:
    if not expire_at:
        return "۳۰ روز"
    now = datetime.now(timezone.utc)
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    delta = expire_at - now
    total_seconds = delta.total_seconds()
    if total_seconds <= 0:
        return "پایان یافته"
    total_hours = int(total_seconds // 3600)
    if total_hours >= 24:
        return f"{total_hours // 24} روز"
    if total_hours > 0:
        return f"{total_hours} ساعت"
    return f"{int(total_seconds // 60)} دقیقه"


def _parse_purchase_metadata(raw_username: str | None) -> tuple[str, str, str | None]:
    if not raw_username:
        return "", "default", None
    if "|" not in raw_username:
        return raw_username, "default", None

    parts = raw_username.split("|")
    username = parts[0]
    service_pk = parts[1] if len(parts) > 1 else "default"
    pop_code = parts[2] if len(parts) > 2 else None
    return username, service_pk, pop_code


async def get_controld_device_ips(device_id: str, settings_obj) -> dict:
    """Retrieves Legacy DNS resolvers, preferring local static configurations over slow APIs [cite: 1]."""
    from app.config import SLOT_CONFIGS
    for config in SLOT_CONFIGS.values():
        if config["device_id"] == device_id:
            return {
                "ipv4_primary": config["dns_primary"],
                "ipv4_secondary": config["dns_secondary"],
            }
            
    url = f"https://api.controld.com/devices/{device_id}"
    headers = {
        "Authorization": f"Bearer {settings_obj.controld_api_token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                body = data.get("body", {})
                resolver_info = body.get("resolvers") or body.get("resolver") or []
                v4_list = resolver_info.get("v4") or resolver_info.get("legacy", {}).get("ipv4") or []
                return {
                    "ipv4_primary": v4_list[0] if len(v4_list) > 0 else "76.76.2.162",
                    "ipv4_secondary": v4_list[1] if len(v4_list) > 1 else "76.76.10.162",
                }
        except Exception:
            pass
    return {
        "ipv4_primary": "76.76.2.162",
        "ipv4_secondary": "76.76.10.162",
    }


def verify_admin_web_token(uid: int, token: str) -> bool:
    admin_ids = set(settings.admin_ids)
    if settings.root_admin_telegram_id is not None:
        admin_ids.add(settings.root_admin_telegram_id)
        
    if uid not in admin_ids:
        return False
        
    correct_token = hmac.new(
        settings.bot_token.encode('utf-8'),
        str(uid).encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return secrets.compare_digest(token, correct_token)


async def _apply_purchase_route(order: Order, service: VPNService, settings_obj) -> tuple[str, str | None]:
    """No-op helper under the Static 5-Server Slot Model to prevent unneeded API requests [cite: 1]."""
    _username, service_pk, slot_num_str = _parse_purchase_metadata(order.custom_username)
    return service_pk, slot_num_str


# ip_server.py & run_web_ip_updater.py

async def _build_paystar_context(order: Order, service: VPNService, settings_obj) -> dict[str, str]:
    raw_username, service_pk, pop_code = _parse_purchase_metadata(order.custom_username)
    username = raw_username or f"user{order.user_id}"

    service_display = service_pk.capitalize() if service_pk != "default" else "🌐 کل ترافیک اینترنت"
    
    # Fast non-blocking lookups with short timeout guards
    if service.plan and service.plan.controld_profile_id and service_pk != "default":
        try:
            controld_service = ControlDService(settings_obj)
            # Ensure fetch_controld_services internally uses short timeouts (<= 3s)
            services = await asyncio.wait_for(
                controld_service.fetch_controld_services(service.plan.controld_profile_id), 
                timeout=2.0
            )
            if services:
                for item in services:
                    if item.get("pk") == service_pk and item.get("name"):
                        service_display = item["name"]
                        break
        except Exception:
            pass

    country_display = pop_code or "پیش‌فرض"

    # Fast IP lookup from local SLOT_CONFIGS
    ips = await get_controld_device_ips(service.controld_device_id, settings_obj) if service.controld_device_id else {
        "ipv4_primary": "76.76.2.162",
        "ipv4_secondary": "76.76.10.162",
    }

    expire_at = service.expire_at
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
        
    try:
        tehran_tz = ZoneInfo("Asia/Tehran")
        tehran_expire = expire_at.astimezone(tehran_tz)
        naive_tehran = tehran_expire.replace(tzinfo=None)
        # 🛠 FIX: Defined expire_str directly to resolve the UnboundLocalError [cite: 1]
        expire_str = jdatetime.datetime.fromgregorian(datetime=naive_tehran).strftime("%Y/%m/%d - %H:%M:%S")
    except Exception:
        expire_str = expire_at.astimezone(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "username": username,
        "service_display": service_display,
        "country_display": country_display,
        "duration_text": calculate_remaining_time_fa(expire_at),
        "expire_str": expire_str,  # Now guaranteed to be populated
        "device_id": service.controld_device_id or "",
        "ipv4_primary": ips["ipv4_primary"],
        "ipv4_secondary": ips["ipv4_secondary"],
        "service_pk": service_pk,
        "pop_code": pop_code or "",
    }


def _render_paystar_success_html(order: Order, payment: Payment, context: dict[str, str]) -> HTMLResponse:
    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>پرداخت موفقیت‌آمیز</title>
        <style>
            body {{ font-family: Tahoma, Arial, sans-serif; background-color: #f4f6f9; text-align: center; padding: 50px; direction: rtl; }}
            .card {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); display: inline-block; max-width: 720px; }}
            h1 {{ color: #2ecc71; }}
            p {{ color: #333; font-size: 18px; line-height: 1.9; text-align: right; }}
            code {{ background: #f4f6f9; padding: 2px 6px; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>✅ پرداخت شما با موفقیت انجام شد!</h1>
            <p>کد رهگیری سفارش: <b>{escape(order.tracking_code)}</b></p>
            <p>کد پیگیری تراکنش: <b>{escape(payment.ref_id or "-")}</b></p>
            <p>نام کاربری دستگاه: <b>{escape(context["username"])}</b></p>
            <p>برنامه/بازی: <b>{escape(context["service_display"])}</b></p>
            <p>سرور (کشور): <b>{escape(context["country_display"])}</b></p>
            <p>مدت اعتبار: <b>{escape(context["duration_text"])}</b></p>
            <p>تاریخ انقضا: <b>{escape(context["expire_str"])}</b></p>
            <p>DNS اختصاصی شما:</p>
            <p>Primary: <code>{escape(context["ipv4_primary"])}</code></p>
            <p>Secondary: <code>{escape(context["ipv4_secondary"])}</code></p>
            <p>جزئیات اتصال به تلگرام شما ارسال شد.</p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# ip_server.py & run_web_ip_updater.py

async def _send_paystar_success_message(order: Order, payment: Payment, context: dict[str, str]) -> None:
    """Sends the checkout completion notification with secure direct update-ip buttons [cite: 1]."""
    from bot.routers.services import create_secure_ip_update_keyboard
    
    async with async_session_maker() as session:
        # 🛠 FIX: Directly query VPNService to avoid SQLAlchemy DetachedInstanceError [cite: 1.2.2]
        stmt = select(VPNService).where(VPNService.order_id == order.id).limit(1)
        res = await session.execute(stmt)
        vpn_service = res.scalars().first()

        if vpn_service is None:
            logger.error("send_paystar_success_message_failed_missing_service", order_id=order.id)
            return

        # Safely generate the inline keyboard
        markup = await create_secure_ip_update_keyboard(session, vpn_service.id)

    # ip_server.py & run_web_ip_updater.py
# (Inside _send_paystar_success_message)

    success_telegram_text = f"""✅ <b>پرداخت آنلاین شما تایید و اشتراک فعال شد!</b>

🔹 تاریخ انقضاء پلن : <code>{escape(context["expire_str"])}</code>
🔷 زمان باقی‌مانده: {escape(context["duration_text"])}
🎮 برنامه/بازی: <b>{escape(context["service_display"])}</b>
🗺 سرور (کشور) فعلی: <b>{escape(context["country_display"])}</b>

دی ان اس اختصاصی شما :
🔷 Primary : <code>{escape(context["ipv4_primary"])}</code>
🔷 Secondary : <code>{escape(context["ipv4_secondary"])}</code>

📱 دی ان اس مخصوص موبایل :
🔷 <code>76.76.2.22</code>


مراحل ثبت آی‌پی (بسیار مهم):
1️⃣ : دستگاه خود (موبایل یا لپ‌تاپ) را به همان مودم/روتری وصل کنید که کنسول یا سیستم بازی شما به آن متصل است.
2️⃣ : فیلترشکن خود را خاموش کرده و روی دکمه «ثبت آی‌پی اتوماتیک» زیر کلیک کنید تا آی‌پی مودم شما ثبت شود.
❌ در صورت عدم ثبت آی‌پی روی مودم/روتر مشترک، دی‌ان‌اس‌ها متصل نخواهند شد ❌

⚠️ در صورت عدم اتصال دی‌ان‌اس‌ها، لطفاً وضعیت اتصال اینترنت خود را شخصاً بررسی کنید.

📌 برای تغییر لوکیشن بازی به لوکیشن کشور دلخواه خود: به بخش «اشتراک‌های من» بروید، روی «مدیریت» کلیک کنید و لوکیشن دلخواه را تنظیم کنید."""

    try:
        await bot.send_message(
            chat_id=order.user.telegram_id,
            text=success_telegram_text,
            reply_markup=markup,
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("paystar_notification_failed", order_id=order.id, payment_id=payment.id, error=str(exc))

# ============================================================================
# AUTO-REGISTRATION ENDPOINT & HELPERS
# ============================================================================

_bot_username = None

async def get_bot_username() -> str:
    """Helper to retrieve and cache the active Telegram bot username."""
    global _bot_username
    if _bot_username is None:
        try:
            me = await bot.get_me()
            _bot_username = me.username
        except Exception as exc:
            logger.warning("failed_to_retrieve_bot_info", error=str(exc))
            _bot_username = "bot"
    return _bot_username


def _render_capture_ip_html(
    title: str,
    heading: str,
    message: str,
    is_success: bool = False,
    client_ip: str | None = None,
    bot_username: str = "bot"
) -> HTMLResponse:
    """Renders a responsive dark-themed Persian template utilizing Bootstrap 5 [cite: 1]."""
    icon_class = "success-icon" if is_success else "error-icon"
    icon = "✅" if is_success else "❌"
    
    ip_box = ""
    if client_ip:
        ip_box = f"""
        <div class="info-ip py-2 px-3 rounded-3 mb-4 text-center">
            {escape(client_ip)}
        </div>
        """
        
    html_content = f"""
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{escape(title)}</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css" rel="stylesheet">
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;700&display=swap');
            body {{
                font-family: 'Vazirmatn', Tahoma, sans-serif;
                background-color: #0f172a;
                color: #f8fafc;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            .theme-card {{
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 16px;
                padding: 40px;
                box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3), 0 8px 10px -6px rgba(0, 0, 0, 0.3);
                max-width: 550px;
                width: 100%;
                text-align: center;
            }}
            .icon-wrapper {{
                width: 80px;
                height: 80px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 24px;
                font-size: 40px;
            }}
            .success-icon {{
                background-color: rgba(16, 185, 129, 0.1);
                color: #10b981;
                border: 2px solid rgba(16, 185, 129, 0.2);
            }}
            .error-icon {{
                background-color: rgba(239, 68, 68, 0.1);
                color: #ef4444;
                border: 2px solid rgba(239, 68, 68, 0.2);
            }}
            .info-ip {{
                background-color: #0f172a;
                border: 1px solid #334155;
                font-family: monospace;
                font-size: 1.25rem;
                letter-spacing: 1px;
                color: #38bdf8;
            }}
            .btn-home {{
                background-color: #3b82f6;
                color: #ffffff;
                border: none;
                font-weight: bold;
                transition: all 0.2s ease-in-out;
            }}
            .btn-home:hover {{
                background-color: #2563eb;
                transform: translateY(-1px);
                color: #ffffff;
            }}
        </style>
    </head>
    <body>
        <div class="theme-card">
            <div class="icon-wrapper {icon_class}">
                {icon}
            </div>
            <h1 class="h4 mb-3 fw-bold">{escape(heading)}</h1>
            <p class="mb-4 text-secondary" style="font-size: 15px; line-height: 1.8;">{escape(message)}</p>
            {ip_box}
            <a href="https://t.me/{escape(bot_username)}" class="btn btn-home py-2 px-4 rounded-3 text-decoration-none d-inline-block">بازگشت به ربات تلگرام</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get("/capture-ip/{token}", response_class=HTMLResponse)
async def capture_ip(request: Request, token: str):
    """Processes dynamic client IP collection from an expiring token securely [cite: 1]."""
    bot_user = await get_bot_username()

    # 1. Path sanitization to match both standard and compact hex UUID tokens
    token = token.strip()
    if not re.match(r"^[a-fA-F0-9-]{32,36}$", token):
        return _render_capture_ip_html(
            title="خطا در ثبت آی‌پی",
            heading="لینک وارد شده نامعتبر است",
            message="ساختار توکن امنیتی این لینک نامعتبر است. لطفاً از طریق ربات مجدداً تلاش کنید.",
            is_success=False,
            bot_username=bot_user
        )

    async with async_session_maker() as session:
        # 2. Database validation query
        stmt = (
            select(IPAuthToken)
            .options(joinedload(IPAuthToken.service).joinedload(VPNService.user))
            .where(IPAuthToken.token == token)
            .limit(1)
        )
        res = await session.execute(stmt)
        token_record = res.scalars().first()

        if not token_record:
            return _render_capture_ip_html(
                title="خطا در ثبت آی‌پی",
                heading="لینک وارد شده نامعتبر است",
                message="توکن مورد نظر یافت نشد. ممکن است این لینک قدیمی یا نامعتبر باشد.",
                is_success=False,
                bot_username=bot_user
            )

        if token_record.is_used:
            return _render_capture_ip_html(
                title="خطا در ثبت آی‌پی",
                heading="لینک استفاده شده است",
                message="این لینک یک‌بار مصرف پیش از این مورد استفاده قرار گرفته است. لطفاً لینک جدیدی از دکمه‌های ربات تلگرام دریافت کنید.",
                is_success=False,
                bot_username=bot_user
            )

        # 3. Expiration checks
        now = datetime.now(timezone.utc)
        expires_at = token_record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
            
        if now > expires_at:
            return _render_capture_ip_html(
                title="خطا در ثبت آی‌پی",
                heading="انقضای لینک امنیتی",
                message="مهلت استفاده از این لینک (۱۰ دقیقه) به پایان رسیده است. لطفاً یک لینک تازه از منوی ربات تلگرام دریافت کنید.",
                is_success=False,
                bot_username=bot_user
            )

        service = token_record.service
        if not service:
            return _render_capture_ip_html(
                title="خطا در ثبت آی‌پی",
                heading="سرویس یافت نشد",
                message="اشتراک متناظر با این لینک امنیتی در سیستم یافت نشد یا حذف گردیده است.",
                is_success=False,
                bot_username=bot_user
            )

        # 4. Resolve the client public IP cleanly
        client_ip = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or request.client.host
        if client_ip and "," in client_ip:
            client_ip = client_ip.split(",")[0].strip()

        if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", client_ip):
            return _render_capture_ip_html(
                title="خطا در ثبت آی‌پی",
                heading="آی‌پی نامعتبر است",
                message="آی‌پی ارسالی توسط مرورگر شما یک آدرس IPv4 استاندارد عمومی نیست. لطفاً اتصال اینترنت خود را بررسی کنید.",
                is_success=False,
                bot_username=bot_user
            )

        # 5. Remote and Local registration sync
        success = await update_device_ip_safe(session, service, client_ip)
        
        if success:
            token_record.is_used = True
            await session.commit()
            
            return _render_capture_ip_html(
                title="ثبت آی‌پی موفقیت‌آمیز",
                heading="✅ ثبت آی‌پی با موفقیت انجام شد!",
                message="آی‌پی فعلی دستگاه شما با موفقیت در پروفایل امنیتی ثبت شد. اکنون می‌توانید بدون نیاز به فیلترشکن از دی‌ان‌اس اختصاصی خود روی این دستگاه استفاده کنید.",
                is_success=True,
                client_ip=client_ip,
                bot_username=bot_user
            )
        else:
            return _render_capture_ip_html(
                title="خطا در ثبت آی‌پی",
                heading="خطای ارتباطی با سرور",
                message="ارتباط با پنل ابری دی‌ان‌اس برقرار نشد. لطفاً چند لحظه بعد مجدداً تلاش کنید.",
                is_success=False,
                bot_username=bot_user
            )


# ip_server.py & run_web_ip_updater.py

@app.get("/update-ip/{device_id}", response_class=HTMLResponse)
async def update_device_ip(request: Request, device_id: str):
    """Detects, registers, and synchronizes the client public IP to the local database [cite: 1]."""
    # Detect the client's real public IP address (handling proxies safely)
    client_ip = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or request.client.host
    if client_ip and "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()

    token = settings.controld_api_token
    if not token:
        return "<h3>خطا: توکن API در تنظیمات یافت نشد.</h3>"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "accept": "application/json"
    }

    # Add support for Sub-Organizations in the web route
    import os
    org_id = getattr(settings, "controld_org_id", None) or os.getenv("CONTROLD_ORG_ID")
    if org_id:
        headers["X-Force-Org-Id"] = org_id

    async with httpx.AsyncClient() as client:
        device_url = f"https://api.controld.com/devices/{device_id}"
        profile_id = None
        try:
            device_resp = await client.get(device_url, headers=headers, timeout=5.0)
            if device_resp.status_code == 200:
                profile_id = device_resp.json().get("body", {}).get("device", {}).get("profile_id")
        except Exception:
            pass

        if not profile_id:
            profile_id = settings.controld_profile_id

        if not profile_id:
            return "<h3>خطا: شناسه پروفایل برای این دستگاه یافت نشد.</h3>"

        access_url = "https://api.controld.com/access"
        payload = {
            "ips": [client_ip],
            "ips[]": [client_ip],
            "name": "Auto Registered"
        }

        try:
            response = await client.post(f"{access_url}?device_id={device_id}", json=payload, headers=headers, timeout=10.0)
            if response.status_code in (200, 201):
                
                # ============================================================
                # DATABASE SYNCHRONIZATION HOOK
                # ============================================================
                try:
                    async with async_session_maker() as db_session:
                        # Find the active subscription matching this ControlD device_id
                        stmt = select(VPNService).where(VPNService.controld_device_id == device_id).limit(1)
                        res = await db_session.execute(stmt)
                        service = res.scalars().first()
                        if service:
                            service.authorized_ip = client_ip
                            await db_session.commit()
                            logger.info("web_updater_synced_registered_ip_to_db", device_id=device_id, ip=client_ip)
                except Exception as db_exc:
                    logger.error("web_updater_failed_to_sync_registered_ip_to_db", device_id=device_id, error=str(db_exc))
                # ============================================================

                return f"""
                <html>
                <head>
                    <meta charset="utf-8">
                    <title>ثبت آی‌پی موفقیت‌آمیز</title>
                    <style>
                        body {{ font-family: Tahoma, Arial, sans-serif; background-color: #f4f6f9; text-align: center; padding: 50px; direction: rtl; }}
                        .card {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); display: inline-block; }}
                        h1 {{ color: #2ecc71; }}
                        p {{ color: #333; font-size: 18px; }}
                    </style>
                </head>
                <body>
                    <div class="card">
                        <h1>✅ ثبت آی‌پی با موفقیت انجام شد!</h1>
                        <p>آی‌پی شناسایی‌شده شما: <b>{escape(client_ip)}</b></p>
                        <p>اکنون می‌توانید بدون نیاز به فیلترشکن از دی‌ان‌اس اختصاصی خود روی دستگاه خود استفاده کنید.</p>
                    </div>
                </body>
                </html>
                """
            else:
                return f"<h3>خطا در ثبت آی‌پی در پنل کنترل دی: {response.text}</h3>"
        except Exception as e:
            return f"<h3>خطا در برقراری ارتباط با سرور: {str(e)}</h3>"

# ============================================================================
# WEB ADMIN DASHBOARD
# ============================================================================

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, uid: int = Query(...), token: str = Query(...)):
    if not verify_admin_web_token(uid, token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="دسترسی غیرمجاز است. لطفا از طریق دکمه مدیریت ربات تلگرام وارد شوید."
        )

    async with async_session_maker() as session:
        repo = ServicesRepository(session)
        raw_rows = await repo.get_admin_dashboard_data()

        dashboard_data = []
        for row in raw_rows:
            expire_at = row.expire_at
            shamsi_expire = "-"
            if expire_at:
                if expire_at.tzinfo is None:
                    expire_at = expire_at.replace(tzinfo=timezone.utc)
                tehran_tz = ZoneInfo("Asia/Tehran")
                tehran_expire = expire_at.astimezone(tehran_tz)
                try:
                    naive_tehran = tehran_expire.replace(tzinfo=None)
                    shamsi_expire = jdatetime.datetime.fromgregorian(datetime=naive_tehran).strftime("%Y/%m/%d - %H:%M")
                except Exception:
                    shamsi_expire = tehran_expire.strftime("%Y-%m-%d %H:%M")

            slot_name = "ثبت نشده / Unmapped"
            for _num, config in SLOT_CONFIGS.items():
                if config["device_id"] == row.controld_device_id:
                    slot_name = config["name"]
                    break

            dashboard_data.append({
                "telegram_id": row.telegram_id,
                "telegram_username": row.telegram_username or "-",
                "first_name": row.first_name or "-",
                "service_id": row.service_id,
                "controld_device_id": row.controld_device_id,
                "authorized_ip": row.authorized_ip or "ثبت نشده (No IP)",
                "expire_at_shamsi": shamsi_expire,
                "status": "فعال" if row.status == "active" else "منقضی شده",
                "slot_name": slot_name
            })

    return templates.TemplateResponse(
        "admin.html", 
        {
            "request": request, 
            "users": dashboard_data, 
            "uid": uid, 
            "token": token
        }
    )


@app.post("/admin/delete-ip")
async def admin_delete_ip(
    uid: int = Query(...),
    token: str = Query(...),
    service_id: int = Form(...),
    device_id: str = Form(...),
    ip: str = Form(...),
):
    if not verify_admin_web_token(uid, token):
         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="دسترسی غیرمجاز.")

    if not ip or ip == "ثبت نشده (No IP)":
        return RedirectResponse(url=f"/admin?uid={uid}&token={token}", status_code=status.HTTP_303_SEE_OTHER)

    controld = ControlDService(settings)
    await controld.deauthorize_ip(device_id, ip)

    async with async_session_maker() as session:
        stmt = select(VPNService).where(VPNService.id == service_id).limit(1)
        res = await session.execute(stmt)
        service = res.scalars().first()
        if service:
            service.authorized_ip = None
            await session.commit()

    logger.info("admin_force_cleared_user_ip", service_id=service_id, cleared_ip=ip)
    return RedirectResponse(url=f"/admin?uid={uid}&token={token}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/add-ip")
async def admin_add_ip(
    uid: int = Query(...),
    token: str = Query(...),
    service_id: int = Form(...),
    device_id: str = Form(...),
    new_ip: str = Form(...),
):
    if not verify_admin_web_token(uid, token):
         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="دسترسی غیرمجاز.")

    new_ip = new_ip.strip()
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", new_ip):
        raise HTTPException(status_code=400, detail="فرمت آی‌پی عددی وارد شده معتبر نیست.")

    async with async_session_maker() as session:
        stmt = select(VPNService).where(VPNService.id == service_id).limit(1)
        res = await session.execute(stmt)
        service = res.scalars().first()
        
        if not service:
            raise HTTPException(status_code=404, detail="سرویس مورد نظر یافت نشد.")

        success = await update_device_ip_safe(session, service, new_ip)
        if not success:
            raise HTTPException(status_code=500, detail="خطا در ثبت آی‌پی در پنل Control D.")

    logger.info("admin_manually_overrode_user_ip", service_id=service_id, new_ip=new_ip)
    return RedirectResponse(url=f"/admin?uid={uid}&token={token}", status_code=status.HTTP_303_SEE_OTHER)


# ============================================================================
# PAYSTAR REDIRECT PROXY
# ============================================================================
# ip_server.py & run_web_ip_updater.py

@app.get("/paystar/redirect", response_class=HTMLResponse)
async def paystar_redirect(token: str):
    """Gracefully handles redirects using a highly-styled dark card and a robust manual submit fallback [cite: 1]."""
    try:
        async with async_session_maker() as session:
            payment = await PaymentsRepository(session).get_by_token_with_details(token)
            if payment is None or payment.order is None or payment.user is None:
                return _failed_html("توکن پرداخت معتبر نیست.")
            if payment.method != "paystar":
                return _failed_html("این لینک برای پرداخت آنلاین ثبت نشده است.")
            if payment.status == PaymentStatus.APPROVED.value or payment.order.status == OrderStatus.COMPLETED.value:
                return _success_html("این سفارش قبلاً با موفقیت نهایی شده است.")
    except Exception as exc:
        logger.exception("failed_to_process_paystar_redirect_route", token=token)
        return _failed_html(f"خطای داخلی سرور در انتقال به درگاه پرداخت: {str(exc)}")

    # Proceed to submit form to core.paystar.click with custom style and manual action fallback [cite: 1]
    html_content = f"""
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>در حال انتقال به درگاه پرداخت...</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css" rel="stylesheet">
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;700&display=swap');
            body {{
                font-family: 'Vazirmatn', Tahoma, sans-serif;
                background-color: #0f172a;
                color: #f8fafc;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            .redirect-card {{
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 16px;
                padding: 40px;
                box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
                max-width: 500px;
                width: 100%;
                text-align: center;
            }}
            .spinner-border {{
                color: #38bdf8;
                width: 3rem;
                height: 3rem;
            }}
            .btn-submit {{
                background-color: #10b981;
                color: #ffffff;
                border: none;
                font-weight: bold;
                transition: all 0.2s ease-in-out;
            }}
            .btn-submit:hover {{
                background-color: #059669;
                transform: translateY(-1px);
                color: #ffffff;
            }}
        </style>
        <script>
            window.onload = function() {{
                document.getElementById('paystar_form').submit();
            }};
        </script>
    </head>
    <body>
        <div class="redirect-card">
            <div class="spinner-border mb-4" role="status"></div>
            <h1 class="h4 mb-3 fw-bold">در حال انتقال به درگاه پرداخت شاپرک...</h1>
            <p class="mb-4 text-secondary" style="font-size: 15px; line-height: 1.8;">
                لطفاً شکیبا باشید. در صورتی که مرورگر شما به طور خودکار منتقل نشد، روی دکمه زیر کلیک کنید:
            </p>
            <!-- 🛠 FORCED ACTION URL TO CORE.PAYSTAR.CLICK FOR COMPLETE FIREWALL BYPASS -->
            <form id="paystar_form" action="https://core.paystar.click/api/pardakht/payment" method="POST">
                <input type="hidden" name="token" value="{token}" />
                <button type="submit" class="btn btn-submit py-2 px-4 rounded-3 text-decoration-none">انتقال دستی به درگاه بانکی</button>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# ============================================================================
# PAYSTAR GATEWAY CALLBACK
# ============================================================================
# ip_server.py & run_web_ip_updater.py

@app.api_route("/paystar/callback", methods=["GET", "POST"], response_class=HTMLResponse)
async def paystar_callback(request: Request):
    """Bypasses 500 errors gracefully, logs callbacks, and confirms transactions [cite: 1]."""
    try:
        if request.method.upper() == "POST":
            payload = await request.form()
        else:
            payload = request.query_params

        try:
            status = int(payload.get("status", 0))
        except (TypeError, ValueError):
            status = 0

        order_id = str(payload.get("order_id", "")).strip()
        ref_num = str(payload.get("ref_num", "")).strip()
        card_number = str(payload.get("card_number", "")).strip()
        tracking_code = str(payload.get("tracking_code", "")).strip()

        if not order_id or not ref_num:
            return _failed_html("اطلاعات برگشتی درگاه ناقص است.")

        async with async_session_maker() as session:
            order = await OrdersRepository(session).get_by_tracking_code_with_details(order_id)
            payment = order.payment if order else None

            if order is None or payment is None or order.user is None or order.plan is None:
                return _failed_html("سفارش مرتبط با این تراکنش پیدا نشد.")

            if payment.status == PaymentStatus.APPROVED.value and order.status == OrderStatus.COMPLETED.value:
                service_stmt = select(VPNService).options(joinedload(VPNService.plan)).where(VPNService.order_id == order.id).limit(1)
                service_res = await session.execute(service_stmt)
                service = service_res.scalars().first()
                if service is None:
                    return _success_html("پرداخت این سفارش قبلاً ثبت شده است.")
                context = await _build_paystar_context(order, service, settings)
                return _render_paystar_success_html(order, payment, context)

            if status != 1:
                return _failed_html("پرداخت توسط کاربر لغو شد یا درگاه آن را ناموفق ثبت کرد.")

            paystar = PaystarService()
            try:
                is_verified = await paystar.verify_payment(
                    amount_toman=order.amount,
                    ref_num=ref_num,
                    card_number=card_number,
                    tracking_code=tracking_code,
                )
            except Exception as exc:
                logger.exception("paystar_verify_failed", order_id=order_id, error=str(exc))
                return _failed_html("خطا در ارتباط با سرویس تایید درگاه پرداخت.")

            if not is_verified:
                return _failed_html("خطا در تایید اصالت تراکنش درگاه بانکی.")

            payment.method = "paystar"
            payment.ref_id = ref_num
            payment.authority = tracking_code or payment.authority

            payment_service = PaymentService(session, VPNPanelService(), settings)
            try:
                await payment_service.approve_payment(payment.id)
            except PaymentAlreadyProcessedError:
                service_stmt = select(VPNService).options(joinedload(VPNService.plan)).where(VPNService.order_id == order.id).limit(1)
                service_res = await session.execute(service_stmt)
                service = service_res.scalars().first()
                if service is None:
                    return _success_html("پرداخت قبلاً ثبت شده است.")
                context = await _build_paystar_context(order, service, settings)
                return _render_paystar_success_html(order, payment, context)
            except PaymentExpiredError:
                return _failed_html("این سفارش منقضی شده است.")
            except PaymentApprovalError as exc:
                logger.exception("paystar_approval_failed", order_id=order_id, error=str(exc))
                return _failed_html("پرداخت تایید شد اما در ساخت سرویس خطا رخ داد.")

            service_stmt = select(VPNService).options(joinedload(VPNService.plan)).where(VPNService.order_id == order.id).limit(1)
            service_res = await session.execute(service_stmt)
            service = service_res.scalars().first()
            if service is None:
                return _failed_html("سرویس پس از پرداخت پیدا نشد.")

            try:
                await _apply_purchase_route(order, service, settings)
            except Exception as exc:
                logger.warning("paystar_route_update_failed", order_id=order_id, error=str(exc))

            context = await _build_paystar_context(order, service, settings)
            try:
                await _send_paystar_success_message(order, payment, context)
            except Exception:
                pass

            return _render_paystar_success_html(order, payment, context)
    except Exception as global_exc:
        logger.exception("global_unhandled_callback_exception")
        return _failed_html(f"خطای داخلی سرور در تایید نهایی پرداخت: {str(global_exc)}")

def _failed_html(reason: str) -> HTMLResponse:
    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>خطا در پرداخت</title>
        <style>
            body {{ font-family: Tahoma, Arial, sans-serif; background-color: #f4f6f9; text-align: center; padding: 50px; direction: rtl; }}
            .card {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); display: inline-block; }}
            h1 {{ color: #e74c3c; }}
            p {{ color: #333; font-size: 18px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>❌ تراکنش ناموفق بود</h1>
            <p>{escape(reason)}</p>
            <p>مبلغ کسر شده (در صورت کسر وجه) طی ۷۲ ساعت به حساب شما بازخواهد گشت. لطفاً مجدداً در ربات تلاش کنید.</p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)


def _success_html(message: str) -> HTMLResponse:
    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>اقدام با موفقیت</title>
        <style>
            body {{ font-family: Tahoma, Arial, sans-serif; background-color: #f4f6f9; text-align: center; padding: 50px; direction: rtl; }}
            .card {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); display: inline-block; }}
            h1 {{ color: #2ecc71; }}
            p {{ color: #333; font-size: 18px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>✅ {escape(message)}</h1>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)