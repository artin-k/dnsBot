# ip_server.py
import asyncio
import secrets
import logging
import re
import hmac
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from html import escape
import jdatetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, Form, HTTPException, status, Query, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import httpx
from sqlalchemy import select, or_
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

# 🔥 اتصال مجدد به فایل اصلی خودتان
from app.services.ip_manager import update_device_ip_safe

from bot.loader import create_bot
from bot.utils.auto_clean import schedule_message_deletion
from app.services.vpn_detector import verify_user_ip
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi import FastAPI, Request, Form, HTTPException, status, Query, Response
from fastapi.staticfiles import StaticFiles # ADD THIS LINE
from app.services.settings_service import AppSettingsService

app = FastAPI(title="Control D Auto-IP & Payment Gateway")
settings = get_settings()
bot = create_bot(settings)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")
WEB_SERVER_BASE_URL = settings.public_web_base_url

app = FastAPI(title="Control D Auto-IP & Payment Gateway")

# ADD THIS LINE TO SERVE IMAGES
app.mount("/static", StaticFiles(directory="static"), name="static")

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
def get_client_real_ip(request: Request) -> tuple[str, str | None]:
    headers = request.headers
    ar_ip = headers.get("ar-real-ip")
    ar_country = headers.get("ar-real-country") or headers.get("x-country-code")
    if ar_ip: return ar_ip.strip(), (ar_country.strip().upper() if ar_country else None)
    cf_ip = headers.get("cf-connecting-ip")
    cf_country = headers.get("cf-ipcountry")
    if cf_ip: return cf_ip.strip(), (cf_country.strip().upper() if cf_country else None)
    real_ip = headers.get("x-real-ip")
    if real_ip: return real_ip.strip(), None
    xff = headers.get("x-forwarded-for")
    if xff: return xff.split(",")[0].strip(), None
    return request.client.host if request.client else "127.0.0.1", None

def calculate_remaining_time_fa(expire_at: datetime | None) -> str:
    if not expire_at: return "نامحدود"
    now = datetime.now(timezone.utc)
    if expire_at.tzinfo is None: expire_at = expire_at.replace(tzinfo=timezone.utc)
    delta = expire_at - now
    total_seconds = delta.total_seconds()
    if total_seconds <= 0: return "پایان یافته"
    total_hours = int(total_seconds // 3600)
    if total_hours >= 24: return f"{total_hours // 24} روز"
    if total_hours > 0: return f"{total_hours} ساعت"
    return f"{int(total_seconds // 60)} دقیقه"

def _parse_purchase_metadata(raw_username: str | None) -> tuple[str, str, str | None]:
    if not raw_username: return "", "default", None
    if "|" not in raw_username: return raw_username, "default", None
    parts = raw_username.split("|")
    return parts[0], parts[1] if len(parts) > 1 else "default", parts[2] if len(parts) > 2 else None

async def get_controld_device_ips(device_id: str, settings_obj) -> dict:
    from app.config import SLOT_CONFIGS
    for config in SLOT_CONFIGS.values():
        if config["device_id"] == device_id:
            return {
                "ipv4_primary": config["dns_primary"], 
                "ipv4_secondary": config["dns_secondary"]
            }
    # Set your true default Control D slot IPs here instead of .162
    return {"ipv4_primary": "76.76.2.175", "ipv4_secondary": "76.76.10.175"}


def verify_admin_web_token(uid: int, token: str) -> bool:
    admin_ids = set(settings.admin_ids)
    if settings.root_admin_telegram_id is not None: admin_ids.add(settings.root_admin_telegram_id)
    if uid not in admin_ids: return False
    correct_token = hmac.new(settings.bot_token.encode('utf-8'), str(uid).encode('utf-8'), hashlib.sha256).hexdigest()
    return secrets.compare_digest(token, correct_token)

_bot_username = None
async def get_bot_username() -> str:
    global _bot_username
    if _bot_username is None:
        try: _bot_username = (await bot.get_me()).username
        except Exception: _bot_username = "PingSepBot"
    return _bot_username

def _render_capture_ip_html(title: str, heading: str, message: str, is_success: bool = False, client_ip: str | None = None, bot_username: str = "bot") -> HTMLResponse:
    icon_class = "success-icon" if is_success else "error-icon"
    icon = "✅" if is_success else "❌"
    ip_box = f'<div class="info-ip py-2 px-3 rounded-3 mb-4 text-center">{escape(client_ip)}</div>' if client_ip else ""
    html = f"""<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css" rel="stylesheet"><style>@import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;700&display=swap'); body {{ font-family: 'Vazirmatn', Tahoma, sans-serif; background-color: #0f172a; color: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }} .theme-card {{ background-color: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 40px; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.3); max-width: 550px; width: 100%; text-align: center; }} .icon-wrapper {{ width: 80px; height: 80px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 24px; font-size: 40px; }} .success-icon {{ background-color: rgba(16, 185, 129, 0.1); color: #10b981; border: 2px solid rgba(16, 185, 129, 0.2); }} .error-icon {{ background-color: rgba(239, 68, 68, 0.1); color: #ef4444; border: 2px solid rgba(239, 68, 68, 0.2); }} .info-ip {{ background-color: #0f172a; border: 1px solid #334155; font-family: monospace; font-size: 1.25rem; color: #38bdf8; }} .btn-home {{ background-color: #3b82f6; color: #ffffff; border: none; font-weight: bold; }}</style></head><body><div class="theme-card"><div class="icon-wrapper {icon_class}">{icon}</div><h1 class="h4 mb-3 fw-bold">{escape(heading)}</h1><p class="mb-4 text-secondary" style="font-size: 15px; line-height: 1.8;">{escape(message)}</p>{ip_box}<a href="https://t.me/{escape(bot_username)}" class="btn btn-home py-2 px-4 rounded-3 text-decoration-none d-inline-block">بازگشت به ربات تلگرام</a></div></body></html>"""
    return HTMLResponse(content=html)

def _render_vpn_detected_html(detected_ip: str, country: str, isp: str, error_message: str | None = None, bot_username: str = "bot") -> HTMLResponse:
    custom_msg = error_message or "آی‌پی شناسایی‌شده شما متعلق به سرور خارجی یا فیلترشکن است."
    html = f"""<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>فیلترشکن روشن است</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css" rel="stylesheet"><style>@import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;700&display=swap'); body {{ font-family: 'Vazirmatn', Tahoma, sans-serif; background-color: #0f172a; color: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }} .card-box {{ background-color: #1e293b; border: 2px solid #eab308; border-radius: 16px; padding: 36px; max-width: 540px; width: 100%; text-align: center; }} .icon-box {{ width: 75px; height: 75px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 20px; font-size: 38px; background-color: rgba(234, 179, 8, 0.12); border: 2px solid rgba(234, 179, 8, 0.3); }} .btn-reload {{ background-color: #eab308; color: #0f172a; font-weight: bold; border: none; }}</style></head><body><div class="card-box"><div class="icon-box">⚠️</div><h1 class="h4 mb-3 fw-bold text-warning">فیلترشکن شما روشن است!</h1><p class="text-light mb-3">{escape(custom_msg)}<br>ثبت آی‌پی فقط با اینترنت ایران امکان‌پذیر است.</p><button onclick="location.reload()" class="btn btn-reload py-2 px-4 rounded-3 w-100 mb-2">🔄 فیلترشکن را خاموش کردم، بررسی مجدد</button><a href="https://t.me/{escape(bot_username)}" class="btn btn-outline-secondary py-2 px-4 rounded-3 w-100 text-decoration-none">بازگشت به ربات</a></div></body></html>"""
    return HTMLResponse(content=html, status_code=200)

def _failed_html(reason: str, bot_username: str = "bot") -> HTMLResponse:
    html = f"""<html lang="fa" dir="rtl"><body style="background:#0f172a;color:#fff;text-align:center;padding:50px;font-family:Tahoma;"><h2 style="color:#ef4444;">❌ خطا</h2><p>{escape(reason)}</p><a href="https://t.me/{escape(bot_username)}" style="color:#3b82f6;">بازگشت به ربات</a></body></html>"""
    return HTMLResponse(content=html)

def _success_html(message: str, bot_username: str = "bot") -> HTMLResponse:
    html = f"""<html lang="fa" dir="rtl"><body style="background:#0f172a;color:#fff;text-align:center;padding:50px;font-family:Tahoma;"><h2 style="color:#10b981;">✅ موفق</h2><p>{escape(message)}</p><a href="https://t.me/{escape(bot_username)}" style="color:#3b82f6;">بازگشت به ربات</a></body></html>"""
    return HTMLResponse(content=html)


# ============================================================================
# ROOT ROUTES (Prevent 404 logs)
# ============================================================================
@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url=f"https://t.me/{await get_bot_username()}")

