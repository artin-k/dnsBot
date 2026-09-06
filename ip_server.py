# ip_server.py
import asyncio
import secrets
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
import structlog
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.config import get_settings, SLOT_CONFIGS
from app.database import async_session_maker
from app.models import IPAuthToken, Order, Payment, VPNService, OrderStatus, PaymentStatus
from app.repositories.orders import OrdersRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.services import ServicesRepository
from app.services.controld import ControlDService
from app.services.payment_service import (
    PaymentApprovalError,
    PaymentAlreadyProcessedError,
    PaymentExpiredError,
    PaymentService,
)
from app.services.vpn_panel import VPNPanelService
from app.services.paystar import PaystarService
from app.services.ip_manager import update_device_ip_safe
from bot.loader import create_bot
from app.services.vpn_detector import verify_user_ip
from bot.utils.messages import send_dns_delivery_card

logger = structlog.get_logger(__name__)

app = FastAPI(title="Control D Auto-IP & Payment Gateway")
settings = get_settings()
bot = create_bot(settings)

templates = Jinja2Templates(directory="templates")
WEB_SERVER_BASE_URL = settings.public_web_base_url


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
            response = await client.get(url, headers=headers, timeout=5.0)
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
        settings.bot_token.encode("utf-8"),
        str(uid).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return secrets.compare_digest(token, correct_token)


async def _apply_purchase_route(order: Order, service: VPNService, settings_obj) -> tuple[str, str | None]:
    _username, service_pk, slot_num_str = _parse_purchase_metadata(order.custom_username)
    return service_pk, slot_num_str


async def _build_paystar_context(order: Order, service: VPNService, settings_obj) -> dict[str, str]:
    raw_username, service_pk, pop_code = _parse_purchase_metadata(order.custom_username)
    username = raw_username or f"user{order.user_id}"
    service_display = service_pk.capitalize() if service_pk != "default" else "🌐 کل ترافیک اینترنت"

    if service.plan and service.plan.controld_profile_id and service_pk != "default":
        try:
            controld_service = ControlDService(settings_obj)
            services = await asyncio.wait_for(
                controld_service.fetch_controld_services(service.plan.controld_profile_id),
                timeout=2.0,
            )
            if services:
                for item in services:
                    if item.get("pk") == service_pk and item.get("name"):
                        service_display = item["name"]
                        break
        except Exception:
            pass

    country_display = pop_code or "پیش‌فرض"

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
        expire_str = jdatetime.datetime.fromgregorian(datetime=naive_tehran).strftime("%Y/%m/%d - %H:%M:%S")
    except Exception:
        expire_str = expire_at.astimezone(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "username": username,
        "service_display": service_display,
        "country_display": country_display,
        "duration_text": calculate_remaining_time_fa(expire_at),
        "expire_str": expire_str,
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


async def _send_paystar_success_message(order: Order, payment: Payment, context: dict[str, str]) -> None:
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


_bot_username = None


async def get_bot_username() -> str:
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
    bot_username: str = "bot",
) -> HTMLResponse:
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
                box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
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


def get_client_real_ip(request: Request) -> tuple[str, str | None]:
    headers = request.headers
    ar_ip = headers.get("ar-real-ip")
    ar_country = headers.get("ar-real-country") or headers.get("x-country-code")
    if ar_ip:
        return ar_ip.strip(), (ar_country.strip().upper() if ar_country else None)

    cf_ip = headers.get("cf-connecting-ip")
    cf_country = headers.get("cf-ipcountry")
    if cf_ip:
        return cf_ip.strip(), (cf_country.strip().upper() if cf_country else None)

    real_ip = headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip(), None

    xff = headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip(), None

    fallback = request.client.host if request.client else ""
    return fallback, None


# ============================================================================
# USER DASHBOARD (SHELTER REPLICA)
# ============================================================================

