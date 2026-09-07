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
        return "█│█░ ╪▒┘ê╪▓"
    now = datetime.now(timezone.utc)
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    delta = expire_at - now
    total_seconds = delta.total_seconds()
    if total_seconds <= 0:
        return "┘╛╪º█î╪º┘å █î╪º┘ü╪¬┘ç"
    total_hours = int(total_seconds // 3600)
    if total_hours >= 24:
        return f"{total_hours // 24} ╪▒┘ê╪▓"
    if total_hours > 0:
        return f"{total_hours} ╪│╪º╪╣╪¬"
    return f"{int(total_seconds // 60)} ╪»┘é█î┘é┘ç"


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

    service_display = service_pk.capitalize() if service_pk != "default" else "≡ƒîÉ ┌⌐┘ä ╪¬╪▒╪º┘ü█î┌⌐ ╪º█î┘å╪¬╪▒┘å╪¬"
    
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

    country_display = pop_code or "┘╛█î╪┤ΓÇî┘ü╪▒╪╢"

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
        # ≡ƒ¢á FIX: Defined expire_str directly to resolve the UnboundLocalError [cite: 1]
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
        <title>┘╛╪▒╪»╪º╪«╪¬ ┘à┘ê┘ü┘é█î╪¬ΓÇî╪ó┘à█î╪▓</title>
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
            <h1>Γ£à ┘╛╪▒╪»╪º╪«╪¬ ╪┤┘à╪º ╪¿╪º ┘à┘ê┘ü┘é█î╪¬ ╪º┘å╪¼╪º┘à ╪┤╪»!</h1>
            <p>┌⌐╪» ╪▒┘ç┌»█î╪▒█î ╪│┘ü╪º╪▒╪┤: <b>{escape(order.tracking_code)}</b></p>
            <p>┌⌐╪» ┘╛█î┌»█î╪▒█î ╪¬╪▒╪º┌⌐┘å╪┤: <b>{escape(payment.ref_id or "-")}</b></p>
            <p>┘å╪º┘à ┌⌐╪º╪▒╪¿╪▒█î ╪»╪│╪¬┌»╪º┘ç: <b>{escape(context["username"])}</b></p>
            <p>╪¿╪▒┘å╪º┘à┘ç/╪¿╪º╪▓█î: <b>{escape(context["service_display"])}</b></p>
            <p>╪│╪▒┘ê╪▒ (┌⌐╪┤┘ê╪▒): <b>{escape(context["country_display"])}</b></p>
            <p>┘à╪»╪¬ ╪º╪╣╪¬╪¿╪º╪▒: <b>{escape(context["duration_text"])}</b></p>
            <p>╪¬╪º╪▒█î╪« ╪º┘å┘é╪╢╪º: <b>{escape(context["expire_str"])}</b></p>
            <p>DNS ╪º╪«╪¬╪╡╪º╪╡█î ╪┤┘à╪º:</p>
            <p>Primary: <code>{escape(context["ipv4_primary"])}</code></p>
            <p>Secondary: <code>{escape(context["ipv4_secondary"])}</code></p>
            <p>╪¼╪▓╪ª█î╪º╪¬ ╪º╪¬╪╡╪º┘ä ╪¿┘ç ╪¬┘ä┌»╪▒╪º┘à ╪┤┘à╪º ╪º╪▒╪│╪º┘ä ╪┤╪».</p>
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
            title_prefix="Γ£à <b>┘╛╪▒╪»╪º╪«╪¬ ╪ó┘å┘ä╪º█î┘å ╪¬╪º█î█î╪» ┘ê ╪º╪┤╪¬╪▒╪º┌⌐ ┘ü╪╣╪º┘ä ╪┤╪»!</b>",
            ipv4_primary=context.get("ipv4_primary", "76.76.2.162"),
            ipv4_secondary=context.get("ipv4_secondary", "76.76.10.162"),
            service_display=context.get("service_display", "┌⌐┘ä ╪¬╪▒╪º┘ü█î┌⌐ ╪º█î┘å╪¬╪▒┘å╪¬ (Default)"),
            country_display=context.get("country_display", "┘╛█î╪┤ΓÇî┘ü╪▒╪╢"),
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
    icon = "Γ£à" if is_success else "Γ¥î"
    
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
            <a href="https://t.me/{escape(bot_username)}" class="btn btn-home py-2 px-4 rounded-3 text-decoration-none d-inline-block">╪¿╪º╪▓┌»╪┤╪¬ ╪¿┘ç ╪▒╪¿╪º╪¬ ╪¬┘ä┌»╪▒╪º┘à</a>
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
    custom_msg = error_message or "╪ó█îΓÇî┘╛█î ╪┤┘å╪º╪│╪º█î█îΓÇî╪┤╪»┘ç ╪┤┘à╪º ┘à╪¬╪╣┘ä┘é ╪¿┘ç ╪│╪▒┘ê╪▒ ╪«╪º╪▒╪¼█î █î╪º ┘ü█î┘ä╪¬╪▒╪┤┌⌐┘å ╪º╪│╪¬."
    html_content = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>┘ü█î┘ä╪¬╪▒╪┤┌⌐┘å ╪┤┘à╪º ╪▒┘ê╪┤┘å ╪º╪│╪¬</title>
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
        <div class="icon-box">ΓÜá∩╕Å</div>
        <h1 class="h4 mb-3 fw-bold text-warning">┘ü█î┘ä╪¬╪▒╪┤┌⌐┘å ╪┤┘à╪º ╪▒┘ê╪┤┘å ╪º╪│╪¬!</h1>
        <p class="text-light mb-3" style="font-size: 15px; line-height: 1.8;">{escape(custom_msg)}<br>╪¿╪▒╪º█î ┘ü╪╣╪º┘äΓÇî╪│╪º╪▓█î DNS╪î ╪½╪¿╪¬ ╪ó█îΓÇî┘╛█î <b>┘ü┘é╪╖ ╪¿╪º ╪º█î┘å╪¬╪▒┘å╪¬ ┘à╪│╪¬┘é█î┘à ╪º█î╪▒╪º┘å</b> ╪º┘à┌⌐╪º┘åΓÇî┘╛╪░█î╪▒ ╪º╪│╪¬.</p>
        <div class="alert alert-dark text-start small mb-4 py-2 border-secondary" style="font-size: 13px;">
            1∩╕ÅΓâú ┘ü█î┘ä╪¬╪▒╪┤┌⌐┘å ┘ê ┘╛╪▒┘ê┌⌐╪│█î ╪¬┘ä┌»╪▒╪º┘à ╪«┘ê╪» ╪▒╪º ┌⌐╪º┘à┘ä╪º┘ï ╪«╪º┘à┘ê╪┤ ┌⌐┘å█î╪».<br>
            2∩╕ÅΓâú ┘à╪╖┘à╪ª┘å ╪┤┘ê█î╪» ╪¿┘ç ╪º█î┘å╪¬╪▒┘å╪¬ ╪º╪╡┘ä█î/┘ê╪º█îΓÇî┘ü╪º█î ╪«┘ê╪» ┘à╪¬╪╡┘ä ┘ç╪│╪¬█î╪».<br>
            3∩╕ÅΓâú ╪»┌⌐┘à┘ç ╪▓█î╪▒ ╪▒╪º ┘ä┘à╪│ ┌⌐┘å█î╪»:
        </div>
        <button onclick="location.reload()" class="btn btn-reload py-2 px-4 rounded-3 w-100 mb-2">≡ƒöä ┘ü█î┘ä╪¬╪▒╪┤┌⌐┘å ╪▒╪º ╪«╪º┘à┘ê╪┤ ┌⌐╪▒╪»┘à╪î ╪¿╪▒╪▒╪│█î ┘à╪¼╪»╪»</button>
        <a href="https://t.me/{escape(bot_username)}" class="btn btn-outline-secondary py-2 px-4 rounded-3 w-100 text-decoration-none">╪¿╪º╪▓┌»╪┤╪¬ ╪¿┘ç ╪▒╪¿╪º╪¬ ╪¬┘ä┌»╪▒╪º┘à</a>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)