@app.get("/logo.jpg", include_in_schema=False)
async def serve_logo():
    import os
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    
    # Explicitly look inside the templates folder
    image_path = os.path.join("templates", "logo.jpg")
    
    if os.path.exists(image_path):
        return FileResponse(image_path)
    raise HTTPException(status_code=404, detail="Logo not found")

@app.get("/ping")
async def ping(): return JSONResponse(content={"status": "pong"}, headers={"Cache-Control": "no-store"})

# ============================================================================
# PAYSTAR REDIRECT & CALLBACK (Fixed Domain and Routing)
# ============================================================================
@app.get("/paystar/redirect/{token}", response_class=HTMLResponse)
@app.get("/paystar/redirect", response_class=HTMLResponse)
@app.get("/paystar/redirect/", response_class=HTMLResponse)
async def paystar_redirect(request: Request):
    bot_user = await get_bot_username()
    token = request.path_params.get("token") or request.query_params.get("token")
    if not token: return _failed_html("توکن پرداخت یافت نشد.", bot_user)

    try:
        clean_token = token.strip()
        async with async_session_maker() as session:
            payment = await PaymentsRepository(session).get_by_token_with_details(clean_token)
            if not payment or not payment.order or not payment.user: 
                return _failed_html("توکن معتبر نیست یا منقضی شده است.", bot_user)
            if payment.status == PaymentStatus.APPROVED.value or payment.order.status == OrderStatus.COMPLETED.value:
                return _success_html("این سفارش قبلاً پرداخت شده است.", bot_user)

        html_content = f"""<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="UTF-8"><meta name="referrer" content="origin" /><title>در حال انتقال...</title><style>body {{ font-family: Tahoma, sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; text-align: center; }} .card {{ background: #1e293b; border: 1px solid #334155; padding: 30px; border-radius: 16px; max-width: 400px; width: 90%; }} button {{ background-color: #3b82f6; color: #fff; border: none; border-radius: 8px; padding: 12px; font-weight: bold; width: 100%; cursor: pointer; margin-top: 15px; }}</style></head><body><div class="card"><h3>در حال انتقال به درگاه بانکی...</h3><p style="color: #94a3b8; font-size: 14px;">لطفاً چند لحظه صبر کنید.</p><form id="paymentForm" action="https://core.paystar.click/api/pardakht/payment" method="POST"><input type="hidden" name="token" value="{clean_token}"><button type="submit" id="fallbackBtn" style="display: none;">انتقال دستی به درگاه</button></form></div><script>document.addEventListener("DOMContentLoaded", function() {{ document.getElementById("paymentForm").submit(); setTimeout(function() {{ document.getElementById("fallbackBtn").style.display = "block"; }}, 2500); }});</script></body></html>"""
        return HTMLResponse(content=html_content)
    except Exception as exc: return _failed_html(f"خطای سرور: {str(exc)}", bot_user)