@app.get("/ip/{token}", response_class=HTMLResponse)
async def user_dashboard_view(request: Request, token: str):
    bot_user = await get_bot_username()
    token = token.strip()

    if not re.match(r"^[a-fA-F0-9-]{32,36}$", token):
        return _render_capture_ip_html("خطا", "لینک نامعتبر است", "ساختار توکن معتبر نیست.", False, bot_username=bot_user)

    client_ip, _ = get_client_real_ip(request)

    async with async_session_maker() as session:
        stmt = (
            select(IPAuthToken)
            .options(
                joinedload(IPAuthToken.service).joinedload(VPNService.user),
                joinedload(IPAuthToken.service).joinedload(VPNService.plan),
            )
            .where(IPAuthToken.token == token)
            .limit(1)
        )
        res = await session.execute(stmt)
        token_record = res.scalars().first()

        if not token_record or not token_record.service:
            return _render_capture_ip_html("خطا", "لینک منقضی یا نامعتبر", "این اشتراک یا توکن یافت نشد.", False, bot_username=bot_user)

        service = token_record.service
        now = datetime.now(timezone.utc)
        expires_at = token_record.expires_at if token_record.expires_at.tzinfo else token_record.expires_at.replace(tzinfo=timezone.utc)

        if now > expires_at:
            return _render_capture_ip_html("خطا", "انقضای توکن", "مهلت استفاده از این لینک به پایان رسیده است. از ربات لینک جدید بگیرید.", False, bot_username=bot_user)

        device_id = service.controld_device_id
        dns_ips = await get_controld_device_ips(device_id, settings) if device_id else {
            "ipv4_primary": "76.76.2.162",
            "ipv4_secondary": "76.76.10.162",
        }

        duration_text = calculate_remaining_time_fa(service.expire_at)
        tehran_tz = ZoneInfo("Asia/Tehran")
        expire_target = service.expire_at if service.expire_at.tzinfo else service.expire_at.replace(tzinfo=timezone.utc)
        shamsi_expire = jdatetime.datetime.fromgregorian(datetime=expire_target.astimezone(tehran_tz).replace(tzinfo=None)).strftime("%Y/%m/%d")

        context = {
            "request": request,
            "token": token,
            "client_ip": client_ip,
            "bot_username": bot_user,
            "service": service,
            "user": service.user,
            "plan": service.plan,
            "dns_primary": dns_ips["ipv4_primary"],
            "dns_secondary": dns_ips["ipv4_secondary"],
            "duration_text": duration_text,
            "shamsi_expire": shamsi_expire,
            "is_active": service.status == "active" and (expire_target > now),
        }
        return templates.TemplateResponse("user_panel.html", context)


@app.post("/api/ip/{token}/update")
async def api_update_ip(request: Request, token: str):
    token = token.strip()
    client_ip, _ = get_client_real_ip(request)

    ip_check = await verify_user_ip(client_ip)
    if not ip_check.is_iran:
        return {
            "success": False,
            "message": ip_check.error_message or "فیلترشکن شما روشن است! فقط اتصالات مستقیم ایران مجاز هستند.",
        }

    async with async_session_maker() as session:
        stmt = (
            select(IPAuthToken)
            .options(joinedload(IPAuthToken.service))
            .where(IPAuthToken.token == token)
            .limit(1)
        )
        res = await session.execute(stmt)
        token_record = res.scalars().first()

        if not token_record or not token_record.service:
            return {"success": False, "message": "اشتراک یا توکن معتبر یافت نشد."}

        now = datetime.now(timezone.utc)
        expires_at = token_record.expires_at if token_record.expires_at.tzinfo else token_record.expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            return {"success": False, "message": "این لینک منقضی شده است. لطفاً از طریق ربات لینک جدیدی دریافت کنید."}

        service = token_record.service

        if service.authorized_ip == client_ip:
            return {"success": True, "message": f"آی‌پی {client_ip} هم‌اکنون روی اشتراک شما فعال است."}

        success = await update_device_ip_safe(session, service, client_ip)
        if success:
            return {"success": True, "message": f"آی‌پی {client_ip} با موفقیت تایید و روی دی‌ان‌اس اختصاصی شما فعال شد."}
        else:
            return {"success": False, "message": "خطا در تنظیم دی‌ان‌اس روی سرورها. لطفاً لحظاتی دیگر تلاش کنید."}


# ============================================================================
# ENTRYPOINT (TOP LEVEL - OUTSIDE OF ANY FUNCTION)
# ============================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ip_server:app", host="0.0.0.0", port=8000, reload=False)