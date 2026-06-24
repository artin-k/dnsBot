# ip_server.py
import secrets
from datetime import datetime, timezone, timedelta
from html import escape
import jdatetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
import httpx
from sqlalchemy import select

# --- FIXED: Imported required keyboard builders and buttons ---
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from app.config import get_settings
from app.database import async_session_maker
from app.models import Order, Payment, VPNService, OrderStatus, PaymentStatus, OrderKind
from app.services.controld import create_dns_device, ControlDService, get_country_name_fa, get_flag_emoji
from app.services.paystar import PaystarService  # Import Paystar
from bot.loader import create_bot  # To send bot messages directly

app = FastAPI()
settings = get_settings()
bot = create_bot(settings)

# ============================================================================
# CONFIGURATION (No trailing slash)
# ============================================================================
# --- FIXED: Defined the VPS base URL for IP registration buttons ---
# In buy.py and ip_server.py
# Using the raw VPS IP on port 80 (Nginx handles the forwarding) [cite: 1]
WEB_SERVER_BASE_URL = "http://82.115.24.241"


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


@app.get("/update-ip/{device_id}", response_class=HTMLResponse)
async def update_device_ip(request: Request, device_id: str):
    client_ip = request.headers.get("x-forwarded-for") or request.client.host
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
            "name": "Auto Registered"
        }

        try:
            response = await client.post(f"{access_url}?device_id={device_id}", json=payload, headers=headers, timeout=10.0)
            if response.status_code in (200, 201):
                return """
                <html>
                <head>
                    <meta charset="utf-8">
                    <title>ثبت آی‌پی موفقیت‌آمیز</title>
                    <style>
                        body { font-family: Tahoma, Arial, sans-serif; background-color: #f4f6f9; text-align: center; padding: 50px; direction: rtr; }
                        .card { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); display: inline-block; }
                        h1 { color: #2ecc71; }
                        p { color: #333; font-size: 18px; }
                    </style>
                </head>
                <body>
                    <div class="card">
                        <h1>✅ ثبت آی‌پی با موفقیت انجام شد!</h1>
                        <p>آی‌پی شناسایی‌شده شما: <b>""" + client_ip + """</b></p>
                        <p>اکنون می‌توانید بدون نیاز به فیلترشکن از دی‌ان‌اس اختصاصی خود روی دستگاه خود استفاده کنید.</p>
                    </div>
                </body>
                </html>
                """
            else:
                return f"<h3>خطا در ثبت آی‌پی در پنل کنترل دی: {response.text}</h3>"
        except Exception as e:
            return f"<h3>خطا در برقراری ارتباط با سرور: {str(e)}</h3>"


# Add this endpoint inside ip_server.py

@app.get("/paystar/redirect", response_class=HTMLResponse)
async def paystar_redirect(token: str):
    """
    Acts as a secure referrer proxy [cite: 3.2.1].
    Automatically submits a POST form to Paystar with the token [cite: 3.2.1, 5.1.2].
    This guarantees that the HTTP Referer header matches your registered domain/IP!
    """
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
# ONLINE PAYMENT CALLBACK CONTROLLER
# ============================================================================

