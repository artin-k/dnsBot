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
from bot.utils.auto_clean import schedule_message_deletion
from app.services.vpn_detector import verify_user_ip


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


# In ip_server.py:
from bot.utils.messages import send_dns_delivery_card

async def _send_paystar_success_message(order: Order, payment: Payment, context: dict[str, str]) -> None:
    """Sends checkout completion notification using the unified message delivery builder."""
    async with async_session_maker() as session:
        stmt = select(VPNService).where(VPNService.order_id == order.id).limit(1)
        res = await session.execute(stmt)
        vpn_service = res.scalars().first()
        if not vpn_service:
            return

        await send_dns_delivery_card(
            bot=bot,
            chat_id=order.user.telegram_id,
            session=session,
            service=vpn_service,
            title_prefix="✅ <b>پرداخت آنلاین تایید و اشتراک فعال شد!</b>",
            ipv4_primary=context.get("ipv4_primary", "76.76.2.162"),
            ipv4_secondary=context.get("ipv4_secondary", "76.76.10.162"),
            service_display=context.get("service_display", "کل ترافیک اینترنت (Default)"),
            country_display=context.get("country_display", "پیش‌فرض"),
            delay_seconds=7200,
        )
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

def _render_vpn_detected_html(
    detected_ip: str,
    country: str,
    isp: str,
    error_message: str | None = None,
    bot_username: str = "bot"
) -> HTMLResponse:
    """Renders the warning modal when a foreign IP or VPN is detected."""
    custom_msg = error_message or "آی‌پی شناسایی‌شده شما متعلق به سرور خارجی یا فیلترشکن است."
    html_content = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>فیلترشکن شما روشن است</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css" rel="stylesheet">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;700&display=swap');
        body {{ font-family: 'Vazirmatn', Tahoma, sans-serif; background-color: #0f172a; color: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }}
        .card-box {{ background-color: #1e293b; border: 2px solid #eab308; border-radius: 16px; padding: 36px; box-shadow: 0 10px 25px -5px rgba(234, 179, 8, 0.25); max-width: 540px; width: 100%; text-align: center; }}
        .icon-box {{ width: 75px; height: 75px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 20px; font-size: 38px; background-color: rgba(234, 179, 8, 0.12); border: 2px solid rgba(234, 179, 8, 0.3); }}
        .ip-badge {{ background-color: #0f172a; border: 1px solid #334155; font-family: monospace; font-size: 1.15rem; color: #f59e0b; }}
        .btn-reload {{ background-color: #eab308; color: #0f172a; font-weight: bold; border: none; transition: all 0.2s; }}
        .btn-reload:hover {{ background-color: #ca8a04; color: #0f172a; }}
    </style>
</head>
<body>
    <div class="card-box">
        <div class="icon-box">⚠️</div>
        <h1 class="h4 mb-3 fw-bold text-warning">فیلترشکن شما روشن است!</h1>
        <p class="text-light mb-3" style="font-size: 15px; line-height: 1.8;">{escape(custom_msg)}<br>برای فعال‌سازی DNS، ثبت آی‌پی <b>فقط با اینترنت مستقیم ایران</b> امکان‌پذیر است.</p>
        <div class="alert alert-dark text-start small mb-4 py-2 border-secondary" style="font-size: 13px;">
            1️⃣ فیلترشکن و پروکسی تلگرام خود را کاملاً خاموش کنید.<br>
            2️⃣ مطمئن شوید به اینترنت اصلی/وای‌فای خود متصل هستید.<br>
            3️⃣ دکمه زیر را لمس کنید:
        </div>
        <button onclick="location.reload()" class="btn btn-reload py-2 px-4 rounded-3 w-100 mb-2">🔄 فیلترشکن را خاموش کردم، بررسی مجدد</button>
        <a href="https://t.me/{escape(bot_username)}" class="btn btn-outline-secondary py-2 px-4 rounded-3 w-100 text-decoration-none">بازگشت به ربات تلگرام</a>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)


@app.get("/capture-ip/{token}", response_class=HTMLResponse)
async def capture_ip(request: Request, token: str):
    bot_user = await get_bot_username()
    token = token.strip()
    if not re.match(r"^[a-fA-F0-9-]{32,36}$", token):
        return _render_capture_ip_html("خطا در ثبت آی‌پی", "لینک نامعتبر است", "ساختار توکن امنیتی نامعتبر است.", False, bot_username=bot_user)

    client_ip, cdn_country = get_client_real_ip(request)

    # 🛡️ ANTI-VPN CHECK
    ip_check = await verify_user_ip(client_ip)
    if not ip_check.is_iran:
        return _render_vpn_detected_html(
            detected_ip=client_ip,
            country=ip_check.country,
            isp=ip_check.isp,
            error_message=ip_check.error_message,
            bot_username=bot_user,
        )

    async with async_session_maker() as session:
        stmt = select(IPAuthToken).options(joinedload(IPAuthToken.service).joinedload(VPNService.user)).where(IPAuthToken.token == token).limit(1)
        res = await session.execute(stmt)
        token_record = res.scalars().first()

        if not token_record or token_record.is_used:
            return _render_capture_ip_html("خطا در ثبت آی‌پی", "لینک نامعتبر یا استفاده‌شده", "این توکن قبلاً استفاده شده یا معتبر نیست.", False, bot_username=bot_user)

        now = datetime.now(timezone.utc)
        expires_at = token_record.expires_at.replace(tzinfo=timezone.utc) if token_record.expires_at.tzinfo is None else token_record.expires_at
        if now > expires_at:
            return _render_capture_ip_html("خطا در ثبت آی‌پی", "انقضای لینک", "مهلت استفاده از این لینک گذشته است.", False, bot_username=bot_user)

        service = token_record.service
        if not service:
            return _render_capture_ip_html("خطا در ثبت آی‌پی", "سرویس یافت نشد", "سرویس مورد نظر یافت نشد.", False, bot_username=bot_user)

        # update_device_ip_safe commits the service IP. Marking the token first
        # makes token consumption and the IP update a single DB commit.
        token_record.is_used = True
        success = await update_device_ip_safe(session, service, client_ip)
        if success:
            return _render_capture_ip_html("ثبت آی‌پی موفقیت‌آمیز", "✅ ثبت آی‌پی با موفقیت انجام شد!", f"آی‌پی ایران ({client_ip}) با موفقیت ثبت شد.", True, client_ip, bot_user)
        return _render_capture_ip_html("خطا در ثبت آی‌پی", "خطای سرور", "خطا در ثبت در سرور دی‌ان‌اس.", False, bot_username=bot_user)

@app.get("/update-ip/{device_id}", response_class=HTMLResponse, status_code=status.HTTP_410_GONE)
async def retired_update_device_ip(device_id: str):
    """Reject unsafe pre-token links; never select a service by shared slot."""
    bot_user = await get_bot_username()
    return _render_capture_ip_html(
        "این لینک منقضی شده است",
        "⚠️ لینک قدیمی است",
        "لطفاً به ربات بازگردید و از لینک امن ثبت IP استفاده کنید.",
        False,
        bot_username=bot_user,
    )
        
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

    # 1. Remove from Control D
    controld = ControlDService(settings)
    await controld.deauthorize_ip(device_id, ip)

    # 2. Remove from AdGuard Home
    from app.services.adguard import AdGuardHomeService
    adguard = AdGuardHomeService(settings)
    if adguard.is_configured():
        await adguard.deauthorize_client_ip(ip)

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
            raise HTTPException(status_code=500, detail="خطا در ثبت آی‌پی در پنل")

    logger.info("admin_manually_overrode_user_ip", service_id=service_id, new_ip=new_ip)
    return RedirectResponse(url=f"/admin?uid={uid}&token={token}", status_code=status.HTTP_303_SEE_OTHER)


# ============================================================================
# PAYSTAR REDIRECT PROXY
# ============================================================================
@app.get("/paystar/redirect", response_class=HTMLResponse)
async def paystar_redirect(token: str):
    bot_user = await get_bot_username()
    try:
        clean_token = token.strip()
        async with async_session_maker() as session:
            payment = await PaymentsRepository(session).get_by_token_with_details(clean_token)
            if payment is None or payment.order is None or payment.user is None:
                return _failed_html("توکن پرداخت معتبر نیست یا منقضی شده است.", bot_username=bot_user)
            if payment.method != "paystar":
                return _failed_html("این لینک برای پرداخت آنلاین پی‌استار ثبت نشده است.", bot_username=bot_user)
            if payment.status == PaymentStatus.APPROVED.value or payment.order.status == OrderStatus.COMPLETED.value:
                return _success_html("این سفارش قبلاً با موفقیت پرداخت و نهایی شده است.", bot_username=bot_user)

        # Build the HTML form to auto-submit a POST request to Paystar
        html_content = f"""
        <!DOCTYPE html>
        <html lang="fa" dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <meta name="referrer" content="origin-when-cross-origin" />
            <title>در حال انتقال...</title>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;700&display=swap');
                body {{
                    font-family: 'Vazirmatn', Tahoma, sans-serif;
                    background-color: #0f172a;
                    color: #f8fafc;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                }}
                .btn-fallback {{
                    background-color: #3b82f6;
                    color: #ffffff;
                    border: none;
                    border-radius: 8px;
                    padding: 12px 24px;
                    font-weight: bold;
                    cursor: pointer;
                    margin-top: 20px;
                    font-family: inherit;
                }}
            </style>
        </head>
        <body>
            <div style="text-align: center;">
                <h3 style="margin-bottom: 10px;">در حال انتقال به درگاه بانکی...</h3>
                <p style="color: #94a3b8; font-size: 14px;">لطفاً چند لحظه صبر کنید.</p>
                
                <form id="paymentForm" action="https://core.paystar.click/api/pardakht/payment" method="POST">
                    <input type="hidden" name="token" value="{clean_token}">
                    <noscript>
                        <p style="color: #ef4444; margin-top: 20px;">جاوااسکریپت در مرورگر شما غیرفعال است.</p>
                    </noscript>
                    <button type="submit" id="fallbackBtn" class="btn-fallback" style="display: none;">
                        انتقال دستی به درگاه پرداخت
                    </button>
                </form>
            </div>

            <script>
                document.addEventListener("DOMContentLoaded", function() {{
                    // Auto-submit the form as soon as the DOM is ready
                    document.getElementById("paymentForm").submit();
                    
                    // Show fallback button if the redirect fails or is blocked
                    setTimeout(function() {{
                        document.getElementById("fallbackBtn").style.display = "inline-block";
                    }}, 2500);
                }});
            </script>
        </body>
        </html>
        """
        
        return HTMLResponse(content=html_content)

    except Exception as exc:
        logger.exception("failed_to_process_paystar_redirect_route", token=token)
        return _failed_html(f"خطای داخلی در اتصال به درگاه بانکی: {str(exc)}", bot_username=bot_user)

# ============================================================================
# PAYSTAR GATEWAY CALLBACK & RESULT PAGES
# ============================================================================

PAYSTAR_STATUS_MESSAGES = {
    -1: "درخواست نامعتبر است (خطای داخلی یا ساختار ناقص داده‌ها).",
    -2: "درگاه پرداخت فعال نیست یا اطلاعات احراز هویت (Gateway ID / Sign Key) نامعتبر است.",
    -3: "آدرس آی‌پی سرور برای این درگاه پرداخت در پنل پی‌استار تعریف/مجاز نشده است.",
    -4: "مبلغ ارسالی به درگاه نامعتبر است.",
    -5: "تراکنش تکراری است یا قبلاً پردازش شده است.",
    -6: "تراکنش در سیستم پی‌استار پیدا نشد.",
    -7: "مهلت پرداخت به پایان رسیده و فاکتور منقضی شده است.",
    -8: "شماره کارت واریزکننده مجاز نیست.",
    -9: "مبلغ واریز شده با فاکتور سفارش مطابقت ندارد.",
    -98: "پرداخت توسط کاربر لغو شد (انصراف در درگاه بانکی).",
}


@app.api_route("/paystar/callback", methods=["GET", "POST"], response_class=HTMLResponse)
async def paystar_callback(request: Request):
    """Handles Paystar gateway redirect callback with robust logging and validation."""
    bot_user = await get_bot_username()
    try:
        if request.method.upper() == "POST":
            payload = await request.form()
        else:
            payload = request.query_params

        # 🔍 Terminal Debug Log: inspect the exact response from Paystar
        print("\n" + "=" * 50)
        print(f"📥 PAYSTAR CALLBACK RECEIVED [{request.method}]")
        for key, value in payload.items():
            print(f"  {key}: {value}")
        print("=" * 50 + "\n")

        try:
            status_code = int(payload.get("status", 0))
        except (TypeError, ValueError):
            status_code = 0

        order_id = str(payload.get("order_id", "")).strip()
        ref_num = str(payload.get("ref_num", "")).strip()
        card_number = str(payload.get("card_number", "")).strip()
        tracking_code = str(payload.get("tracking_code", "")).strip()

        if not order_id or not ref_num:
            return _failed_html("اطلاعات برگشتی درگاه ناقص است (کد رهگیری یا شماره مرجع دریافت نشد).", bot_username=bot_user)

        async with async_session_maker() as session:
            order = await OrdersRepository(session).get_by_tracking_code_with_details(order_id)
            payment = order.payment if order else None

            if order is None or payment is None or order.user is None or order.plan is None:
                return _failed_html(f"سفارش با کد پیگیری {order_id} در سیستم یافت نشد.", bot_username=bot_user)

            # Idempotency check: if order was already marked completed
            if payment.status == PaymentStatus.APPROVED.value and order.status == OrderStatus.COMPLETED.value:
                service_stmt = (
                    select(VPNService)
                    .options(joinedload(VPNService.plan))
                    .where(VPNService.order_id == order.id)
                    .limit(1)
                )
                service_res = await session.execute(service_stmt)
                service = service_res.scalars().first()
                if service is None:
                    return _success_html("پرداخت این سفارش قبلاً با موفقیت تایید و ثبت شده است.", bot_username=bot_user)
                context = await _build_paystar_context(order, service, settings)
                return _render_paystar_success_html(order, payment, context)

            # Check if gateway returned a failure code
            if status_code != 1:
                reason = PAYSTAR_STATUS_MESSAGES.get(status_code, f"تراکنش ناموفق بود (کد وضعیت پی‌استار: {status_code}).")
                logger.warning("paystar_gateway_failed", status=status_code, order_id=order_id, reason=reason)
                return _failed_html(reason, bot_username=bot_user)

            # Status is 1 -> verify transaction with Paystar API
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
                return _failed_html("خطا در برقراری ارتباط با سرور شاپرک/پی‌استار جهت تایید نهایی.", bot_username=bot_user)

            if not is_verified:
                return _failed_html("خطا در اعتبارسنجی تراکنش در شبکه بانکی (تراکنش تایید نشد).", bot_username=bot_user)

            # Payment verified -> finalize order
            payment.method = "paystar"
            payment.ref_id = ref_num
            payment.authority = tracking_code or payment.authority

            payment_service = PaymentService(session, VPNPanelService(), settings)
            try:
                await payment_service.approve_payment(payment.id)
            except PaymentAlreadyProcessedError:
                service_stmt = (
                    select(VPNService)
                    .options(joinedload(VPNService.plan))
                    .where(VPNService.order_id == order.id)
                    .limit(1)
                )
                service_res = await session.execute(service_stmt)
                service = service_res.scalars().first()
                if service is None:
                    return _success_html("پرداخت قبلاً تایید و ثبت شده است.", bot_username=bot_user)
                context = await _build_paystar_context(order, service, settings)
                return _render_paystar_success_html(order, payment, context)
            except PaymentExpiredError:
                return _failed_html("مهلت پرداخت این سفارش در ربات به پایان رسیده و منقضی شده است.", bot_username=bot_user)
            except PaymentApprovalError as exc:
                logger.exception("paystar_approval_failed", order_id=order_id, error=str(exc))
                return _failed_html("پرداخت بانکی تایید شد، اما در فعال‌سازی سرویس خطایی رخ داد.", bot_username=bot_user)

            # Load activated service
            service_stmt = (
                select(VPNService)
                .options(joinedload(VPNService.plan))
                .where(VPNService.order_id == order.id)
                .limit(1)
            )
            service_res = await session.execute(service_stmt)
            service = service_res.scalars().first()
            if service is None:
                return _failed_html("سرویس دی‌ان‌اس پس از پرداخت در سیستم یافت نشد.", bot_username=bot_user)

            try:
                await _apply_purchase_route(order, service, settings)
            except Exception as exc:
                logger.warning("paystar_route_update_failed", order_id=order_id, error=str(exc))

            context = await _build_paystar_context(order, service, settings)
            try:
                await _send_paystar_success_message(order, payment, context)
            except Exception as exc:
                logger.warning("failed_to_send_paystar_telegram_message", error=str(exc))

            return _render_paystar_success_html(order, payment, context)

    except Exception as global_exc:
        logger.exception("global_unhandled_callback_exception")
        return _failed_html(f"خطای غیرمنتظره سرور در ثبت نتیجه پرداخت: {str(global_exc)}", bot_username=bot_user)


def _failed_html(reason: str, bot_username: str = "bot") -> HTMLResponse:
    """Modern dark-themed failure card with action buttons."""
    html_content = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>تراکنش ناموفق</title>
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
        .card-box {{
            background-color: #1e293b;
            border: 1px solid #ef4444;
            border-radius: 16px;
            padding: 40px;
            box-shadow: 0 10px 25px -5px rgba(239, 68, 68, 0.2);
            max-width: 550px;
            width: 100%;
            text-align: center;
        }}
        .icon-box {{
            width: 75px;
            height: 75px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 20px;
            font-size: 38px;
            background-color: rgba(239, 68, 68, 0.1);
            color: #ef4444;
            border: 2px solid rgba(239, 68, 68, 0.3);
        }}
        .reason-box {{
            background-color: #0f172a;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 12px 16px;
            color: #f87171;
            font-size: 15px;
            line-height: 1.8;
        }}
        .btn-telegram {{
            background-color: #3b82f6;
            color: #ffffff;
            font-weight: bold;
            border: none;
            transition: all 0.2s;
        }}
        .btn-telegram:hover {{
            background-color: #2563eb;
            color: #ffffff;
        }}
    </style>
</head>
<body>
    <div class="card-box">
        <div class="icon-box">❌</div>
        <h1 class="h4 mb-3 fw-bold text-danger">تراکنش ناموفق بود</h1>
        <div class="reason-box mb-4">
            {escape(reason)}
        </div>
        <p class="text-secondary small mb-4">
            در صورتی که مبلغی از حساب شما کسر شده باشد، معمولاً ظرف مدت چند ساعت و نهایتاً ۷۲ ساعت از طرف بانک مبدا به حساب شما بازگردانده می‌شود.
        </p>
        <a href="https://t.me/{escape(bot_username)}" class="btn btn-telegram py-2 px-4 rounded-3 text-decoration-none w-100">
            بازگشت به ربات تلگرام
        </a>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)


def _success_html(message: str, bot_username: str = "bot") -> HTMLResponse:
    """Modern dark-themed success notification card."""
    html_content = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>تراکنش موفق</title>
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
        .card-box {{
            background-color: #1e293b;
            border: 1px solid #10b981;
            border-radius: 16px;
            padding: 40px;
            box-shadow: 0 10px 25px -5px rgba(16, 185, 129, 0.2);
            max-width: 550px;
            width: 100%;
            text-align: center;
        }}
        .icon-box {{
            width: 75px;
            height: 75px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 20px;
            font-size: 38px;
            background-color: rgba(16, 185, 129, 0.1);
            color: #10b981;
            border: 2px solid rgba(16, 185, 129, 0.3);
        }}
        .btn-telegram {{
            background-color: #10b981;
            color: #ffffff;
            font-weight: bold;
            border: none;
            transition: all 0.2s;
        }}
        .btn-telegram:hover {{
            background-color: #059669;
            color: #ffffff;
        }}
    </style>
</head>
<body>
    <div class="card-box">
        <div class="icon-box">✅</div>
        <h1 class="h4 mb-3 fw-bold text-success">پرداخت با موفقیت انجام شد</h1>
        <p class="text-light mb-4">{escape(message)}</p>
        <a href="https://t.me/{escape(bot_username)}" class="btn btn-telegram py-2 px-4 rounded-3 text-decoration-none w-100">
            بازگشت به ربات تلگرام
        </a>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)

def get_client_real_ip(request: Request) -> tuple[str, str | None]:
    """
    Extracts the true client IP and country header behind ArvanCloud, Cloudflare, or Nginx.
    Returns: (client_ip, country_code_if_available)
    """
    headers = request.headers

    # 1. ArvanCloud (ابر آروان)
    ar_ip = headers.get("ar-real-ip")
    ar_country = headers.get("ar-real-country") or headers.get("x-country-code")
    if ar_ip:
        return ar_ip.strip(), (ar_country.strip().upper() if ar_country else None)

    # 2. Cloudflare
    cf_ip = headers.get("cf-connecting-ip")
    cf_country = headers.get("cf-ipcountry")
    if cf_ip:
        return cf_ip.strip(), (cf_country.strip().upper() if cf_country else None)

    # 3. Standard Nginx / Reverse Proxy headers
    real_ip = headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip(), None

    xff = headers.get("x-forwarded-for")
    if xff:
        # First IP in XFF chain is the client
        return xff.split(",")[0].strip(), None

    # 4. Fallback to socket host
    fallback = request.client.host if request.client else ""
    return fallback, None
