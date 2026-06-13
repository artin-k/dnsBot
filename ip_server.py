# ip_server.py
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import httpx

# Import the exact settings helper used by your bot
from app.config import get_settings

app = FastAPI()
settings = get_settings()

@app.get("/update-ip/{device_id}", response_class=HTMLResponse)
async def update_device_ip(request: Request, device_id: str):
    # 1. Detect the client's real public IP address (handling proxies safely)
    client_ip = request.headers.get("x-forwarded-for") or request.client.host
    if client_ip and "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()

    token = settings.controld_api_token
    if not token:
        return "<h3>خطا: توکن API مربوط به Control D در تنظیمات (.env) یافت نشد.</h3>"

    # 2. Update the authorized IP on Control D
    url = f"https://api.controld.com/devices/{device_id}/ips"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "accept": "application/json"
    }
    payload = {"ip": client_ip}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers=headers, timeout=10.0)
            if response.status_code in (200, 201):
                # Show the beautiful success card matching your UI theme
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