# ============================================================================
# PAYSTAR STATUS MESSAGES & GUIDED RESULT RENDERER
# ============================================================================

PAYSTAR_STATUS_DETAILS = {
    1: {
        "title": "پرداخت با موفقیت انجام شد",
        "type": "success",
        "icon": "✅",
        "color": "#10b981",
        "bg_glow": "rgba(16, 185, 129, 0.15)",
        "message": "تراکنش بانکی شما تایید و اشتراک DNS بلافاصله فعال شد.",
        "steps": [
            "مشخصات اتصال، آدرس‌های DNS و راهنمای راه‌اندازی به تلگرام شما ارسال شد.",
            "جهت ثبت خودکار آی‌پی یا تغییر لوکیشن، به ربات تلگرام مراجعه نمایید.",
        ],
    },
    -98: {
        "title": "انصراف از پرداخت",
        "type": "cancelled",
        "icon": "↩️",
        "color": "#f59e0b",
        "bg_glow": "rgba(245, 158, 11, 0.15)",
        "message": "فرآیند پرداخت در صفحه شاپرک توسط شما لغو شد.",
        "steps": [
            "هیچ وجهی از حساب بانکی شما کسر نشده است.",
            "برای تلاش مجدد، به ربات تلگرام بازگشته و مجدداً روی دکمه پرداخت آنلاین کلیک کنید.",
            "در صورت بروز مشکل، می‌توانید از روش پرداخت کارت‌به‌کارت نیز استفاده نمایید.",
        ],
    },
    -7: {
        "title": "مهلت پرداخت منقضی شد",
        "type": "failed",
        "icon": "⏳",
        "color": "#ef4444",
        "bg_glow": "rgba(239, 68, 68, 0.15)",
        "message": "زمان مجاز برای تکمیل تراکنش در درگاه شاپرک به پایان رسید.",
        "steps": [
            "اگر وجهی از حسابتان کسر شده باشد، حداکثر تا ۷۲ ساعت آینده توسط شبکه شاپرک عودت داده می‌شود.",
            "لطفاً به ربات تلگرام بازگشته و سفارش جدیدی ثبت نمایید.",
        ],
    },
    -8: {
        "title": "شماره کارت نامعتبر است",
        "type": "failed",
        "icon": "💳",
        "color": "#ef4444",
        "bg_glow": "rgba(239, 68, 68, 0.15)",
        "message": "پرداخت با کارت بانکی وارد شده مجاز نمی‌باشد.",
        "steps": [
            "لطفاً مطمئن شوید کارت عضو شتاب بوده و رمز پویای آن فعال است.",
            "به ربات برگردید و با کارت بانکی دیگری اقدام به خرید نمایید.",
        ],
    },
}


