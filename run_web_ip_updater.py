# run_web_ip_updater.py
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
from app.models import Order, Payment, VPNService, OrderStatus, PaymentStatus, User
from app.repositories.orders import OrdersRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.services import ServicesRepository
from app.services.controld import create_dns_device, ControlDService
from app.services.payment_service import PaymentApprovalError, PaymentAlreadyProcessedError, PaymentExpiredError, PaymentService
from app.services.vpn_panel import VPNPanelService
from app.services.paystar import PaystarService
from app.services.ip_manager import update_device_ip_safe
from bot.loader import create_bot
from ip_server import _apply_purchase_route, _build_paystar_context, _render_paystar_success_html, _send_paystar_success_message, _success_html

app = FastAPI(title="Control D Auto-IP & Payment Gateway")
settings = get_settings()
bot = create_bot(settings)
logger = logging.getLogger(__name__)

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
                    "ipv4_primary": v4_list[0] if len(v4_list) > 0 else "94.183.166.203",
                    "ipv4_secondary": v4_list[1] if len(v4_list) > 1 else "94.183.166.208",
                }
        except Exception:
            pass
    return {
        "ipv4_primary": "94.183.166.203",
        "ipv4_secondary": "94.183.166.208",
    }


def verify_admin_web_token(uid: int, token: str) -> bool:
    """
    Verifies that the incoming web request is initiated by a verified Admin 
    listed in your .env settings, using a secure HMAC-SHA256 signature [cite: 3.4.1, 1].
    """
    admin_ids = set(settings.admin_ids)
    if settings.root_admin_telegram_id is not None:
        admin_ids.add(settings.root_admin_telegram_id)
        
    if uid not in admin_ids:
        return False
        
    # Re-compute the correct secure token
    correct_token = hmac.new(
        settings.bot_token.encode('utf-8'),
        str(uid).encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return secrets.compare_digest(token, correct_token)


# ============================================================================
# AUTO-REGISTRATION ENDPOINT
# ============================================================================

@app.get("/update-ip/{device_id}", response_class=HTMLResponse)
async def update_device_ip(request: Request, device_id: str):
    client_ip = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or request.client.host
    if client_ip and "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()

    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", client_ip):
        return _failed_html("فرمت آی‌پی شناسایی‌شده نامعتبر است.")

    async with async_session_maker() as session:
        stmt = select(VPNService).where(VPNService.controld_device_id == device_id, VPNService.status == "active").limit(1)
        res = await session.execute(stmt)
        service = res.scalars().first()
        
        if not service:
            return _failed_html("اشتراک فعال متناظر با این دستگاه یافت نشد.")

        success = await update_device_ip_safe(session, service, client_ip)
        
        if success:
            return f"""
            <html>
            <head>
                <meta charset="utf-8">
                <title>ثبت آی‌پی موفقیت‌آمیز</title>
                <style>
                    body {{ font-family: Tahoma, Arial, sans-serif; background-color: #f4f6f9; text-align: center; padding: 50px; direction: rtl; }}
                    .card {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); display: inline-block; max-width: 500px; }}
                    h1 {{ color: #2ecc71; font-size: 22px; }}
                    p {{ color: #333; font-size: 16px; line-height: 1.8; }}
                    .ip-box {{ background: #f8f9fa; border: 1px solid #e2e8f0; font-family: monospace; font-size: 20px; font-weight: bold; padding: 10px; border-radius: 6px; margin: 15px 0; color: #2d3748; }}
                </style>
            </head>
            <body>
                <div class="card">
                    <h1>✅ ثبت آی‌پی با موفقیت انجام شد!</h1>
                    <p>آی‌پی شناسایی‌شده شما:</p>
                    <div class="ip-box">{escape(client_ip)}</div>
                    <p>اکنون می‌توانید بدون نیاز به فیلترشکن از دی‌ان‌اس اختصاصی خود روی دستگاه خود استفاده کنید.</p>
                </div>
            </body>
            </html>
            """
        else:
            return _failed_html("خطا در ثبت لوکیشن و آی‌پی جدید در پنل Control D. لطفاً مجدداً تلاش کنید.")


# ============================================================================
# WEB ADMIN DASHBOARD
# ============================================================================

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, uid: int = Query(...), token: str = Query(...)):
    """Secure passwordless admin entrypoint verifying the Telegram token [cite: 3.4.1]."""
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
                    shamsi_expire = jdatetime.datetime.fromgregorian(datetime=naive_tehran).strftime("%Y/%m/%d - %H:%M:%S")
                except Exception:
                    shamsi_expire = tehran_expire.strftime("%Y-%m-%d %H:%M:%S")

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
    """Deauthorizes an IP address after verifying the secure payload token [cite: services.py, 1]."""
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
    """Overrides an IP address securely after verifying the token [cite: services.py, 1]."""
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

@app.get("/paystar/redirect", response_class=HTMLResponse)
async def paystar_redirect(token: str):
    async with async_session_maker() as session:
        payment = await PaymentsRepository(session).get_by_token_with_details(token)
        if payment is None or payment.order is None or payment.user is None:
            return _failed_html("توکن پرداخت معتبر نیست.")
        if payment.method != "paystar":
            return _failed_html("این لینک برای پرداخت آنلاین ثبت نشده است.")
        if payment.status == PaymentStatus.APPROVED.value or payment.order.status == OrderStatus.COMPLETED.value:
            return _success_html("این سفارش قبلاً با موفقیت نهایی شده است.")

    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>در حال انتقال به درگاه بانکی...</title>
        <script>
            window.onload = function() {{
                document.getElementById('paystar_form').submit();
            }};
        </script>
    </head>
    <body>
        <div style="text-align: center; margin-top: 100px; font-family: Tahoma, sans-serif;">
            <h3>در حال انتقال به درگاه پرداخت بانکی شاپرک...</h3>
            <p>لطفاً شکیبا باشید.</p>
            <form id="paystar_form" action="https://core.paystar.ir/api/pardakht/payment" method="POST">
                <input type="hidden" name="token" value="{token}" />
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# ============================================================================
# PAYSTAR GATEWAY CALLBACK
# ============================================================================

@app.api_route("/paystar/callback", methods=["GET", "POST"], response_class=HTMLResponse)
async def paystar_callback(request: Request):
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