@app.post("/paystar/callback", response_class=HTMLResponse)
async def paystar_callback(
    request: Request,
    status: int = Form(...),
    order_id: str = Form(...),  # order_id matches our tracking_code [cite: 3.3.1]
    ref_num: str = Form(...),
    card_number: str = Form(""),
    tracking_code: str = Form("")
):
    # If the transaction failed on the gateway side, show immediate failed page [cite: 5.1.2]
    if status != 1:
        return _failed_html("پرداخت توسط کاربر لغو شد یا ناموفق بود.")

    async with async_session_maker() as session:
        # Fetch the pending Order
        stmt = select(Order).where(Order.tracking_code == order_id).order_by(Order.created_at.desc()).limit(1)
        res = await session.execute(stmt)
        order = res.scalars().first()

        if not order or order.status == OrderStatus.COMPLETED.value:
            return _failed_html("این سفارش یافت نشد یا قبلاً تکمیل شده است.")

        # Convert to Toman for verification comparison
        amount_toman = order.amount

        # Call Paystar API to verify the signature and transaction [cite: 5.4.1]
        paystar = PaystarService()
        is_verified = await paystar.verify_payment(
            amount_toman=amount_toman,
            ref_num=ref_num,
            card_number=card_number,
            tracking_code=tracking_code
        )

        if not is_verified:
            return _failed_html("خطا در تایید اصالت تراکنش درگاه بانکی.")

        # PROVISION DEVICE ON CONTROL D INSTANTLY [cite: 1]
        raw_username = order.custom_username or ""
        if "|" in raw_username:
            parts = raw_username.split("|")
            username = parts[0]
            service_pk = parts[1] if len(parts) > 1 else "default"
            pop_code = parts[2] if len(parts) > 2 else None
        else:
            username = raw_username
            service_pk = "default"
            pop_code = None

        profile_id = order.plan.controld_profile_id if order.plan else settings.controld_profile_id
        duration_hours = order.plan.duration_hours if order.plan else 720
        duration_days = duration_hours // 24 if duration_hours >= 24 else 1

        now = datetime.now(timezone.utc)
        expire_at = now + timedelta(hours=duration_hours)
        unique_device_name = f"tg_user_{order.user_id}_{order.tracking_code}"

        device_data = await create_dns_device(
            tg_user_id=order.user_id,
            profile_id=profile_id,
            duration_hours=duration_hours,
            device_type="mobile",
            device_name=unique_device_name
        )

        if not device_data:
            return _failed_html("تراکنش بانکی تایید شد، اما خطا در ساخت DNS در پنل Control D رخ داد. لطفاً با پشتیبانی تماس بگیرید.")

        device_id = device_data["device_id"]
        doh_link = device_data["doh"]
        dot_link = device_data.get("dot") or ""
        ipv4_primary = device_data["ipv4_primary"]
        ipv4_secondary = device_data["ipv4_secondary"]

        # Apply chosen routing country
        controld_service = ControlDService(settings)
        if pop_code:
            if service_pk == "default":
                await controld_service.update_profile_default(profile_id, pop_code)  
            else:
                await controld_service.update_service_route(profile_id, service_pk, pop_code)  

        # Save active VPNService to DB
        new_subscription = VPNService(
            user_id=order.user_id,
            plan_id=order.plan_id,
            controld_device_id=device_id,
            config_link=doh_link,
            subscription_link=dot_link or None,
            username=username,
            expire_at=expire_at,
            status="active",
            order_id=order.id
        )
        session.add(new_subscription)

        # Update Order and Payment to Completed [cite: 1]
        order.status = OrderStatus.COMPLETED.value
        order.paid_at = now
        order.completed_at = now

        payment_stmt = select(Payment).where(Payment.order_id == order.id).limit(1)
        payment_res = await session.execute(payment_stmt)
        payment = payment_res.scalars().first()
        if payment:
            payment.status = PaymentStatus.APPROVED.value
            payment.verified_at = now
            payment.tracking_code = tracking_code

        await session.commit()

        # Send Telegram message containing connection details directly to user [cite: 1]
        try:
            duration_text = calculate_remaining_time_fa(expire_at)
            
            try:
                tehran_tz = ZoneInfo("Asia/Tehran")
                tehran_expire = expire_at.astimezone(tehran_tz)
                shamsi_expire = jdatetime.datetime.fromgregorian(datetime=tehran_expire)
                expire_str = shamsi_expire.strftime("%Y/%m/%d - %H:%M:%S")
            except Exception:
                expire_str = expire_at.strftime("%Y-%m-%d %H:%M")

            user_display_name = next((s["name"] for s in [
                {"pk": "default", "name": "🌐 کل ترافیک اینترنت"},
                {"pk": "callofduty", "name": "🎮 Call of Duty"},
                {"pk": "apexlegends", "name": "🎮 Apex Legends"},
                {"pk": "pubg", "name": "🎮 PUBG Mobile"},
                {"pk": "fortnite", "name": "🎮 Fortnite"},
                {"pk": "youtube", "name": "📹 YouTube"},
                {"pk": "netflix", "name": "🎬 Netflix"}
            ] if s["pk"] == service_pk), service_pk)

            country_display = pop_code
            proxies = await controld_service.fetch_controld_proxies()
            if proxies:
                for p in proxies:
                    if p["code"] == pop_code:
                        country_display = f"{p['country_name']} - {p['city_name']} ({p['code']})"
                        break

            success_telegram_text = f"""✅ <b>پرداخت آنلاین شما تایید و اشتراک فعال شد!</b>

🛒 <b>کد پیگیری تراکنش:</b> <code>{tracking_code}</code>
🗓 <b>مدت اعتبار:</b> {duration_text}
📅 <b>تاریخ انقضاء:</b> {expire_str}
🎮 <b>برنامه/بازی:</b> {escape(user_display_name)}
🗺 <b>سرور (کشور):</b> {escape(country_display)}

🔐 <b>دی‌ان‌اس اختصاصی شما:</b>
Primary: <code>{ipv4_primary}</code>
Secondary: <code>{ipv4_secondary}</code>

⚠️ <i>در صورت عدم اتصال دی‌ان‌اس‌ها، لطفاً وضعیت اتصال اینترنت خود را شخصاً بررسی کنید.</i>"""

            # Build and attach registration keyboard [cite: 1]
            builder = InlineKeyboardBuilder()
            builder.button(text="✳️ ثبت آی‌پی اتوماتیک ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
            builder.button(text="✳️ ثبت آی‌پی اتوماتیک 2 ✳️", url=f"{WEB_SERVER_BASE_URL}/update-ip/{device_id}")
            builder.button(text="🤖 ثبت آی‌پی دستی 🤖", callback_data=f"manual_ip_reg:{device_id}")
            builder.adjust(1)

            # Send Message [cite: 1]
            await bot.send_message(
                chat_id=order.user.telegram_id,
                text=success_telegram_text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
        except Exception as te:
            pass

        # Return gorgeous green HTML success page
        return f"""
        <html>
        <head>
            <meta charset="utf-8">
            <title>پرداخت موفقیت‌آمیز</title>
            <style>
                body {{ font-family: Tahoma, Arial, sans-serif; background-color: #f4f6f9; text-align: center; padding: 50px; direction: rtr; }}
                .card {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); display: inline-block; }}
                h1 {{ color: #2ecc71; }}
                p {{ color: #333; font-size: 18px; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h1>✅ پرداخت شما با موفقیت انجام شد!</h1>
                <p>کد رهگیری تراکنش: <b>{tracking_code}</b></p>
                <p>اشتراک DNS شما با موفقیت فعال شد. جزئیات اتصال به ربات تلگرام شما ارسال گردید.</p>
            </div>
        </body>
        </html>
        """


def _failed_html(reason: str) -> HTMLResponse:
    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>خطا در پرداخت</title>
        <style>
            body {{ font-family: Tahoma, Arial, sans-serif; background-color: #f4f6f9; text-align: center; padding: 50px; direction: rtr; }}
            .card {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); display: inline-block; }}
            h1 {{ color: #e74c3c; }}
            p {{ color: #333; font-size: 18px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>❌ تراکنش ناموفق بود</h1>
            <p>{reason}</p>
            <p>مبلغ کسر شده (در صورت کسر وجه) طی ۷۲ ساعت به حساب شما بازخواهد گشت. لطفاً مجدداً در ربات تلاش کنید.</p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)