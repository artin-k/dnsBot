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

from app.config import get_settings, SLOT_CONFIGS
from app.database import async_session_maker
from app.models import IPAuthToken, Order, Payment, VPNService, OrderStatus, PaymentStatus
from app.repositories.orders import OrdersRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.services import ServicesRepository
from app.services.controld import ControlDService
from app.services.payment_service import PaymentApprovalError, PaymentAlreadyProcessedError, PaymentExpiredError, PaymentService
from app.services.vpn_panel import VPNPanelService
from app.services.paystar import PaystarService
from app.services.ip_manager import update_device_ip_safe
from bot.loader import create_bot
from app.services.vpn_detector import verify_user_ip

app = FastAPI(title="PingSep Web Server")
settings = get_settings()
bot = create_bot(settings)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")

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
    if delta.total_seconds() <= 0: return "پایان یافته"
    total_hours = int(delta.total_seconds() // 3600)
    if total_hours >= 24: return f"{total_hours // 24} روز"
    if total_hours > 0: return f"{total_hours} ساعت"
    return f"{int(delta.total_seconds() // 60)} دقیقه"

async def get_controld_device_ips(device_id: str, settings_obj) -> dict:
    for config in SLOT_CONFIGS.values():
        if config["device_id"] == device_id:
            return {"ipv4_primary": config["dns_primary"], "ipv4_secondary": config["dns_secondary"]}
    return {"ipv4_primary": "76.76.2.162", "ipv4_secondary": "76.76.10.162"}

_bot_username = None
async def get_bot_username() -> str:
    global _bot_username
    if _bot_username is None:
        try: _bot_username = (await bot.get_me()).username
        except Exception: _bot_username = "PingSepBot"
    return _bot_username

def _failed_html(reason: str, bot_username: str = "bot") -> HTMLResponse:
    html = f"""<html lang="fa" dir="rtl"><body style="background:#0f172a;color:#fff;text-align:center;padding:50px;font-family:Tahoma;">
    <h2 style="color:#ef4444;">❌ خطا</h2><p>{escape(reason)}</p>
    <a href="https://t.me/{escape(bot_username)}" style="color:#3b82f6;">بازگشت به ربات</a></body></html>"""
    return HTMLResponse(content=html)

def _success_html(message: str, bot_username: str = "bot") -> HTMLResponse:
    html = f"""<html lang="fa" dir="rtl"><body style="background:#0f172a;color:#fff;text-align:center;padding:50px;font-family:Tahoma;">
    <h2 style="color:#10b981;">✅ موفق</h2><p>{escape(message)}</p>
    <a href="https://t.me/{escape(bot_username)}" style="color:#3b82f6;">بازگشت به ربات</a></body></html>"""
    return HTMLResponse(content=html)


# ============================================================================
# ROOT ROUTES 
# ============================================================================
@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url=f"https://t.me/{await get_bot_username()}")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon(): return Response(status_code=204)

@app.get("/ping")
async def ping(): return JSONResponse(content={"status": "pong"}, headers={"Cache-Control": "no-store"})

# ============================================================================
# PAYSTAR REDIRECT & CALLBACK
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

        html_content = f"""<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="UTF-8"><meta name="referrer" content="origin" /><title>در حال انتقال...</title>
        <style>body {{ font-family: Tahoma, sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; text-align: center; }} .card {{ background: #1e293b; border: 1px solid #334155; padding: 30px; border-radius: 16px; max-width: 400px; width: 90%; }} button {{ background-color: #3b82f6; color: #fff; border: none; border-radius: 8px; padding: 12px; font-weight: bold; width: 100%; cursor: pointer; margin-top: 15px; }}</style></head>
        <body><div class="card"><h3>در حال انتقال به درگاه بانکی...</h3><p style="color: #94a3b8; font-size: 14px;">لطفاً چند لحظه صبر کنید.</p>
        <form id="paymentForm" action="https://core.paystar.click/api/pardakht/payment" method="POST"><input type="hidden" name="token" value="{clean_token}"><button type="submit" id="fallbackBtn" style="display: none;">انتقال دستی به درگاه</button></form>
        </div><script>document.addEventListener("DOMContentLoaded", function() {{ document.getElementById("paymentForm").submit(); setTimeout(function() {{ document.getElementById("fallbackBtn").style.display = "block"; }}, 2500); }});</script></body></html>"""
        return HTMLResponse(content=html_content)
    except Exception as exc: return _failed_html(f"خطای سرور: {str(exc)}", bot_user)