@app.get("/capture-ip/{token}", response_class=HTMLResponse)
async def capture_ip(request: Request, token: str):
    bot_user = await get_bot_username()
    token = token.strip()
    if not re.match(r"^[a-fA-F0-9-]{32,36}$", token):
        return _render_capture_ip_html("╪«╪╖╪º ╪»╪▒ ╪½╪¿╪¬ ╪ó█îΓÇî┘╛█î", "┘ä█î┘å┌⌐ ┘å╪º┘à╪╣╪¬╪¿╪▒ ╪º╪│╪¬", "╪│╪º╪«╪¬╪º╪▒ ╪¬┘ê┌⌐┘å ╪º┘à┘å█î╪¬█î ┘å╪º┘à╪╣╪¬╪¿╪▒ ╪º╪│╪¬.", False, bot_username=bot_user)

    client_ip, cdn_country = get_client_real_ip(request)

    # ≡ƒ¢í∩╕Å ANTI-VPN CHECK
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

        if not token_record:
            return _render_capture_ip_html("╪«╪╖╪º ╪»╪▒ ╪½╪¿╪¬ ╪ó█îΓÇî┘╛█î", "┘ä█î┘å┌⌐ ┘å╪º┘à╪╣╪¬╪¿╪▒", "╪º█î┘å ┘ä█î┘å┌⌐ ┘ê╪¼┘ê╪» ┘å╪»╪º╪▒╪».", False, bot_username=bot_user)

        now = datetime.now(timezone.utc)
        expires_at = token_record.expires_at.replace(tzinfo=timezone.utc) if token_record.expires_at.tzinfo is None else token_record.expires_at
        if now > expires_at:
            return _render_capture_ip_html("╪«╪╖╪º ╪»╪▒ ╪½╪¿╪¬ ╪ó█îΓÇî┘╛█î", "╪º┘å┘é╪╢╪º█î ┘ä█î┘å┌⌐", "┘à┘ç┘ä╪¬ ╪º╪│╪¬┘ü╪º╪»┘ç ╪º╪▓ ╪º█î┘å ┘ä█î┘å┌⌐ ┌»╪░╪┤╪¬┘ç ╪º╪│╪¬. ┘ä╪╖┘ü╪º┘ï ╪º╪▓ ╪▒╪¿╪º╪¬ ┘ä█î┘å┌⌐ ╪¼╪»█î╪» ╪»╪▒█î╪º┘ü╪¬ ┌⌐┘å█î╪».", False, bot_username=bot_user)

        service = token_record.service
        if not service:
            return _render_capture_ip_html("╪«╪╖╪º ╪»╪▒ ╪½╪¿╪¬ ╪ó█îΓÇî┘╛█î", "╪│╪▒┘ê█î╪│ █î╪º┘ü╪¬ ┘å╪┤╪»", "╪│╪▒┘ê█î╪│ ┘à┘ê╪▒╪» ┘å╪╕╪▒ █î╪º┘ü╪¬ ┘å╪┤╪».", False, bot_username=bot_user)

        # Γ£¿ FIX: If the IP is already registered, return success immediately!
        if service.authorized_ip == client_ip:
            return _render_capture_ip_html(
                title="╪½╪¿╪¬ ╪ó█îΓÇî┘╛█î ┘à┘ê┘ü┘é█î╪¬ΓÇî╪ó┘à█î╪▓", 
                heading="Γ£à ╪ó█îΓÇî┘╛█î ╪┤┘à╪º ┘ü╪╣╪º┘ä ╪º╪│╪¬!", 
                message=f"╪ó█îΓÇî┘╛█î ┘ü╪╣┘ä█î ╪┤┘à╪º ({client_ip}) ╪º╪▓ ┘é╪¿┘ä ╪▒┘ê█î ╪º█î┘å ╪º╪┤╪¬╪▒╪º┌⌐ ╪½╪¿╪¬ ┘ê ┘ü╪╣╪º┘ä ┘à█îΓÇî╪¿╪º╪┤╪».", 
                is_success=True, 
                client_ip=client_ip, 
                bot_username=bot_user
            )

        # Run the update safely for a NEW IP.
        success = await update_device_ip_safe(session, service, client_ip)
        if success:
            return _render_capture_ip_html("╪½╪¿╪¬ ╪ó█îΓÇî┘╛█î ┘à┘ê┘ü┘é█î╪¬ΓÇî╪ó┘à█î╪▓", "Γ£à ╪½╪¿╪¬ ╪ó█îΓÇî┘╛█î ╪¿╪º ┘à┘ê┘ü┘é█î╪¬ ╪º┘å╪¼╪º┘à ╪┤╪»!", f"╪ó█îΓÇî┘╛█î ╪º█î╪▒╪º┘å ({client_ip}) ╪¿╪º ┘à┘ê┘ü┘é█î╪¬ ╪½╪¿╪¬ ╪┤╪».", True, client_ip, bot_user)
        
        return _render_capture_ip_html("╪«╪╖╪º ╪»╪▒ ╪½╪¿╪¬ ╪ó█îΓÇî┘╛█î", "╪«╪╖╪º█î ╪│╪▒┘ê╪▒", "╪«╪╖╪º ╪»╪▒ ╪½╪¿╪¬ ╪»╪▒ ╪│╪▒┘ê╪▒ ╪»█îΓÇî╪º┘åΓÇî╪º╪│. ┘ä╪╖┘ü╪º┘ï ╪»┘ê╪¿╪º╪▒┘ç ╪¬┘ä╪º╪┤ ┌⌐┘å█î╪».", False, bot_username=bot_user)
    
    
