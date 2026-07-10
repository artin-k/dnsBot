# bot/utils/beautifier.py
import re
from datetime import datetime, timezone
import jdatetime
from zoneinfo import ZoneInfo
from html import escape

# Ensure you import your existing SLOT_CONFIGS and helper maps [cite: services.py, 1]
from app.config import SLOT_CONFIGS
from app.services.controld import get_country_name_fa, get_flag_emoji

# ============================================================================
# 1. REGEX-DRIVEN METADATA PARSER
# ============================================================================

def parse_raw_subscription_log(raw_string: str) -> dict:
    """
    Parses raw pipe-delimited subscription strings using Regular Expressions [cite: Paste July 10, 2026 - 8:06PM].
    Extracts: Telegram ID, Account Type, Profile Key, Slot/POP, Status, and Expiry.
    """
    # Split by pipe character and strip trailing/leading spaces
    parts = [part.strip() for p in raw_string.split('|') if (part := p.strip())]
    if len(parts) < 3:
        return {}

    raw_username = parts[0]
    service_pk = parts[1]
    slot_or_loc = parts[2]
    status = parts[3] if len(parts) > 3 else "نامشخص"
    expiry_raw = parts[4] if len(parts) > 4 else ""

    # Regular Expression to extract clean Telegram ID (Removes dns_user_ or tg-test- prefixes and trailing random hashes)
    match = re.search(r'(?:dns_user_|tg-test-)?(\d+)', raw_username)
    telegram_id = match.group(1) if match else raw_username

    # Determine account types cleanly
    account_type = "تست (Trial)" if "tg-test-" in raw_username else "پریمیوم (Premium)"

    return {
        "telegram_id": telegram_id,
        "raw_username": raw_username,
        "account_type": account_type,
        "service_pk": service_pk,
        "slot_or_loc": slot_or_loc,
        "status": status,
        "expiry_raw": expiry_raw
    }

# ============================================================================
# 2. PERSIAN HTML FORMATTER
# ============================================================================

def format_subscription_html(parsed_data: dict) -> str:
    """Formats parsed metadata into a beautiful, RTL-friendly Persian HTML message."""
    if not parsed_data:
        return "❌ خطا در خواندن اطلاعات اشتراک."

    # Map Service Name
    service_display = "کل ترافیک اینترنت (Default)" if parsed_data["service_pk"] == "default" else parsed_data["service_pk"].capitalize()

    # Map Server Slot / Location
    slot_or_loc = parsed_data["slot_or_loc"]
    server_display = slot_or_loc
    if slot_or_loc.isdigit():
        slot_num = int(slot_or_loc)
        if slot_num in SLOT_CONFIGS:
            server_display = SLOT_CONFIGS[slot_num]["name"]
    else:
        # Resolve using Control D ISO translator [cite: services.py, 1]
        try:
            server_display = f"{get_flag_emoji(slot_or_loc)} {get_country_name_fa(slot_or_loc)} ({slot_or_loc})"
        except Exception:
            server_display = slot_or_loc

    # Map Expiry to Shamsi (Jalali)
    shamsi_expire = parsed_data["expiry_raw"]
    try:
        dt = datetime.strptime(parsed_data["expiry_raw"], "%Y-%m-%d %H:%M")
        # Ensure UTC timezone alignment before conversion
        dt = dt.replace(tzinfo=timezone.utc)
        tehran_expire = dt.astimezone(ZoneInfo("Asia/Tehran")).replace(tzinfo=None)
        shamsi_expire = jdatetime.datetime.fromgregorian(datetime=tehran_expire).strftime("%Y/%m/%d - %H:%M:%S")
    except Exception:
        pass

    # Status indicator emoji
    status_emoji = "✅" if parsed_data["status"] == "فعال" else "❌"

    return f"""━━━━━━━━━━━━━━━━━━━━━
👤 <b>شناسه کاربر:</b> <code>{escape(parsed_data["telegram_id"])}</code> (برای کپی لمس کنید)
💎 <b>نوع حساب:</b> {escape(parsed_data["account_type"])}
🎮 <b>برنامه/بازی:</b> <code>{escape(service_display)}</code>
🗺 <b>سرور (لوکیشن):</b> {escape(server_display)}
⏳ <b>تاریخ انقضا:</b> <code>{shamsi_expire}</code>
📌 <b>وضعیت:</b> {status_emoji} {escape(parsed_data["status"])}"""