# ============================================================================
# USER DASHBOARD (/ip/{token})
# ============================================================================
@app.get("/ip/{token}", response_class=HTMLResponse)
@app.get("/capture-ip/{token}", response_class=HTMLResponse)
async def user_dashboard_view(request: Request, token: str):
    bot_user = await get_bot_username()
    token = token.strip()
    formatted_token = token
    try:
        if len(token) == 32: formatted_token = str(uuid.UUID(token))
    except ValueError: pass

    client_ip, _ = get_client_real_ip(request)

    async with async_session_maker() as session:
        stmt = select(IPAuthToken).options(joinedload(IPAuthToken.service).joinedload(VPNService.user), joinedload(IPAuthToken.service).joinedload(VPNService.plan)).where(or_(IPAuthToken.token == token, IPAuthToken.token == formatted_token)).limit(1)
        token_record = (await session.execute(stmt)).scalars().first()

        if not token_record or not token_record.service: return _failed_html("این لینک یافت نشد یا منقضی شده است.", bot_user)

        service = token_record.service
        now = datetime.now(timezone.utc)
        token_expires = token_record.expires_at.replace(tzinfo=timezone.utc) if token_record.expires_at.tzinfo is None else token_record.expires_at
        if now > token_expires: return _failed_html("مهلت استفاده از این لینک به پایان رسیده است. از ربات لینک جدید بگیرید.", bot_user)

        service_expires = service.expire_at.replace(tzinfo=timezone.utc) if service.expire_at and service.expire_at.tzinfo is None else service.expire_at
        is_active = (service.status == "active") and (service_expires is None or service_expires > now)
        
        dns_ips = await get_controld_device_ips(service.controld_device_id, settings) if service.controld_device_id else {"ipv4_primary": "76.76.2.162", "ipv4_secondary": "76.76.10.162"}
        user_name = service.user.first_name or service.user.username or f"کاربر {service.user.telegram_id}" if service.user else "کاربر گرامی"
        
        context = {
            "request": request, "token": token, "client_ip": client_ip, "bot_username": bot_user,
            "user_name": user_name, "device_username": (service.username or "user").split("|")[0],
            "subscription_status": "فعال" if is_active else "منقضی شده", "is_active": is_active,
            "duration_text": calculate_remaining_time_fa(service.expire_at),
            "dns_primary": dns_ips["ipv4_primary"], "dns_secondary": dns_ips["ipv4_secondary"],
            "is_ip_synced": (service.authorized_ip == client_ip),
        }
        return templates.TemplateResponse("user_panel.html", context)

# ============================================================================
# API POST ROUTE (/api/ip/update)
# ============================================================================
@app.post("/api/ip/{token}/update")
async def api_update_ip(request: Request, token: str):
    """
    🔥 FIXED: Removed the short-circuit. It ALWAYS calls update_device_ip_safe to force sync.
    """
    token = token.strip()
    formatted_token = token
    try:
        if len(token) == 32: formatted_token = str(uuid.UUID(token))
    except ValueError: pass

    client_ip, _ = get_client_real_ip(request)
    ip_check = await verify_user_ip(client_ip)
    
    if not ip_check.is_iran:
        return JSONResponse(status_code=400, content={"success": False, "is_vpn": True, "message": ip_check.error_message or "فیلترشکن شما روشن است! لطفاً خاموش کنید."})

    async with async_session_maker() as session:
        stmt = select(IPAuthToken).options(joinedload(IPAuthToken.service)).where(or_(IPAuthToken.token == token, IPAuthToken.token == formatted_token)).limit(1)
        token_record = (await session.execute(stmt)).scalars().first()

        if not token_record or not token_record.service: 
            return JSONResponse(status_code=404, content={"success": False, "message": "اشتراک یافت نشد."})
        
        now = datetime.now(timezone.utc)
        expires_at = token_record.expires_at if token_record.expires_at.tzinfo else token_record.expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at: 
            return JSONResponse(status_code=410, content={"success": False, "message": "لینک منقضی شده است."})

        service = token_record.service

        # 🔥 REMOVED THE FAST-RETURN BUG HERE. IT WILL ALWAYS SYNC TO CONTROLD!
        success = await update_device_ip_safe(session, service, client_ip)
        
        if success: 
            return {"success": True, "client_ip": client_ip, "message": f"آی‌پی {client_ip} با موفقیت در سیستم و سرور ثبت شد."}
        else: 
            return JSONResponse(status_code=500, content={"success": False, "message": "خطا در تنظیم دی‌ان‌اس روی سرورها."})