def _render_payment_result_html(
    *,
    status_type: str,  # "success" | "failed" | "cancelled" | "support"
    title: str,
    heading: str,
    description: str,
    guidance_steps: list[str],
    order_code: str | None = None,
    ref_num: str | None = None,
    amount_toman: int | None = None,
    bot_username: str = "bot",
) -> HTMLResponse:
    """Renders a responsive, modern dark-themed Persian payment receipt with specific next steps."""
    theme_colors = {
        "success": {"color": "#10b981", "bg": "rgba(16, 185, 129, 0.12)", "border": "#10b981", "icon": "✅"},
        "failed": {"color": "#ef4444", "bg": "rgba(239, 68, 68, 0.12)", "border": "#ef4444", "icon": "❌"},
        "cancelled": {"color": "#f59e0b", "bg": "rgba(245, 158, 11, 0.12)", "border": "#f59e0b", "icon": "⚠️"},
        "support": {"color": "#38bdf8", "bg": "rgba(56, 189, 248, 0.12)", "border": "#38bdf8", "icon": "🛠"},
    }
    t = theme_colors.get(status_type, theme_colors["failed"])

    # Details rows
    details_html = ""
    if order_code:
        details_html += f"""
        <div class="info-row">
            <span class="info-title">کد پیگیری سفارش:</span>
            <span class="info-val font-monospace">{escape(str(order_code))}</span>
        </div>"""
    if ref_num:
        details_html += f"""
        <div class="info-row">
            <span class="info-title">شماره مرجع بانکی (RefID):</span>
            <span class="info-val font-monospace">{escape(str(ref_num))}</span>
        </div>"""
    if amount_toman:
        details_html += f"""
        <div class="info-row">
            <span class="info-title">مبلغ پرداختی:</span>
            <span class="info-val text-warning fw-bold">{amount_toman:,} تومان</span>
        </div>"""

    # Guidance items
    steps_html = "".join([f"<li>{escape(step)}</li>" for step in guidance_steps])

    html_content = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;600;700;900&display=swap');
        :root {{
            --bg-dark: #090d16;
            --card-dark: #111827;
            --border-dark: #1f293d;
        }}
        body {{
            font-family: 'Vazirmatn', -apple-system, BlinkMacSystemFont, sans-serif;
            background-color: var(--bg-dark);
            color: #f9fafb;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 30px 14px;
            margin: 0;
        }}
        .receipt-card {{
            background-color: var(--card-dark);
            border: 1px solid var(--border-dark);
            border-top: 4px solid {t['border']};
            border-radius: 18px;
            padding: 32px 24px;
            max-width: 540px;
            width: 100%;
            box-shadow: 0 15px 35px -5px rgba(0, 0, 0, 0.6);
            text-align: center;
        }}
        .icon-circle {{
            width: 76px;
            height: 76px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 20px;
            font-size: 36px;
            background-color: {t['bg']};
            border: 2px solid {t['border']};
            box-shadow: 0 0 25px {t['bg']};
        }}
        .status-heading {{
            color: {t['color']};
            font-weight: 800;
            font-size: 1.45rem;
            margin-bottom: 12px;
        }}
        .status-desc {{
            color: #cbd5e1;
            font-size: 0.95rem;
            line-height: 1.8;
            margin-bottom: 24px;
        }}
        .details-box {{
            background: #090e1a;
            border: 1px solid var(--border-dark);
            border-radius: 12px;
            padding: 12px 18px;
            margin-bottom: 22px;
            text-align: right;
        }}
        .info-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            font-size: 0.9rem;
        }}
        .info-row:last-child {{
            border-bottom: none;
        }}
        .info-title {{
            color: #94a3b8;
        }}
        .info-val {{
            direction: ltr;
            font-weight: 600;
        }}
        .guidance-box {{
            background: rgba(255, 255, 255, 0.02);
            border: 1px dashed rgba(255, 255, 255, 0.12);
            border-radius: 12px;
            padding: 16px 18px;
            margin-bottom: 26px;
            text-align: right;
        }}
        .guidance-box h6 {{
            color: #94a3b8;
            font-size: 0.85rem;
            font-weight: 700;
            margin-bottom: 10px;
        }}
        .guidance-box ul {{
            margin: 0;
            padding-right: 18px;
            color: #cbd5e1;
            font-size: 0.88rem;
            line-height: 1.85;
        }}
        .btn-action {{
            background: linear-gradient(135deg, #2563eb, #1d4ed8);
            color: #ffffff;
            border: none;
            border-radius: 12px;
            padding: 14px 24px;
            font-weight: 700;
            font-size: 1.05rem;
            width: 100%;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            transition: all 0.2s ease;
        }}
        .btn-action:hover {{
            background: linear-gradient(135deg, #3b82f6, #2563eb);
            color: #ffffff;
            transform: translateY(-2px);
        }}
        .footer-brand {{
            margin-top: 24px;
            font-size: 0.8rem;
            letter-spacing: 5px;
            color: rgba(255, 255, 255, 0.3);
            text-transform: uppercase;
        }}
    </style>
</head>
<body>

<div class="receipt-card">
    <div class="icon-circle">
        {t['icon']}
    </div>

    <h4 class="status-heading">{escape(heading)}</h4>
    <p class="status-desc">{escape(description)}</p>

    {f'<div class="details-box">{details_html}</div>' if details_html else ''}

    <div class="guidance-box">
        <h6><i class="fa fa-info-circle me-1"></i> اقدامات بعدی و راهنمایی:</h6>
        <ul>{steps_html}</ul>
    </div>

    <a href="https://t.me/{escape(bot_username)}" class="btn-action">
        <i class="fab fa-telegram-plane"></i>
        <span>بازگشت به ربات تلگرام</span>
    </a>

    <div class="footer-brand">P i n g S e p . i r</div>
</div>

</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)


# ============================================================================
# REFACTORED PAYSTAR GATEWAY CALLBACK ROUTE
# ============================================================================

@app.api_route("/paystar/callback", methods=["GET", "POST"], response_class=HTMLResponse)
async def paystar_callback(request: Request):
    """Processes Paystar gateway redirect callback with robust verification,

    descriptive errors, and guided customer recovery paths.
    """
    bot_user = await get_bot_username()

    try:
        # Extract payload whether Paystar used GET or POST redirect
        if request.method.upper() == "POST":
            payload = await request.form()
        else:
            payload = request.query_params

        try:
            status_code = int(payload.get("status", 0))
        except (TypeError, ValueError):
            status_code = 0

        order_id = str(payload.get("order_id", "")).strip()
        ref_num = str(payload.get("ref_num", "")).strip()
        card_number = str(payload.get("card_number", "")).strip()
        tracking_code = str(payload.get("tracking_code", "")).strip()

        # ---------------------------------------------------------------------
        # Case 1: Callback parameters are incomplete
        # ---------------------------------------------------------------------
        if not order_id or not ref_num:
            return _render_payment_result_html(
                status_type="failed",
                title="اطلاعات ناقص",
                heading="اطلاعات بازگشتی از درگاه ناقص است",
                description="ارتباط با بانک برقرار شد، اما شناسه فاکتور یا شماره مرجع از شاپرک بازگردانده نشد.",
                guidance_steps=[
                    "اگر وجهی از حساب شما کسر شده باشد، نهایتاً تا ۷۲ ساعت آینده توسط شبکه شاپرک به حسابتان بازمی‌گردد.",
                    "می‌توانید به ربات تلگرام بازگشته و مجدداً جهت خرید اشتراک اقدام فرمایید.",
                    "در صورت بروز مشکل مداوم، به پشتیبانی تلگرام پیام دهید.",
                ],
                bot_username=bot_user,
            )

        # ---------------------------------------------------------------------
        # Case 2: Inspect Database Order and Payment
        # ---------------------------------------------------------------------
        async with async_session_maker() as session:
            order = await OrdersRepository(session).get_by_tracking_code_with_details(order_id)
            payment = order.payment if order else None

            if not order or not payment:
                return _render_payment_result_html(
                    status_type="failed",
                    title="سفارش یافت نشد",
                    heading="سفارش در سیستم یافت نشد",
                    description=f"سفارشی با کد «{order_id}» در پایگاه داده ربات وجود ندارد یا منقضی شده است.",
                    guidance_steps=[
                        "اگر وجهی کسر شده باشد، توسط بانک ظرف چند ساعت برگشت داده می‌شود.",
                        "لطفاً به ربات تلگرام مراجعه کرده و یک سفارش خرید جدید ثبت کنید.",
                    ],
                    order_code=order_id,
                    ref_num=ref_num,
                    bot_username=bot_user,
                )

            # -----------------------------------------------------------------
            # Case 3: Order was already approved (Idempotent Check)
            # -----------------------------------------------------------------
            if payment.status == PaymentStatus.APPROVED.value and order.status == OrderStatus.COMPLETED.value:
                return _render_payment_result_html(
                    status_type="success",
                    title="سفارش قبلاً فعال شده",
                    heading="این سفارش قبلاً تایید و فعال شده است",
                    description="پرداخت مربوط به این فاکتور پیش‌تر با موفقیت پردازش شده و سرویس شما فعال می‌باشد.",
                    guidance_steps=[
                        "برای مشاهده اطلاعات اشتراک و تنظیمات دی‌ان‌اس، وارد ربات تلگرام شوید.",
                        "در منوی ربات از گزینه «🛍 اشتراک‌های من و تغییر لوکیشن» استفاده نمایید.",
                    ],
                    order_code=order.tracking_code,
                    ref_num=payment.ref_id or ref_num,
                    amount_toman=order.amount,
                    bot_username=bot_user,
                )

            # -----------------------------------------------------------------
            # Case 4: User Cancelled or Bank Reported Failure
            # -----------------------------------------------------------------
            if status_code != 1:
                # Check for explicit user cancellation (Paystar status -98)
                if status_code == -98:
                    return _render_payment_result_html(
                        status_type="cancelled",
                        title="انصراف از پرداخت",
                        heading="پرداخت توسط شما لغو شد",
                        description="فرآیند پرداخت در درگاه شاپرک با درخواست شما متوقف گردید.",
                        guidance_steps=[
                            "هیچ وجهی از کارت بانکی شما کسر نشده است.",
                            "برای خرید اشتراک، به ربات بازگشته و مجدداً روی پرداخت آنلاین کلیک کنید.",
                            "در صورت تمایل، می‌توانید روش کارت‌به‌کارت را نیز در ربات انتخاب نمایید.",
                        ],
                        order_code=order.tracking_code,
                        bot_username=bot_user,
                    )

                # Other bank rejection codes
                reason_info = PAYSTAR_STATUS_DETAILS.get(status_code)
                reason_msg = (
                    reason_info["message"]
                    if reason_info
                    else f"پرداخت در شبکه بانکی ناموفق بود (کد خطای شاپرک: {status_code})."
                )

                return _render_payment_result_html(
                    status_type="failed",
                    title="تراکنش ناموفق",
                    heading="تراکنش بانکی تایید نشد",
                    description=reason_msg,
                    guidance_steps=[
                        "چنانچه وجهی از حسابتان کسر شده است، معمولاً ظرف چند دقیقه و نهایتاً تا ۷۲ ساعت از طرف بانک مبدا مسترد خواهد شد.",
                        "جهت خرید مجدد اشتراک، به ربات تلگرام بازگشته و دوباره اقدام فرمایید.",
                        "پیش از ورود مجدد به درگاه، از اتصال بدون فیلترشکن و صحیح بودن اطلاعات کارت اطمینان حاصل کنید.",
                    ],
                    order_code=order.tracking_code,
                    ref_num=ref_num,
                    amount_toman=order.amount,
                    bot_username=bot_user,
                )

            # -----------------------------------------------------------------
            # Case 5: Verify Payment with Paystar Bank API
            # -----------------------------------------------------------------
            paystar = PaystarService()
            try:
                is_verified = await paystar.verify_payment(
                    amount_toman=order.amount,
                    ref_num=ref_num,
                    card_number=card_number,
                    tracking_code=tracking_code,
                )
            except Exception as exc:
                logger.exception("paystar_verify_api_failed", order_id=order_id, error=str(exc))
                return _render_payment_result_html(
                    status_type="support",
                    title="خطا در تاییدیه بانک",
                    heading="تراکنش انجام شد اما استعلام بانک پاسخ نداد",
                    description="ارتباط با سرور پی‌استار جهت اعتبارسنجی نهایی با وقفه زمانی مواجه شد.",
                    guidance_steps=[
                        "مبلغ پرداختی نزد بانک محفوظ است و در صورت کسر شدن، حداکثر تا ۷۲ ساعت برگشت می‌خورد.",
                        "اگر وجه از حسابتان کسر شده، کد پیگیری و شماره مرجع زیر را برای پشتیبانی ربات بفرستید تا اشتراکتان دستی فعال شود.",
                    ],
                    order_code=order.tracking_code,
                    ref_num=ref_num,
                    amount_toman=order.amount,
                    bot_username=bot_user,
                )

            if not is_verified:
                return _render_payment_result_html(
                    status_type="failed",
                    title="تایید نشدن تراکنش",
                    heading="صحت تراکنش توسط شاپرک تایید نشد",
                    description="بانک تاییدیه نهایی انجام تراکنش را صادر نکرد و پرداخت معتبر تلقی نشد.",
                    guidance_steps=[
                        "اگر وجهی از حساب کسر شده باشد، ظرف ۷۲ ساعت توسط بانک عودت داده می‌شود.",
                        "جهت تلاش دوباره، به ربات برگشته و سفارش جدید ثبت کنید.",
                    ],
                    order_code=order.tracking_code,
                    ref_num=ref_num,
                    amount_toman=order.amount,
                    bot_username=bot_user,
                )

            # -----------------------------------------------------------------
            # Case 6: Approve and Provision Subscription
            # -----------------------------------------------------------------
            payment.method = "paystar"
            payment.ref_id = ref_num
            payment.authority = tracking_code or payment.authority

            payment_service = PaymentService(session, VPNPanelService(), settings)
            try:
                await payment_service.approve_payment(payment.id)
            except PaymentAlreadyProcessedError:
                pass  # Handled safely below
            except Exception as act_exc:
                logger.exception("payment_activation_after_bank_success_failed", order_id=order.id, error=str(act_exc))
                return _render_payment_result_html(
                    status_type="support",
                    title="نیاز به بررسی پشتیبانی",
                    heading="پرداخت با موفقیت انجام شد اما نیاز به ثبت دستی دارد",
                    description="بانک پرداخت شما را تایید کرده است، ولی در ثبت خودکار سرویس خطای فنی رخ داد.",
                    guidance_steps=[
                        "نـگـران نـبـاشـیـد! وجه با موفقیت پرداخت شده است و نیازی به پرداخت مجدد نیست.",
                        "کد سفارش و شماره مرجع زیر را کپی کرده و به پشتیبانی ربات تلگرام ارسال فرمایید.",
                        "پشتیبانی در اسرع وقت اشتراک شما را فعال خواهد کرد.",
                    ],
                    order_code=order.tracking_code,
                    ref_num=ref_num,
                    amount_toman=order.amount,
                    bot_username=bot_user,
                )

            # -----------------------------------------------------------------
            # Case 7: Send Delivery Card to Telegram (Safe Delivery)
            # -----------------------------------------------------------------
            service_stmt = (
                select(VPNService)
                .options(joinedload(VPNService.plan))
                .where(VPNService.order_id == order.id)
                .limit(1)
            )
            service_res = await session.execute(service_stmt)
            service = service_res.scalars().first()

            if service:
                try:
                    from app.services.controld import get_controld_device_ips
                    from bot.utils.messages import send_dns_delivery_card

                    ips = await get_controld_device_ips(service.controld_device_id, settings)
                    await send_dns_delivery_card(
                        bot=bot,
                        chat_id=order.user.telegram_id,
                        session=session,
                        service=service,
                        title_prefix="✅ <b>پرداخت آنلاین تایید و اشتراک فعال شد!</b>",
                        ipv4_primary=ips["ipv4_primary"],
                        ipv4_secondary=ips["ipv4_secondary"],
                        service_display="کل ترافیک اینترنت (Default)",
                        country_display="پیش‌فرض",
                        delay_seconds=7200,
                    )
                except Exception as tg_exc:
                    logger.warning("failed_to_send_delivery_card_to_telegram", error=str(tg_exc))

            # -----------------------------------------------------------------
            # Case 8: Success Response
            # -----------------------------------------------------------------
            return _render_payment_result_html(
                status_type="success",
                title="پرداخت موفقیت‌آمیز",
                heading="اشتراک شما با موفقیت فعال شد!",
                description="پرداخت با موفقیت تایید گردید و دسترسی DNS برای شما فعال شد.",
                guidance_steps=[
                    "مشخصات اتصال و DNSهای اختصاصی به چت تلگرام شما ارسال شد.",
                    "برای استفاده، به ربات برگشته و روی دکمه «ثبت آی‌پی» کلیک نمایید تا اتصال شما برقرار شود.",
                    "در صورت بروز هرگونه سوال یا راهنمایی، پشتیبانی آنلاین در دسترس شماست.",
                ],
                order_code=order.tracking_code,
                ref_num=ref_num,
                amount_toman=order.amount,
                bot_username=bot_user,
            )

    except Exception as global_exc:
        logger.exception("unhandled_callback_exception", error=str(global_exc))
        return _render_payment_result_html(
            status_type="failed",
            title="خطای پردازش",
            heading="خطای غیرمنتظره در پردازش پاسخ درگاه",
            description=f"خطایی در ثبت اطلاعات رخ داد: {escape(str(global_exc))}",
            guidance_steps=[
                "اگر مبلغی کسر شده باشد، به صورت خودکار توسط شاپرک تا ۷۲ ساعت آینده بازگشت داده می‌شود.",
                "برای بررسی وضعیت خرید خود به ربات مراجعه کنید.",
            ],
            bot_username=bot_user,
        )

# ============================================================================
# USER DASHBOARD (/ip/{token} and /capture-ip/{token})
# ============================================================================
@app.get("/capture-ip/{token}", response_class=HTMLResponse)
async def capture_ip(request: Request, token: str):
    """Old Legacy Route - Reverts exactly to how it was."""
    bot_user = await get_bot_username()
    token = token.strip()
    formatted_token = token
    try:
        if len(token) == 32: formatted_token = str(uuid.UUID(token))
    except ValueError: pass

    client_ip, _ = get_client_real_ip(request)
    ip_check = await verify_user_ip(client_ip)
    
    if not ip_check.is_iran:
        return _render_vpn_detected_html(client_ip, ip_check.country, ip_check.isp, ip_check.error_message, bot_user)

    async with async_session_maker() as session:
        stmt = select(IPAuthToken).options(joinedload(IPAuthToken.service).joinedload(VPNService.user)).where(or_(IPAuthToken.token == token, IPAuthToken.token == formatted_token)).limit(1)
        res = await session.execute(stmt)
        token_record = res.scalars().first()

        if not token_record or not token_record.service:
            return _render_capture_ip_html(
                "خطا", 
                "لینک نامعتبر", 
                "این لینک نامعتبر است (احتمالاً لینک جدیدی از ربات دریافت کرده‌اید). در ربات، وارد بخش «اشتراک‌های من» شوید، سپس روی «لینک‌های اتصال» بزنید. در پیام جدید ربات، گزینه «پنل مدیریت و ثبت آی‌پی» را انتخاب کنید.", 
                False, 
                bot_user
            )

        now = datetime.now(timezone.utc)
        expires_at = token_record.expires_at.replace(tzinfo=timezone.utc) if token_record.expires_at.tzinfo is None else token_record.expires_at
        if now > expires_at:
            return _render_capture_ip_html(
                "خطا", 
                "انقضای لینک", 
                "مهلت استفاده از این لینک گذشته است. در ربات، وارد بخش «اشتراک‌های من» شوید، سپس روی «لینک‌های اتصال» بزنید. در پیام جدید ربات، گزینه «پنل مدیریت و ثبت آی‌پی» را انتخاب کنید.", 
                False, 
                bot_user
            )
        service = token_record.service

        if service.authorized_ip == client_ip:
            return _render_capture_ip_html("ثبت موفق", "✅ آی‌پی شما فعال است!", f"آی‌پی ({client_ip}) از قبل ثبت شده است.", True, client_ip, bot_user)

        # 🔥 Calling ip_manager.py directly
        success = await update_device_ip_safe(session, service, client_ip)
        
        if success:
            return _render_capture_ip_html("ثبت موفق", "✅ ثبت آی‌پی با موفقیت انجام شد!", f"آی‌پی ({client_ip}) ثبت شد.", True, client_ip, bot_user)
        return _render_capture_ip_html("خطا", "خطای سرور", "خطا در ثبت دی‌ان‌اس.", False, bot_username=bot_user)

@app.get("/ip/{token}", response_class=HTMLResponse)
async def user_dashboard_view(request: Request, token: str):
    """Modern Dashboard Route"""
    bot_user = await get_bot_username()
    token = token.strip()
    formatted_token = token
    try:
        if len(token) == 32: formatted_token = str(uuid.UUID(token))
    except ValueError: pass

    client_ip, _ = get_client_real_ip(request)

    async with async_session_maker() as session:
        stmt = select(IPAuthToken).options(joinedload(IPAuthToken.service).joinedload(VPNService.user), joinedload(IPAuthToken.service).joinedload(VPNService.plan)).where(or_(IPAuthToken.token == token, IPAuthToken.token == formatted_token)).limit(1)
        res = await session.execute(stmt)
        token_record = res.scalars().first()

        if not token_record or not token_record.service:
            return _render_capture_ip_html("خطا", "لینک نامعتبر", "لینک منقضی یا یافت نشد.", False, bot_user)

        service = token_record.service
        now = datetime.now(timezone.utc)
        token_expires = token_record.expires_at.replace(tzinfo=timezone.utc) if token_record.expires_at.tzinfo is None else token_record.expires_at
        
        if now > token_expires:
            return _render_capture_ip_html(
                "خطا", 
                "انقضای توکن", 
                "مهلت استفاده از این لینک گذشته است. در ربات، وارد بخش «اشتراک‌های من» شوید، سپس روی «لینک‌های اتصال» بزنید. در پیام جدید ربات، گزینه «پنل مدیریت و ثبت آی‌پی» را انتخاب کنید.", 
                False, 
                bot_user
            )
    
        dns_ips = await get_controld_device_ips(service.controld_device_id, settings) if service.controld_device_id else {"ipv4_primary": "76.76.2.162", "ipv4_secondary": "76.76.10.162"}
        duration_text = calculate_remaining_time_fa(service.expire_at)
        
        try:
            tehran_tz = ZoneInfo("Asia/Tehran")
            expire_target = service.expire_at.replace(tzinfo=timezone.utc) if service.expire_at.tzinfo is None else service.expire_at
            shamsi_expire = jdatetime.datetime.fromgregorian(datetime=expire_target.astimezone(tehran_tz).replace(tzinfo=None)).strftime("%Y/%m/%d")
        except:
            shamsi_expire = "-"

        # Define is_active status first for cleaner logic
        is_active_status = service.status == "active" and (service.expire_at.replace(tzinfo=timezone.utc) if service.expire_at.tzinfo is None else service.expire_at) > now

        # Fetch AdGuard IPs dynamically from the database with your correct true fallbacks
        app_settings = AppSettingsService(session)
        db_adguard_primary = await app_settings.get_setting("adguard_primary_ip")
        db_adguard_secondary = await app_settings.get_setting("adguard_secondary_ip")
        
        # Fetch exact database settings using the explicit lowercase keys
        app_settings = AppSettingsService(session)
        # Direct repository fetch to bypass any service caching
        from app.repositories.settings import SettingsRepository
        settings_repo = SettingsRepository(session)
        
        row_primary = await settings_repo.get("adguard_primary_ip")
        row_secondary = await settings_repo.get("adguard_secondary_ip")
        
        ag_primary = row_primary.value if row_primary else "109.94.164.210"
        ag_secondary = row_secondary.value if row_secondary else "76.76.2.175"
                
        context = {
            "request": request,
            "token": token,
            "client_ip": client_ip,
            "bot_username": bot_user,
            "service": service,
            "user": service.user,
            "plan": service.plan,
            # Swap these so Control D device IPs map correctly to primary/secondary server slots
            "dns_primary": dns_ips["ipv4_primary"],
            "dns_secondary": dns_ips["ipv4_secondary"],
            "duration_text": duration_text,
            "shamsi_expire": shamsi_expire,
            "is_active": is_active_status,
            "is_ip_synced": (service.authorized_ip == client_ip),
            # AdGuard database settings
            "adguard_primary": ag_primary,
            "adguard_secondary": ag_secondary,
            "device_username": service.username.split('|')[0] if service.username else "کاربر",
            "subscription_status": "فعال" if is_active_status else "منقضی/غیرفعال"
        }
        return templates.TemplateResponse(request=request, name="user_panel.html", context=context)
    



@app.post("/api/ip/{token}/update")
async def api_update_ip(request: Request, token: str):
    """The AJAX route used by the User Panel."""
    token = token.strip()
    formatted_token = token
    try:
        if len(token) == 32: formatted_token = str(uuid.UUID(token))
    except ValueError: pass

    client_ip, _ = get_client_real_ip(request)
    ip_check = await verify_user_ip(client_ip)
    
    if not ip_check.is_iran:
        return JSONResponse(status_code=400, content={"success": False, "is_vpn": True, "message": ip_check.error_message or "فیلترشکن شما روشن است!"})

    async with async_session_maker() as session:
        stmt = select(IPAuthToken).options(joinedload(IPAuthToken.service)).where(or_(IPAuthToken.token == token, IPAuthToken.token == formatted_token)).limit(1)
        res = await session.execute(stmt)
        token_record = res.scalars().first()

        if not token_record or not token_record.service:
            return JSONResponse(status_code=404, content={"success": False, "message": "اشتراک یافت نشد."})

        now = datetime.now(timezone.utc)
        expires_at = token_record.expires_at if token_record.expires_at.tzinfo else token_record.expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            return JSONResponse(
                status_code=410, 
                content={
                    "success": False, 
                    "message": "لینک شما منقضی شده است. لطفاً از طریق ربات تلگرام (بخش اشتراک‌های من) یک لینک جدید دریافت کنید."
                }
            )
        service = token_record.service

        # 🔥 Firing your ip_manager directly
        success = await update_device_ip_safe(session, service, client_ip)
        
        if success:
            return JSONResponse(status_code=200, content={"success": True, "client_ip": client_ip, "message": f"آی‌پی {client_ip} با موفقیت روی سرورها فعال شد."})
        else:
            return JSONResponse(status_code=500, content={"success": False, "message": "خطا در تنظیم دی‌ان‌اس روی سرورها."})


# ============================================================================
# WEB ADMIN DASHBOARD
# ============================================================================
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, uid: int = Query(...), token: str = Query(...)):
    if not verify_admin_web_token(uid, token): raise HTTPException(status_code=403, detail="دسترسی غیرمجاز")
    async with async_session_maker() as session:
        raw_rows = await ServicesRepository(session).get_admin_dashboard_data()
        users = [{"telegram_id": r.telegram_id, "first_name": r.first_name, "service_id": r.service_id, "controld_device_id": r.controld_device_id, "authorized_ip": r.authorized_ip, "status": "فعال" if r.status == "active" else "منقضی شده"} for r in raw_rows]
    return templates.TemplateResponse(request=request, name="admin.html", context={"request": request, "users": users, "uid": uid, "token": token})

