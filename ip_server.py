# ip_server.py
import os
from dotenv import load_dotenv
load_dotenv()

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
from bot.loader import create_bot
from app.services.vpn_detector import verify_user_ip

# 🔥 استفاده از ماژول قدرتمند خودتان برای آپدیت ادگارد و کنترل‌دی
from app.services.ip_manager import update_device_ip_safe

app = FastAPI(title="PingSep Web Server")
settings = get_settings()
bot = create_bot(settings)
logger = logging.getLogger(__name__)

WEB_SERVER_BASE_URL = settings.public_web_base_url

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
    html = f"""<html lang="fa" dir="rtl"><body style="background:#0f172a;color:#fff;text-align:center;padding:50px;font-family:Tahoma;"><h2 style="color:#ef4444;">❌ خطا</h2><p>{escape(reason)}</p><a href="https://t.me/{escape(bot_username)}" style="color:#3b82f6;">بازگشت به ربات</a></body></html>"""
    return HTMLResponse(content=html)

def _success_html(message: str, bot_username: str = "bot") -> HTMLResponse:
    html = f"""<html lang="fa" dir="rtl"><body style="background:#0f172a;color:#fff;text-align:center;padding:50px;font-family:Tahoma;"><h2 style="color:#10b981;">✅ موفق</h2><p>{escape(message)}</p><a href="https://t.me/{escape(bot_username)}" style="color:#3b82f6;">بازگشت به ربات</a></body></html>"""
    return HTMLResponse(content=html)

def _render_vpn_detected_html(detected_ip: str, country: str, isp: str, error_message: str | None = None, bot_username: str = "bot") -> HTMLResponse:
    custom_msg = error_message or "آی‌پی شناسایی‌شده شما متعلق به سرور خارجی یا فیلترشکن است."
    html = f"""<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>فیلترشکن روشن است</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css" rel="stylesheet"><style>@import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;700&display=swap'); body {{ font-family: 'Vazirmatn', Tahoma, sans-serif; background-color: #0f172a; color: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }} .card-box {{ background-color: #1e293b; border: 2px solid #eab308; border-radius: 16px; padding: 36px; max-width: 540px; width: 100%; text-align: center; }} .icon-box {{ width: 75px; height: 75px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 20px; font-size: 38px; background-color: rgba(234, 179, 8, 0.12); border: 2px solid rgba(234, 179, 8, 0.3); }} .btn-reload {{ background-color: #eab308; color: #0f172a; font-weight: bold; border: none; }}</style></head><body><div class="card-box"><div class="icon-box">⚠️</div><h1 class="h4 mb-3 fw-bold text-warning">فیلترشکن شما روشن است!</h1><p class="text-light mb-3">{escape(custom_msg)}<br>ثبت آی‌پی فقط با اینترنت ایران امکان‌پذیر است.</p><button onclick="location.reload()" class="btn btn-reload py-2 px-4 rounded-3 w-100 mb-2">🔄 فیلترشکن را خاموش کردم، بررسی مجدد</button><a href="https://t.me/{escape(bot_username)}" class="btn btn-outline-secondary py-2 px-4 rounded-3 w-100 text-decoration-none">بازگشت به ربات</a></div></body></html>"""
    return HTMLResponse(content=html, status_code=200)