@app.get("/update-ip/{device_id}", response_class=HTMLResponse, status_code=status.HTTP_410_GONE)
async def retired_update_device_ip(device_id: str):
    """Reject unsafe pre-token links; never select a service by shared slot."""
    bot_user = await get_bot_username()
    return _render_capture_ip_html(
        "╪º█î┘å ┘ä█î┘å┌⌐ ┘à┘å┘é╪╢█î ╪┤╪»┘ç ╪º╪│╪¬",
        "ΓÜá∩╕Å ┘ä█î┘å┌⌐ ┘é╪»█î┘à█î ╪º╪│╪¬",
        "┘ä╪╖┘ü╪º┘ï ╪¿┘ç ╪▒╪¿╪º╪¬ ╪¿╪º╪▓┌»╪▒╪»█î╪» ┘ê ╪º╪▓ ┘ä█î┘å┌⌐ ╪º┘à┘å ╪½╪¿╪¬ IP ╪º╪│╪¬┘ü╪º╪»┘ç ┌⌐┘å█î╪».",
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
            detail="╪»╪│╪¬╪▒╪│█î ╪║█î╪▒┘à╪¼╪º╪▓ ╪º╪│╪¬. ┘ä╪╖┘ü╪º ╪º╪▓ ╪╖╪▒█î┘é ╪»┌⌐┘à┘ç ┘à╪»█î╪▒█î╪¬ ╪▒╪¿╪º╪¬ ╪¬┘ä┌»╪▒╪º┘à ┘ê╪º╪▒╪» ╪┤┘ê█î╪»."
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

            slot_name = "╪½╪¿╪¬ ┘å╪┤╪»┘ç / Unmapped"
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
                "authorized_ip": row.authorized_ip or "╪½╪¿╪¬ ┘å╪┤╪»┘ç (No IP)",
                "expire_at_shamsi": shamsi_expire,
                "status": "┘ü╪╣╪º┘ä" if row.status == "active" else "┘à┘å┘é╪╢█î ╪┤╪»┘ç",
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
         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="╪»╪│╪¬╪▒╪│█î ╪║█î╪▒┘à╪¼╪º╪▓.")

    if not ip or ip == "╪½╪¿╪¬ ┘å╪┤╪»┘ç (No IP)":
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
         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="╪»╪│╪¬╪▒╪│█î ╪║█î╪▒┘à╪¼╪º╪▓.")

    new_ip = new_ip.strip()
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", new_ip):
        raise HTTPException(status_code=400, detail="┘ü╪▒┘à╪¬ ╪ó█îΓÇî┘╛█î ╪╣╪»╪»█î ┘ê╪º╪▒╪» ╪┤╪»┘ç ┘à╪╣╪¬╪¿╪▒ ┘å█î╪│╪¬.")

    async with async_session_maker() as session:
        stmt = select(VPNService).where(VPNService.id == service_id).limit(1)
        res = await session.execute(stmt)
        service = res.scalars().first()
        
        if not service:
            raise HTTPException(status_code=404, detail="╪│╪▒┘ê█î╪│ ┘à┘ê╪▒╪» ┘å╪╕╪▒ █î╪º┘ü╪¬ ┘å╪┤╪».")

        success = await update_device_ip_safe(session, service, new_ip)
        if not success:
            raise HTTPException(status_code=500, detail="╪«╪╖╪º ╪»╪▒ ╪½╪¿╪¬ ╪ó█îΓÇî┘╛█î ╪»╪▒ ┘╛┘å┘ä")

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
                return _failed_html("╪¬┘ê┌⌐┘å ┘╛╪▒╪»╪º╪«╪¬ ┘à╪╣╪¬╪¿╪▒ ┘å█î╪│╪¬ █î╪º ┘à┘å┘é╪╢█î ╪┤╪»┘ç ╪º╪│╪¬.", bot_username=bot_user)
            if payment.method != "paystar":
                return _failed_html("╪º█î┘å ┘ä█î┘å┌⌐ ╪¿╪▒╪º█î ┘╛╪▒╪»╪º╪«╪¬ ╪ó┘å┘ä╪º█î┘å ┘╛█îΓÇî╪º╪│╪¬╪º╪▒ ╪½╪¿╪¬ ┘å╪┤╪»┘ç ╪º╪│╪¬.", bot_username=bot_user)
            if payment.status == PaymentStatus.APPROVED.value or payment.order.status == OrderStatus.COMPLETED.value:
                return _success_html("╪º█î┘å ╪│┘ü╪º╪▒╪┤ ┘é╪¿┘ä╪º┘ï ╪¿╪º ┘à┘ê┘ü┘é█î╪¬ ┘╛╪▒╪»╪º╪«╪¬ ┘ê ┘å┘ç╪º█î█î ╪┤╪»┘ç ╪º╪│╪¬.", bot_username=bot_user)

        # Build the HTML form to auto-submit a POST request to Paystar
        html_content = f"""
        <!DOCTYPE html>
        <html lang="fa" dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <!-- Forces the browser to send your exact domain to Paystar's security check -->
            <meta name="referrer" content="origin" />
            <title>╪»╪▒ ╪¡╪º┘ä ╪º┘å╪¬┘é╪º┘ä...</title>
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
                <h3 style="margin-bottom: 10px;">╪»╪▒ ╪¡╪º┘ä ╪º┘å╪¬┘é╪º┘ä ╪¿┘ç ╪»╪▒┌»╪º┘ç ╪¿╪º┘å┌⌐█î...</h3>
                <p style="color: #94a3b8; font-size: 14px;">┘ä╪╖┘ü╪º┘ï ┌å┘å╪» ┘ä╪¡╪╕┘ç ╪╡╪¿╪▒ ┌⌐┘å█î╪».</p>
                
                <!-- REVERTED TO OFFICIAL .ir DOMAIN -->
                <form id="paymentForm" action="https://core.paystar.ir/api/pardakht/payment" method="POST">
                    <input type="hidden" name="token" value="{clean_token}">
                    <noscript>
                        <p style="color: #ef4444; margin-top: 20px;">╪¼╪º┘ê╪º╪º╪│┌⌐╪▒█î┘╛╪¬ ╪»╪▒ ┘à╪▒┘ê╪▒┌»╪▒ ╪┤┘à╪º ╪║█î╪▒┘ü╪╣╪º┘ä ╪º╪│╪¬.</p>
                    </noscript>
                    <button type="submit" id="fallbackBtn" class="btn-fallback" style="display: none;">
                        ╪º┘å╪¬┘é╪º┘ä ╪»╪│╪¬█î ╪¿┘ç ╪»╪▒┌»╪º┘ç ┘╛╪▒╪»╪º╪«╪¬
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
        return _failed_html(f"╪«╪╖╪º█î ╪»╪º╪«┘ä█î ╪»╪▒ ╪º╪¬╪╡╪º┘ä ╪¿┘ç ╪»╪▒┌»╪º┘ç ╪¿╪º┘å┌⌐█î: {str(exc)}", bot_username=bot_user)
    
# ============================================================================
# PAYSTAR GATEWAY CALLBACK & RESULT PAGES
# ============================================================================

PAYSTAR_STATUS_MESSAGES = {
    -1: "╪»╪▒╪«┘ê╪º╪│╪¬ ┘å╪º┘à╪╣╪¬╪¿╪▒ ╪º╪│╪¬ (╪«╪╖╪º█î ╪»╪º╪«┘ä█î █î╪º ╪│╪º╪«╪¬╪º╪▒ ┘å╪º┘é╪╡ ╪»╪º╪»┘çΓÇî┘ç╪º).",
    -2: "╪»╪▒┌»╪º┘ç ┘╛╪▒╪»╪º╪«╪¬ ┘ü╪╣╪º┘ä ┘å█î╪│╪¬ █î╪º ╪º╪╖┘ä╪º╪╣╪º╪¬ ╪º╪¡╪▒╪º╪▓ ┘ç┘ê█î╪¬ (Gateway ID / Sign Key) ┘å╪º┘à╪╣╪¬╪¿╪▒ ╪º╪│╪¬.",
    -3: "╪ó╪»╪▒╪│ ╪ó█îΓÇî┘╛█î ╪│╪▒┘ê╪▒ ╪¿╪▒╪º█î ╪º█î┘å ╪»╪▒┌»╪º┘ç ┘╛╪▒╪»╪º╪«╪¬ ╪»╪▒ ┘╛┘å┘ä ┘╛█îΓÇî╪º╪│╪¬╪º╪▒ ╪¬╪╣╪▒█î┘ü/┘à╪¼╪º╪▓ ┘å╪┤╪»┘ç ╪º╪│╪¬.",
    -4: "┘à╪¿┘ä╪║ ╪º╪▒╪│╪º┘ä█î ╪¿┘ç ╪»╪▒┌»╪º┘ç ┘å╪º┘à╪╣╪¬╪¿╪▒ ╪º╪│╪¬.",
    -5: "╪¬╪▒╪º┌⌐┘å╪┤ ╪¬┌⌐╪▒╪º╪▒█î ╪º╪│╪¬ █î╪º ┘é╪¿┘ä╪º┘ï ┘╛╪▒╪»╪º╪▓╪┤ ╪┤╪»┘ç ╪º╪│╪¬.",
    -6: "╪¬╪▒╪º┌⌐┘å╪┤ ╪»╪▒ ╪│█î╪│╪¬┘à ┘╛█îΓÇî╪º╪│╪¬╪º╪▒ ┘╛█î╪»╪º ┘å╪┤╪».",
    -7: "┘à┘ç┘ä╪¬ ┘╛╪▒╪»╪º╪«╪¬ ╪¿┘ç ┘╛╪º█î╪º┘å ╪▒╪│█î╪»┘ç ┘ê ┘ü╪º┌⌐╪¬┘ê╪▒ ┘à┘å┘é╪╢█î ╪┤╪»┘ç ╪º╪│╪¬.",
    -8: "╪┤┘à╪º╪▒┘ç ┌⌐╪º╪▒╪¬ ┘ê╪º╪▒█î╪▓┌⌐┘å┘å╪»┘ç ┘à╪¼╪º╪▓ ┘å█î╪│╪¬.",
    -9: "┘à╪¿┘ä╪║ ┘ê╪º╪▒█î╪▓ ╪┤╪»┘ç ╪¿╪º ┘ü╪º┌⌐╪¬┘ê╪▒ ╪│┘ü╪º╪▒╪┤ ┘à╪╖╪º╪¿┘é╪¬ ┘å╪»╪º╪▒╪».",
    -98: "┘╛╪▒╪»╪º╪«╪¬ ╪¬┘ê╪│╪╖ ┌⌐╪º╪▒╪¿╪▒ ┘ä╪║┘ê ╪┤╪» (╪º┘å╪╡╪▒╪º┘ü ╪»╪▒ ╪»╪▒┌»╪º┘ç ╪¿╪º┘å┌⌐█î).",
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

        # ≡ƒöì Terminal Debug Log: inspect the exact response from Paystar
        print("\n" + "=" * 50)
        print(f"≡ƒôÑ PAYSTAR CALLBACK RECEIVED [{request.method}]")
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
            return _failed_html("╪º╪╖┘ä╪º╪╣╪º╪¬ ╪¿╪▒┌»╪┤╪¬█î ╪»╪▒┌»╪º┘ç ┘å╪º┘é╪╡ ╪º╪│╪¬ (┌⌐╪» ╪▒┘ç┌»█î╪▒█î █î╪º ╪┤┘à╪º╪▒┘ç ┘à╪▒╪¼╪╣ ╪»╪▒█î╪º┘ü╪¬ ┘å╪┤╪»).", bot_username=bot_user)

        async with async_session_maker() as session:
            order = await OrdersRepository(session).get_by_tracking_code_with_details(order_id)
            payment = order.payment if order else None

            if order is None or payment is None or order.user is None or order.plan is None:
                return _failed_html(f"╪│┘ü╪º╪▒╪┤ ╪¿╪º ┌⌐╪» ┘╛█î┌»█î╪▒█î {order_id} ╪»╪▒ ╪│█î╪│╪¬┘à █î╪º┘ü╪¬ ┘å╪┤╪».", bot_username=bot_user)

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
                    return _success_html("┘╛╪▒╪»╪º╪«╪¬ ╪º█î┘å ╪│┘ü╪º╪▒╪┤ ┘é╪¿┘ä╪º┘ï ╪¿╪º ┘à┘ê┘ü┘é█î╪¬ ╪¬╪º█î█î╪» ┘ê ╪½╪¿╪¬ ╪┤╪»┘ç ╪º╪│╪¬.", bot_username=bot_user)
                context = await _build_paystar_context(order, service, settings)
                return _render_paystar_success_html(order, payment, context)

            # Check if gateway returned a failure code
            if status_code != 1:
                reason = PAYSTAR_STATUS_MESSAGES.get(status_code, f"╪¬╪▒╪º┌⌐┘å╪┤ ┘å╪º┘à┘ê┘ü┘é ╪¿┘ê╪» (┌⌐╪» ┘ê╪╢╪╣█î╪¬ ┘╛█îΓÇî╪º╪│╪¬╪º╪▒: {status_code}).")
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
                return _failed_html("╪«╪╖╪º ╪»╪▒ ╪¿╪▒┘é╪▒╪º╪▒█î ╪º╪▒╪¬╪¿╪º╪╖ ╪¿╪º ╪│╪▒┘ê╪▒ ╪┤╪º┘╛╪▒┌⌐/┘╛█îΓÇî╪º╪│╪¬╪º╪▒ ╪¼┘ç╪¬ ╪¬╪º█î█î╪» ┘å┘ç╪º█î█î.", bot_username=bot_user)

            if not is_verified:
                return _failed_html("╪«╪╖╪º ╪»╪▒ ╪º╪╣╪¬╪¿╪º╪▒╪│┘å╪¼█î ╪¬╪▒╪º┌⌐┘å╪┤ ╪»╪▒ ╪┤╪¿┌⌐┘ç ╪¿╪º┘å┌⌐█î (╪¬╪▒╪º┌⌐┘å╪┤ ╪¬╪º█î█î╪» ┘å╪┤╪»).", bot_username=bot_user)

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
                    return _success_html("┘╛╪▒╪»╪º╪«╪¬ ┘é╪¿┘ä╪º┘ï ╪¬╪º█î█î╪» ┘ê ╪½╪¿╪¬ ╪┤╪»┘ç ╪º╪│╪¬.", bot_username=bot_user)
                context = await _build_paystar_context(order, service, settings)
                return _render_paystar_success_html(order, payment, context)
            except PaymentExpiredError:
                return _failed_html("┘à┘ç┘ä╪¬ ┘╛╪▒╪»╪º╪«╪¬ ╪º█î┘å ╪│┘ü╪º╪▒╪┤ ╪»╪▒ ╪▒╪¿╪º╪¬ ╪¿┘ç ┘╛╪º█î╪º┘å ╪▒╪│█î╪»┘ç ┘ê ┘à┘å┘é╪╢█î ╪┤╪»┘ç ╪º╪│╪¬.", bot_username=bot_user)
            except PaymentApprovalError as exc:
                logger.exception("paystar_approval_failed", order_id=order_id, error=str(exc))
                return _failed_html("┘╛╪▒╪»╪º╪«╪¬ ╪¿╪º┘å┌⌐█î ╪¬╪º█î█î╪» ╪┤╪»╪î ╪º┘à╪º ╪»╪▒ ┘ü╪╣╪º┘äΓÇî╪│╪º╪▓█î ╪│╪▒┘ê█î╪│ ╪«╪╖╪º█î█î ╪▒╪« ╪»╪º╪».", bot_username=bot_user)

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
                return _failed_html("╪│╪▒┘ê█î╪│ ╪»█îΓÇî╪º┘åΓÇî╪º╪│ ┘╛╪│ ╪º╪▓ ┘╛╪▒╪»╪º╪«╪¬ ╪»╪▒ ╪│█î╪│╪¬┘à █î╪º┘ü╪¬ ┘å╪┤╪».", bot_username=bot_user)

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
        return _failed_html(f"╪«╪╖╪º█î ╪║█î╪▒┘à┘å╪¬╪╕╪▒┘ç ╪│╪▒┘ê╪▒ ╪»╪▒ ╪½╪¿╪¬ ┘å╪¬█î╪¼┘ç ┘╛╪▒╪»╪º╪«╪¬: {str(global_exc)}", bot_username=bot_user)


def _failed_html(reason: str, bot_username: str = "bot") -> HTMLResponse:
    """Modern dark-themed failure card with action buttons."""
    html_content = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>╪¬╪▒╪º┌⌐┘å╪┤ ┘å╪º┘à┘ê┘ü┘é</title>
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
        <div class="icon-box">Γ¥î</div>
        <h1 class="h4 mb-3 fw-bold text-danger">╪¬╪▒╪º┌⌐┘å╪┤ ┘å╪º┘à┘ê┘ü┘é ╪¿┘ê╪»</h1>
        <div class="reason-box mb-4">
            {escape(reason)}
        </div>
        <p class="text-secondary small mb-4">
            ╪»╪▒ ╪╡┘ê╪▒╪¬█î ┌⌐┘ç ┘à╪¿┘ä╪║█î ╪º╪▓ ╪¡╪│╪º╪¿ ╪┤┘à╪º ┌⌐╪│╪▒ ╪┤╪»┘ç ╪¿╪º╪┤╪»╪î ┘à╪╣┘à┘ê┘ä╪º┘ï ╪╕╪▒┘ü ┘à╪»╪¬ ┌å┘å╪» ╪│╪º╪╣╪¬ ┘ê ┘å┘ç╪º█î╪¬╪º┘ï █╖█▓ ╪│╪º╪╣╪¬ ╪º╪▓ ╪╖╪▒┘ü ╪¿╪º┘å┌⌐ ┘à╪¿╪»╪º ╪¿┘ç ╪¡╪│╪º╪¿ ╪┤┘à╪º ╪¿╪º╪▓┌»╪▒╪»╪º┘å╪»┘ç ┘à█îΓÇî╪┤┘ê╪».
        </p>
        <a href="https://t.me/{escape(bot_username)}" class="btn btn-telegram py-2 px-4 rounded-3 text-decoration-none w-100">
            ╪¿╪º╪▓┌»╪┤╪¬ ╪¿┘ç ╪▒╪¿╪º╪¬ ╪¬┘ä┌»╪▒╪º┘à
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
    <title>╪¬╪▒╪º┌⌐┘å╪┤ ┘à┘ê┘ü┘é</title>
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
        <div class="icon-box">Γ£à</div>
        <h1 class="h4 mb-3 fw-bold text-success">┘╛╪▒╪»╪º╪«╪¬ ╪¿╪º ┘à┘ê┘ü┘é█î╪¬ ╪º┘å╪¼╪º┘à ╪┤╪»</h1>
        <p class="text-light mb-4">{escape(message)}</p>
        <a href="https://t.me/{escape(bot_username)}" class="btn btn-telegram py-2 px-4 rounded-3 text-decoration-none w-100">
            ╪¿╪º╪▓┌»╪┤╪¬ ╪¿┘ç ╪▒╪¿╪º╪¬ ╪¬┘ä┌»╪▒╪º┘à
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

    # 1. ArvanCloud (╪º╪¿╪▒ ╪ó╪▒┘ê╪º┘å)
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


@app.get("/ip/{token}", response_class=HTMLResponse)
async def user_dashboard_view(request: Request, token: str):
    """Renders the Shelter-styled user dashboard."""
    bot_user = await get_bot_username()
    token = token.strip()

    if not re.match(r"^[a-fA-F0-9-]{32,36}$", token):
        return _render_capture_ip_html("╪«╪╖╪º", "┘ä█î┘å┌⌐ ┘å╪º┘à╪╣╪¬╪¿╪▒ ╪º╪│╪¬", "╪│╪º╪«╪¬╪º╪▒ ╪¬┘ê┌⌐┘å ┘à╪╣╪¬╪¿╪▒ ┘å█î╪│╪¬.", False, bot_user)

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
            return _render_capture_ip_html("╪«╪╖╪º", "┘ä█î┘å┌⌐ ┘à┘å┘é╪╢█î █î╪º ┘å╪º┘à╪╣╪¬╪¿╪▒", "╪º█î┘å ╪º╪┤╪¬╪▒╪º┌⌐ █î╪º ╪¬┘ê┌⌐┘å █î╪º┘ü╪¬ ┘å╪┤╪».", False, bot_user)

        service = token_record.service
        now = datetime.now(timezone.utc)
        expires_at = token_record.expires_at if token_record.expires_at.tzinfo else token_record.expires_at.replace(tzinfo=timezone.utc)
        
        if now > expires_at:
            return _render_capture_ip_html("╪«╪╖╪º", "╪º┘å┘é╪╢╪º█î ╪¬┘ê┌⌐┘å", "┘à┘ç┘ä╪¬ ╪º╪│╪¬┘ü╪º╪»┘ç ╪º╪▓ ╪º█î┘å ┘ä█î┘å┌⌐ ╪¿┘ç ┘╛╪º█î╪º┘å ╪▒╪│█î╪»┘ç ╪º╪│╪¬. ╪º╪▓ ╪▒╪¿╪º╪¬ ┘ä█î┘å┌⌐ ╪¼╪»█î╪» ╪¿┌»█î╪▒█î╪».", False, bot_user)

        # Retrieve DNS IPs
        device_id = service.controld_device_id
        dns_ips = await get_controld_device_ips(device_id, settings) if device_id else {
            "ipv4_primary": "76.76.2.162",
            "ipv4_secondary": "76.76.10.162"
        }

        # Calculate time and dates
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
    """Validates Anti-VPN restrictions, registers the IP in Control D + AdGuard, and commits to PostgreSQL."""
    token = token.strip()
    client_ip, _ = get_client_real_ip(request)

    # 1. Anti-VPN / Iran Validation
    ip_check = await verify_user_ip(client_ip)
    if not ip_check.is_iran:
        return {
            "success": False,
            "message": ip_check.error_message or "┘ü█î┘ä╪¬╪▒╪┤┌⌐┘å ╪┤┘à╪º ╪▒┘ê╪┤┘å ╪º╪│╪¬! ┘ü┘é╪╖ ╪º╪¬╪╡╪º┘ä╪º╪¬ ┘à╪│╪¬┘é█î┘à ╪º█î╪▒╪º┘å ┘à╪¼╪º╪▓ ┘ç╪│╪¬┘å╪»."
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
            return {"success": False, "message": "╪º╪┤╪¬╪▒╪º┌⌐ █î╪º ╪¬┘ê┌⌐┘å ┘à╪╣╪¬╪¿╪▒ █î╪º┘ü╪¬ ┘å╪┤╪»."}

        now = datetime.now(timezone.utc)
        expires_at = token_record.expires_at if token_record.expires_at.tzinfo else token_record.expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            return {"success": False, "message": "╪º█î┘å ┘ä█î┘å┌⌐ ┘à┘å┘é╪╢█î ╪┤╪»┘ç ╪º╪│╪¬. ┘ä╪╖┘ü╪º┘ï ╪º╪▓ ╪╖╪▒█î┘é ╪▒╪¿╪º╪¬ ┘ä█î┘å┌⌐ ╪¼╪»█î╪»█î ╪»╪▒█î╪º┘ü╪¬ ┌⌐┘å█î╪»."}

        service = token_record.service

        # 2. Prevent duplicate hits
        if service.authorized_ip == client_ip:
            return {"success": True, "message": f"╪ó█îΓÇî┘╛█î {client_ip} ┘ç┘àΓÇî╪º┌⌐┘å┘ê┘å ╪▒┘ê█î ╪º╪┤╪¬╪▒╪º┌⌐ ╪┤┘à╪º ┘ü╪╣╪º┘ä ╪º╪│╪¬."}

        # 3. Safe multi-tenant deauthorization and authorization
        success = await update_device_ip_safe(session, service, client_ip)
        if success:
            return {"success": True, "message": f"╪ó█îΓÇî┘╛█î {client_ip} ╪¿╪º ┘à┘ê┘ü┘é█î╪¬ ╪¬╪º█î█î╪» ┘ê ╪▒┘ê█î ╪»█îΓÇî╪º┘åΓÇî╪º╪│ ╪º╪«╪¬╪╡╪º╪╡█î ╪┤┘à╪º ┘ü╪╣╪º┘ä ╪┤╪»."}
        else:
            return {"success": False, "message": "╪«╪╖╪º ╪»╪▒ ╪¬┘å╪╕█î┘à ╪»█îΓÇî╪º┘åΓÇî╪º╪│ ╪▒┘ê█î ╪│╪▒┘ê╪▒┘ç╪º. ┘ä╪╖┘ü╪º┘ï ┘ä╪¡╪╕╪º╪¬█î ╪»█î┌»╪▒ ╪¬┘ä╪º╪┤ ┌⌐┘å█î╪»."}

    if __name__ == "__main__":
        import uvicorn
        # Ensure this port matches what you had in run_web_ip_updater.py
        uvicorn.run("ip_server:app", host="127.0.0.1", port=8000, reload=False)