@app.post("/admin/delete-ip")
async def admin_delete_ip(uid: int = Query(...), token: str = Query(...), service_id: int = Form(...), device_id: str = Form(...), ip: str = Form(...)):
    if not verify_admin_web_token(uid, token): raise HTTPException(status_code=403, detail="دسترسی غیرمجاز")
    if not ip or ip == "ثبت نشده (No IP)": return RedirectResponse(url=f"/admin?uid={uid}&token={token}", status_code=303)
    await ControlDService(settings).deauthorize_ip(device_id, ip)
    async with async_session_maker() as session:
        service = (await session.execute(select(VPNService).where(VPNService.id == service_id).limit(1))).scalars().first()
        if service: service.authorized_ip = None; await session.commit()
    return RedirectResponse(url=f"/admin?uid={uid}&token={token}", status_code=303)

@app.post("/admin/add-ip")
async def admin_add_ip(uid: int = Query(...), token: str = Query(...), service_id: int = Form(...), new_ip: str = Form(...)):
    if not verify_admin_web_token(uid, token): raise HTTPException(status_code=403, detail="دسترسی غیرمجاز")
    async with async_session_maker() as session:
        service = (await session.execute(select(VPNService).where(VPNService.id == service_id).limit(1))).scalars().first()
        if service: await update_device_ip_safe(session, service, new_ip.strip())
    return RedirectResponse(url=f"/admin?uid={uid}&token={token}", status_code=303)


# ⚠️ THE INDENTATION FIX
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ip_server:app", host="127.0.0.1", port=8000, reload=False)