# ============================================================================
# EMBEDDED DASHBOARD TEMPLATE (GUARANTEES NO "TEMPLATE NOT FOUND" ERRORS)
# ============================================================================
def _render_user_panel(context: dict) -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>پنل مدیریت دی‌ان‌اس | PingSep</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;600;700;900&display=swap');
        :root {{ --neon-green: #00e676; --neon-glow: rgba(0, 230, 118, 0.35); --bg-slate: #0f172a; --card-slate: rgba(30, 41, 59, 0.85); --border-slate: #334155; --text-muted: #94a3b8; }}
        body {{ background: radial-gradient(circle at 50% -20%, #112238 0%, var(--bg-slate) 80%); background-attachment: fixed; color: #f8fafc; font-family: 'Vazirmatn', Tahoma, sans-serif; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px 14px; margin: 0; position: relative; }}
        body::before {{ content: ""; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: linear-gradient(rgba(0, 230, 118, 0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(0, 230, 118, 0.03) 1px, transparent 1px); background-size: 36px 36px; pointer-events: none; z-index: -1; }}
        .dashboard-wrapper {{ position: relative; z-index: 1; width: 100%; max-width: 540px; }}
        .brand-header {{ text-align: center; margin-bottom: 24px; }}
        .brand-arrow {{ font-size: 45px; color: var(--neon-green); line-height: 1; text-shadow: 0 0 20px var(--neon-glow); }}
        .brand-title {{ font-size: 2rem; font-weight: 900; letter-spacing: 5px; color: #ffffff; margin-top: -6px; }}
        .glass-card {{ background: var(--card-slate); border: 1px solid var(--border-slate); border-radius: 16px; padding: 20px; backdrop-filter: blur(12px); box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4); margin-bottom: 16px; }}
        .ip-badge-box {{ display: flex; align-items: center; justify-content: space-between; }}
        .ip-val {{ font-family: monospace; font-size: 1.4rem; font-weight: 700; color: #38bdf8; direction: ltr; }}
        .info-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-bottom: 16px; }}
        .info-item {{ background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-slate); border-radius: 12px; padding: 14px 10px; text-align: center; }}
        .info-label {{ font-size: 0.8rem; color: var(--text-muted); margin-bottom: 4px; }}
        .info-val {{ font-weight: 700; font-size: 1rem; color: #ffffff; }}
        .badge-active {{ color: var(--neon-green) !important; text-shadow: 0 0 10px var(--neon-glow); }}
        .badge-expired {{ color: #ef4444 !important; }}
        .dns-row {{ background: rgba(15, 23, 42, 0.7); border: 1px dashed rgba(255, 255, 255, 0.1); border-radius: 10px; padding: 12px 14px; display: flex; align-items: center; justify-content: space-between; font-family: monospace; font-size: 1.1rem; margin-bottom: 10px; }}
        .dns-row:last-child {{ margin-bottom: 0; }}
        .btn-copy {{ background: rgba(0, 230, 118, 0.12); border: 1px solid rgba(0, 230, 118, 0.3); color: var(--neon-green); padding: 4px 14px; border-radius: 6px; font-size: 0.85rem; cursor: pointer; transition: all 0.2s; }}
        .btn-copy:hover {{ background: var(--neon-green); color: #000; }}
        .btn-update-ip {{ background: rgba(0, 230, 118, 0.15); border: 1.5px solid var(--neon-green); color: var(--neon-green); border-radius: 14px; padding: 14px; font-weight: 700; font-size: 1.05rem; width: 100%; cursor: pointer; transition: all 0.25s; display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 12px; }}
        .btn-update-ip:hover:not(:disabled) {{ background: var(--neon-green); color: #000000; box-shadow: 0 0 25px var(--neon-glow); }}
        .btn-update-ip:disabled {{ opacity: 0.6; cursor: not-allowed; }}
        .btn-return {{ background: transparent; border: 1px solid var(--border-slate); color: var(--text-muted); border-radius: 14px; padding: 12px; width: 100%; font-size: 0.95rem; text-decoration: none; display: flex; align-items: center; justify-content: center; gap: 8px; transition: all 0.2s; }}
        .btn-return:hover {{ background: rgba(255, 255, 255, 0.05); color: #fff; }}
    </style>
</head>
<body>
<div class="dashboard-wrapper">
    <div class="brand-header"><div class="brand-arrow">▲</div><div class="brand-title">PINGSEP</div></div>
    
    <div class="glass-card">
        <div class="ip-badge-box">
            <div>
                <div class="text-secondary small mb-1">آدرس اینترنت فعلی شما:</div>
                <div class="ip-val" id="displayIp">{context['client_ip']}</div>
            </div>
            <div>
                <span class="badge {'bg-success' if context['is_ip_synced'] else 'bg-warning text-dark'} p-2" id="syncBadge">
                    {'متصل و فعال' if context['is_ip_synced'] else 'نیاز به ثبت آی‌پی'}
                </span>
            </div>
        </div>
    </div>

    <div class="info-grid">
        <div class="info-item"><div class="info-label">دستگاه فعلی:</div><div class="info-val text-truncate px-1" id="deviceDisplay">در حال بررسی...</div></div>
        <div class="info-item"><div class="info-label">نام کاربری:</div><div class="info-val text-truncate px-1">{context['device_username']}</div></div>
        <div class="info-item"><div class="info-label">وضعیت اشتراک:</div><div class="info-val {'badge-active' if context['is_active'] else 'badge-expired'}">{context['subscription_status']}</div></div>
        <div class="info-item"><div class="info-label">اعتبار باقی‌مانده:</div><div class="info-val text-info">{context['duration_text']}</div></div>
    </div>

    <div class="glass-card">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <span class="small fw-bold text-light"><i class="fa fa-server text-success me-1"></i> دی‌ان‌اس اختصاصی شما:</span>
            <span class="small text-secondary" id="pingDisplay"><i class="fa fa-spinner fa-spin"></i> پینگ</span>
        </div>
        <div class="dns-row"><span>{context['dns_primary']}</span><button class="btn-copy" onclick="copyDns('{context['dns_primary']}')">کپی</button></div>
        <div class="dns-row mb-0"><span>{context['dns_secondary']}</span><button class="btn-copy" onclick="copyDns('{context['dns_secondary']}')">کپی</button></div>
    </div>

    <button class="btn-update-ip" id="updateBtn" onclick="submitIpUpdate()">
        <i class="fa fa-arrows-rotate" id="btnIcon"></i>
        <span>ثبت آی‌پی و فعال‌سازی دی‌ان‌اس</span>
    </button>
    
    <a href="https://t.me/{context['bot_username']}" class="btn-return mb-4">
        <i class="fa-brands fa-telegram"></i> بازگشت به ربات تلگرام
    </a>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
    const TOKEN = "{context['token']}";
    function detectDevice() {{
        const ua = navigator.userAgent; let os = "دستگاه ناشناخته";
        if (/windows/i.test(ua)) os = "ویندوز (PC)"; else if (/iphone|ipad|ipod/i.test(ua)) os = "آیفون (iOS)"; else if (/android/i.test(ua)) os = "اندروید"; else if (/macintosh|mac os x/i.test(ua)) os = "مک‌بوک (macOS)"; else if (/linux/i.test(ua)) os = "لینوکس"; else if (/playstation/i.test(ua)) os = "کنسول پلی‌استیشن";
        document.getElementById('deviceDisplay').innerText = os;
    }}
    async function checkPing() {{
        const start = performance.now();
        try {{
            const resp = await fetch('/ping?t=' + Date.now(), {{ cache: 'no-store' }});
            if (resp.ok) {{
                const latency = Math.round(performance.now() - start);
                let color = latency < 60 ? '#00e676' : (latency < 120 ? '#eab308' : '#ef4444');
                document.getElementById('pingDisplay').innerHTML = `<span style="color:${{color}}; font-weight:bold;">${{latency}} ms</span>`;
            }}
        }} catch {{ document.getElementById('pingDisplay').innerText = ''; }}
    }}
    function copyDns(text) {{
        navigator.clipboard.writeText(text);
        Swal.fire({{ toast: true, position: 'top-end', icon: 'success', title: 'کپی شد: ' + text, showConfirmButton: false, timer: 1500, background: '#1e293b', color: '#00e676' }});
    }}
    async function submitIpUpdate() {{
        const btn = document.getElementById('updateBtn'); const icon = document.getElementById('btnIcon');
        btn.disabled = true; icon.classList.add('fa-spin');
        try {{
            const resp = await fetch(`/api/ip/${{TOKEN}}/update`, {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }} }});
            const data = await resp.json();
            if (data.success) {{
                document.getElementById('displayIp').innerText = data.client_ip;
                const badge = document.getElementById('syncBadge'); badge.className = 'badge bg-success p-2'; badge.innerText = 'متصل و فعال';
                Swal.fire({{ icon: 'success', title: 'ثبت موفقیت‌آمیز', text: data.message, confirmButtonText: 'متوجه شدم', background: '#1e293b', color: '#ffffff', confirmButtonColor: '#00e676' }});
            }} else {{
                Swal.fire({{ icon: 'error', title: data.is_vpn ? 'فیلترشکن شما روشن است!' : 'خطا در ثبت', text: data.message, confirmButtonText: 'تلاش مجدد', background: '#1e293b', color: '#ffffff', confirmButtonColor: '#ef4444' }});
            }}
        }} catch (err) {{
            Swal.fire({{ icon: 'error', title: 'خطای ارتباطی', text: 'امکان اتصال به سرور جهت ثبت آی‌پی میسر نشد.', confirmButtonText: 'باشه', background: '#1e293b', color: '#ffffff' }});
        }} finally {{ btn.disabled = false; icon.classList.remove('fa-spin'); }}
    }}
    detectDevice(); checkPing(); setInterval(checkPing, 10000);
</script>
</body>
</html>"""
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

        html_content = f"""<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="UTF-8"><meta name="referrer" content="origin" /><title>در حال انتقال...</title><style>body {{ font-family: Tahoma, sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; text-align: center; }} .card {{ background: #1e293b; border: 1px solid #334155; padding: 30px; border-radius: 16px; max-width: 400px; width: 90%; }} button {{ background-color: #3b82f6; color: #fff; border: none; border-radius: 8px; padding: 12px; font-weight: bold; width: 100%; cursor: pointer; margin-top: 15px; }}</style></head><body><div class="card"><h3>در حال انتقال به درگاه بانکی...</h3><p style="color: #94a3b8; font-size: 14px;">لطفاً چند لحظه صبر کنید.</p><form id="paymentForm" action="https://core.paystar.click/api/pardakht/payment" method="POST"><input type="hidden" name="token" value="{clean_token}"><button type="submit" id="fallbackBtn" style="display: none;">انتقال دستی به درگاه</button></form></div><script>document.addEventListener("DOMContentLoaded", function() {{ document.getElementById("paymentForm").submit(); setTimeout(function() {{ document.getElementById("fallbackBtn").style.display = "block"; }}, 2500); }});</script></body></html>"""
        return HTMLResponse(content=html_content)
    except Exception as exc: return _failed_html(f"خطای سرور: {str(exc)}", bot_user)

@app.api_route("/paystar/callback", methods=["GET", "POST"], response_class=HTMLResponse)
async def paystar_callback(request: Request):
    bot_user = await get_bot_username()
    try:
        payload = await request.form() if request.method.upper() == "POST" else request.query_params
        status_code = int(payload.get("status", 0))
        order_id = str(payload.get("order_id", "")).strip()
        ref_num = str(payload.get("ref_num", "")).strip()

        if not order_id or not ref_num: return _failed_html("اطلاعات درگاه ناقص است.", bot_username=bot_user)

        async with async_session_maker() as session:
            order = await OrdersRepository(session).get_by_tracking_code_with_details(order_id)
            payment = order.payment if order else None
            if not order or not payment: return _failed_html("سفارش در سیستم یافت نشد.", bot_username=bot_user)
            
            if payment.status == PaymentStatus.APPROVED.value:
                return _success_html("این سفارش قبلاً تایید شده است.", bot_username=bot_user)

            if status_code != 1:
                return _failed_html(f"تراکنش ناموفق بود (کد وضعیت: {status_code}).", bot_username=bot_user)

            is_verified = await PaystarService().verify_payment(order.amount, ref_num, str(payload.get("card_number", "")), str(payload.get("tracking_code", "")))
            if not is_verified: return _failed_html("تراکنش در شبکه بانکی تایید نشد.", bot_username=bot_user)

            payment.method, payment.ref_id = "paystar", ref_num
            try:
                await PaymentService(session, VPNPanelService(), settings).approve_payment(payment.id)
            except Exception as e:
                return _failed_html(f"خطا در فعال‌سازی: {str(e)}", bot_username=bot_user)

            # Send Message safely
            try:
                from bot.utils.messages import send_dns_delivery_card
                service_stmt = select(VPNService).where(VPNService.order_id == order.id).limit(1)
                service = (await session.execute(service_stmt)).scalars().first()
                if service:
                    ips = await get_controld_device_ips(service.controld_device_id, settings)
                    await send_dns_delivery_card(bot=bot, chat_id=order.user.telegram_id, session=session, service=service, title_prefix="✅ <b>پرداخت تایید شد!</b>", ipv4_primary=ips["ipv4_primary"], ipv4_secondary=ips["ipv4_secondary"], service_display="کل ترافیک اینترنت", country_display="پیش‌فرض", delay_seconds=7200)
            except Exception: pass

            return _success_html(f"سفارش {order.tracking_code} با موفقیت تایید شد.", bot_username=bot_user)
    except Exception as e:
        return _failed_html(f"خطای سیستم: {str(e)}", bot_username=bot_user)

# ============================================================================
# USER DASHBOARD (/ip/{token}) - DIRECT RENDER
# ============================================================================
@app.get("/ip/{token}", response_class=HTMLResponse)
@app.get("/capture-ip/{token}", response_class=HTMLResponse)
async def capture_or_dashboard_ip(request: Request, token: str):
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
        stmt = select(IPAuthToken).options(joinedload(IPAuthToken.service).joinedload(VPNService.user), joinedload(IPAuthToken.service).joinedload(VPNService.plan)).where(or_(IPAuthToken.token == token, IPAuthToken.token == formatted_token)).limit(1)
        token_record = (await session.execute(stmt)).scalars().first()

        if not token_record or not token_record.service:
            return _failed_html("این لینک یافت نشد یا منقضی شده است.", bot_user)

        service = token_record.service
        now = datetime.now(timezone.utc)
        token_expires = token_record.expires_at.replace(tzinfo=timezone.utc) if token_record.expires_at.tzinfo is None else token_record.expires_at
        
        if now > token_expires:
            return _failed_html("مهلت استفاده از این لینک به پایان رسیده است. از ربات لینک جدید بگیرید.", bot_user)

        service_expires = service.expire_at.replace(tzinfo=timezone.utc) if service.expire_at and service.expire_at.tzinfo is None else service.expire_at
        is_active = (service.status == "active") and (service_expires is None or service_expires > now)
        
        dns_ips = await get_controld_device_ips(service.controld_device_id, settings) if service.controld_device_id else {"ipv4_primary": "76.76.2.162", "ipv4_secondary": "76.76.10.162"}
        user_name = service.user.first_name or service.user.username or f"کاربر {service.user.telegram_id}" if service.user else "کاربر گرامی"
        
        context = {
            "token": token, "client_ip": client_ip, "bot_username": bot_user,
            "user_name": user_name, "device_username": (service.username or "user").split("|")[0],
            "subscription_status": "فعال" if is_active else "منقضی شده", "is_active": is_active,
            "duration_text": calculate_remaining_time_fa(service.expire_at),
            "dns_primary": dns_ips["ipv4_primary"], "dns_secondary": dns_ips["ipv4_secondary"],
            "is_ip_synced": (service.authorized_ip == client_ip),
        }
        
        # 🔥 Using the embedded HTML dashboard guarantees no missing file errors!
        return _render_user_panel(context)


# ============================================================================
# API POST ROUTE (/api/ip/update) - USING ip_manager.py
# ============================================================================
@app.post("/api/ip/{token}/update")
async def api_update_ip(request: Request, token: str):
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
        token_record = (await session.execute(stmt)).scalars().first()

        if not token_record or not token_record.service: 
            return JSONResponse(status_code=404, content={"success": False, "message": "اشتراک یافت نشد."})
        
        now = datetime.now(timezone.utc)
        expires_at = token_record.expires_at if token_record.expires_at.tzinfo else token_record.expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at: 
            return JSONResponse(status_code=410, content={"success": False, "message": "لینک منقضی شده است."})

        service = token_record.service

        # 🔥 We returned to your powerful ip_manager logic here!
        success = await update_device_ip_safe(session, service, client_ip)
        
        if success:
            return JSONResponse(status_code=200, content={"success": True, "client_ip": client_ip, "message": f"آی‌پی {client_ip} با موفقیت در سیستم ثبت شد."})
        else:
            return JSONResponse(status_code=500, content={"success": False, "message": "خطا در تنظیم دی‌ان‌اس روی سرورها."})