# app/services/controld.py
import httpx
import logging
import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

BASE_URL = "https://api.controld.com"

# Dynamic Category Labels [1]
CATEGORY_MAP_FA = {
    "gaming": "🎮 بازی‌ها (Gaming)",
    "video": "🎬 رسانه و استریم (Video/Streaming)",
    "social": "💬 شبکه‌های اجتماعی (Social)",
    "ai": "🤖 هوش مصنوعی (AI & Tech)",
    "music": "🎵 موسیقی (Music)",
    "news": "📰 اخبار (News)",
    "shopping": "🛒 خرید (Shopping)",
    "business": "💼 کسب و کار (Business)",
    "productivity": "🛠 ابزارها (Productivity)",
    "other": "🧩 سایر سرویس‌ها (Other)"
}

def get_category_label_fa(category_key: str) -> str:
    """
    Cleans and translates category keys dynamically (handles prefixes like 'native_') [1].
    """
    clean_key = category_key.lower().replace("native_", "").strip()
    return CATEGORY_MAP_FA.get(clean_key, f"🧩 {clean_key.capitalize()}")


# Persian Country Translator Map [1]
COUNTRY_MAP_FA = {
    "AL": "آلبانی",
    "AQ": "قطب جنوب",
    "AR": "آرژانتین",
    "AU": "استرالیا",
    "AT": "اتریش",
    "BE": "بلژیک",
    "BA": "بوسنی",
    "BR": "برزیل",
    "BG": "بلغارستان",
    "CL": "شیلی",
    "CO": "کلمبیا",
    "CY": "قبرس",
    "CZ": "جمهوری چک",
    "DK": "دانمارک",
    "EC": "اکوادور",
    "EE": "استونی",
    "GE": "گرجستان",
    "US": "ایالات متحده",
    "DE": "آلمان",
    "TR": "ترکیه",
    "GB": "انگلستان",
    "FR": "فرانسه",
    "NL": "هلند",
    "FI": "فنلاند",
    "AE": "امارات متحده",
    "IR": "ایران",
    "CA": "کانادا",
    "SG": "سنگاپور",
    "UA": "اوکراین",
    "RU": "روسیه",
    "SE": "سوئد",
    "CH": "سوئیس",
    "IT": "ایتالیا",
    "PL": "لهستان",
    "ES": "اسپانیا",
    "IN": "هند",
    "JP": "ژاپن",
    "KR": "کره جنوبی",
    "ZA": "آفریقای جنوبی",
    "HK": "هنگ کنگ",
    "HU": "مجارستان",
    "IS": "ایسلند",
    "IE": "ایرلند",
    "IL": "اسرائیل",
    "LV": "لتونی",
    "LT": "لیتوانی",
    "LU": "لوکزامبورگ",
    "MK": "مقدونیه",
    "MY": "مالزی",
    "MX": "مکزیک",
    "MD": "مولداوی",
    "NZ": "نیوزیلند",
    "NG": "نیجریه",
    "NO": "نروژ",
    "PA": "پاناما",
    "PE": "پرو",
    "PH": "فیلیپین",
    "PT": "پرتغال",
    "RO": "رومانی",
    "RS": "صربستان",
    "SK": "اسلواکی",
    "TW": "تایوان",
    "TH": "تایلند",
    "VN": "ویتنام"
}


# Persian City Translator Map [1]
CITY_MAP_FA = {
    "Adelaide": "آدلاید",
    "Brisbane": "بریزبن",
    "Melbourne": "ملبورن",
    "Perth": "پرت",
    "Sydney": "سیدنی",
    "New York": "نیویورک",
    "Los Angeles": "لس آنجلس",
    "London": "لندن",
    "Paris": "پاریس",
    "Frankfurt": "فرانکفورت",
    "Amsterdam": "آمستردام",
    "Helsinki": "هلسینکی",
    "Toronto": "تورنتو",
    "Montreal": "مونترال",
    "Vancouver": "ونکوور",
    "Atlanta": "آتلانتا",
    "Chicago": "شیکاگو",
    "Dallas": "دالاس",
    "Denver": "دنور",
    "Miami": "میامی",
    "Seattle": "سیاتل",
    "Istanbul": "استانبول",
    "Vienna": "وین",
    "Brussels": "بروکسل",
    "Sarajevo": "سارایوو",
    "Sao Paulo": "سائو پائولو",
    "Dubai": "دبی",
    "Singapore": "سنگاپور",
    "Warsaw": "ورشو",
    "Stockholm": "استکهلم",
    "Geneva": "ژنو",
    "Zurich": "زوریخ",
    "Milan": "میلان",
    "Rome": "رم",
    "Madrid": "مادرید",
    "Tokyo": "توکیو",
    "Seoul": "سئول"
}


# Replace this function in app/services/controld.py (Near the top)

def get_country_name_fa(country_code: str, fallback_name: str | None = None) -> str:
    """
    Translates ISO country codes to Persian, falling back to full English name or code [1].
    The fallback_name is now optional to prevent any router/notification TypeErrors [1].
    """
    fallback = fallback_name or country_code.upper()
    return COUNTRY_MAP_FA.get(country_code.upper(), fallback)


def get_city_name_fa(city_name: str) -> str:
    """Translates the city name to Persian."""
    return CITY_MAP_FA.get(city_name, city_name)


def _get_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.controld_api_token}",
        "Content-Type": "application/json",
        "accept": "application/json"
    }


def generate_dns_stamp(resolver_id: str) -> str:
    import struct
    import base64
    
    protocol = b'\x02' 
    properties = struct.pack('<Q', 1) 
    ip_addr_len = b'\x00' 
    hashes_len = b'\x00' 
    
    host = b"dns.controld.com"
    host_len = bytes([len(host)])
    
    path = f"/{resolver_id}".encode('utf-8')
    path_len = bytes([len(path)])
    
    payload = protocol + properties + ip_addr_len + hashes_len + host_len + host + path_len + path
    encoded = base64.urlsafe_b64encode(payload).decode('utf-8').rstrip('=')
    return f"sdns://{encoded}"


async def create_dns_device(
    tg_user_id: int, 
    profile_id: str, 
    duration_hours: int, 
    device_type: str = "mobile", 
    device_name: str | None = None
) -> dict | None: 
    url = f"{BASE_URL}/devices"
    name = device_name or f"tg_user_{tg_user_id}"
    
    # --- FIXED: Strip any metadata from the device name to prevent Control D API crashes [cite: 1] ---
    if name and "|" in name:
        name = name.split("|")[0].strip()
    # -------------------------------------------------------------------------------------------------

    disable_ttl = int((datetime.now(timezone.utc) + timedelta(hours=duration_hours)).timestamp())
    payload = {
        "name": name,
        "profile_id": profile_id,
        "device_type": device_type,
        "analytics": 1,
        "disable_ttl": disable_ttl
    }

    async with httpx.AsyncClient() as client:
        try: 
            response = await client.post(url, json=payload, headers=_get_headers(), timeout=10.0)
            if response.status_code in (200, 201):
                data = response.json()
                body = data.get("body", {})
                
                device_info = body.get("device") or {}
                device_pk = body.get("device_id") or body.get("PK") or body.get("pk") or device_info.get("pk") or device_info.get("id")
                
                resolver_info = body.get("resolvers") or body.get("resolver") or {}
                doh = resolver_info.get("doh") or resolver_info.get("dns_over_https")
                dot = resolver_info.get("dot") or resolver_info.get("dns_over_tls")
                
                # Inside app/services/controld.py -> create_dns_device()

                v4_list = resolver_info.get("v4") or resolver_info.get("legacy", {}).get("ipv4") or []
                v6_list = resolver_info.get("v6") or resolver_info.get("legacy", {}).get("ipv6") or []
                
                # --- FIXED: Added standard Anycast DNS fallbacks to prevent 'ثبت نشده' [cite: 1] ---
                ipv4_primary = v4_list[0] if len(v4_list) > 0 else "76.76.2.22"
                ipv4_secondary = v4_list[1] if len(v4_list) > 1 else "76.76.10.22"
                ipv6 = v6_list[0] if len(v6_list) > 0 else "2606:1a40::22"
                
                resolver_id = resolver_info.get("uid") or resolver_info.get("id") or device_pk
                stamp = resolver_info.get("stamp") or resolver_info.get("dns_stamp")
                if not stamp and resolver_id:
                    stamp = generate_dns_stamp(resolver_id)
                
                if device_pk and doh and dot:
                    return {
                        "device_id": device_pk,
                        "doh": doh,
                        "dot": dot,
                        "ipv4_primary": ipv4_primary,
                        "ipv4_secondary": ipv4_secondary,
                        "ipv6": ipv6,
                        "resolver_id": resolver_id,
                        "stamp": stamp
                    }
                logger.error(f"Incomplete Control D response payload: {data}")
                return None
            else:
                logger.error(f"Control D API error (Status {response.status_code}): {response.text}")
                return None
        except Exception as e:
            logger.error(f"Failed to query Control D: {str(e)}")
            return None


async def delete_dns_device(device_id: str) -> bool:
    url = f"{BASE_URL}/devices/{device_id}"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.delete(url, headers=_get_headers(), timeout=10.0)
            if response.status_code == 200:
                return True
            else:
                logger.error(f"Failed to delete Control D device {device_id} (Status {response.status_code}): {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error during deleting Control D device {device_id}: {str(e)}")
            return False


async def update_dns_device(device_id: str, disable_ttl: int) -> bool:
    url = f"{BASE_URL}/devices/{device_id}"
    payload = {
        "disable_ttl": disable_ttl
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.put(url, json=payload, headers=_get_headers(), timeout=10.0)
            if response.status_code == 200:
                logger.info("controld_device_updated", device_id=device_id, disable_ttl=disable_ttl)
                return True
            else:
                logger.error(f"Failed to update Control D device {device_id} (Status {response.status_code}): {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error during updating Control D device {device_id}: {str(e)}")
            return False


async def fetch_controld_profiles() -> list[dict] | None:
    url = f"{BASE_URL}/profiles"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=_get_headers(), timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                profiles = data.get("body", {}).get("profiles", [])
                result = []
                for p in profiles:
                    result.append({
                        "id": p.get("id") or p.get("pk") or p.get("PK"),
                        "name": p.get("name"),
                        "description": p.get("description", "")
                    })
                return result
            else:
                logger.error(f"Failed to fetch Control D profiles: {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error fetching Control D profiles: {str(e)}")
            return None


# Inside app/services/controld.py -> create_device()

async def create_device(profile_id: str, device_name: str, duration_hours: int) -> dict | None:
    url = f"{BASE_URL}/devices"
    disable_ttl = int((datetime.now(timezone.utc) + timedelta(hours=duration_hours)).timestamp())
    
    # --- FIXED: Strip any metadata from the device name to prevent Control D API crashes [cite: 1] ---
    if device_name and "|" in device_name:
        device_name = device_name.split("|")[0].strip()
    # -------------------------------------------------------------------------------------------------

    payload = {
        "name": device_name,
        "profile_id": profile_id,
        "analytics": 1,
        "disable_ttl": disable_ttl
    }
    # ... rest of the function continues normally ...
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=_get_headers()) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    body = data.get("body", {})
                    
                    device_info = body.get("device") or {}
                    device_pk = body.get("device_id") or body.get("PK") or body.get("pk") or device_info.get("pk") or device_info.get("id")
                    
                    resolver_info = body.get("resolvers") or body.get("resolver") or {}
                    doh = resolver_info.get("doh") or resolver_info.get("dns_over_https")
                    
                    if device_pk and doh:
                        return {"device_id": device_pk, "doh": doh}
                    logger.error(f"Incomplete Control D response payload: {data}")
                    return None
                else:
                    text = await resp.text()
                    logger.error(f"Control D API error (Status {resp.status}): {text}")
                    return None
    except asyncio.TimeoutError:
        logger.error("Control D request timed out")
        return None
    except Exception as e:
        logger.error(f"Failed to query Control D (aiohttp): {str(e)}")
        return None


async def delete_device(device_id: str) -> bool:
    url = f"{BASE_URL}/devices/{device_id}"
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.delete(url, headers=_get_headers()) as resp:
                if resp.status in (200, 204, 404):
                    if resp.status == 404:
                        logger.info("controld_device_already_absent", device_id=device_id)
                    return True
                text = await resp.text()
                logger.error(f"Failed to delete Control D device {device_id} (Status {resp.status}): {text}")
                return False
    except asyncio.TimeoutError:
        logger.error("Control D delete request timed out")
        return False
    except Exception as e:
        logger.error(f"Error during deleting Control D device {device_id}: {str(e)}")
        return False


async def update_dns_device_profile(device_id: str, profile_id: str) -> bool:
    url = f"{BASE_URL}/devices/{device_id}"
    payload = {
        "profile_id": profile_id
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.put(url, json=payload, headers=_get_headers(), timeout=10.0)
            if response.status_code == 200:
                logger.info("controld_device_profile_updated", device_id=device_id, profile_id=profile_id)
                return True
            else:
                logger.error(f"Failed to update Control D device profile {device_id} (Status {response.status_code}): {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error during updating Control D device profile {device_id}: {str(e)}")
            return False


async def fetch_controld_proxies() -> list[dict] | None:
    url = f"{BASE_URL}/proxies"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=_get_headers(), timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                body = data.get("body", {})
                proxies = body.get("proxies", [])
                
                result = []
                for p in proxies:
                    country_code = p.get("country") or "US"
                    fallback_name = p.get("country_name") or country_code
                    pop_id = p.get("PK") or p.get("id") or p.get("code") or p.get("pop") or p.get("location_code")
                    if not pop_id:
                        continue 
                    
                    # Update this part inside fetch_controld_proxies() in app/services/controld.py

                    result.append({
                        "code": pop_id,
                        "country_code": country_code,
                        "flag": get_flag_emoji(country_code),  # <-- Added Unicode Flag [1]
                        "country_name": get_country_name_fa(country_code, fallback_name),
                        "city_name": get_city_name_fa(p.get("city") or ""),
                        "city": p.get("city") or ""
                    })
                return result
            return None
        except Exception as e:
            logger.error(f"Error fetching Control D proxies: {str(e)}")
            return None


# app/services/controld.py (Paste below get_country_name_fa)

def get_flag_emoji(country_code: str) -> str:
    """
    Converts a 2-letter ISO country code (e.g., 'US') directly into its 
    corresponding Unicode regional indicator flag emoji [1].
    """
    if not country_code or len(country_code) != 2:
        return "📍"
    base = 127397  # Regional Indicator Symbol Letter A offset [1]
    try:
        char1 = chr(ord(country_code[0].upper()) + base)
        char2 = chr(ord(country_code[1].upper()) + base)
        return f"{char1}{char2}"
    except Exception:
        return "📍"

async def update_service_route(profile_id: str, service_name: str, pop_code: str) -> bool:
    url = f"{BASE_URL}/profiles/{profile_id}/services/{service_name}"
    payload = {
        "do": 3,      
        "status": 1,  
        "via": pop_code
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.put(url, json=payload, headers=_get_headers(), timeout=10.0)
            return response.status_code in (200, 201)
        except Exception as e:
            logger.error(f"Error updating service route for {service_name}: {str(e)}")
            return False


# app/services/controld.py

# --- LOCATE THIS FUNCTION AND REPLACE IT ---
async def update_profile_default_route(profile_id: str, pop_code: str) -> bool:
    """
    Updates the catch-all Default Rule for a profile.
    Correct Control D API endpoint: PUT /profiles/{profile_id}/default
    """
    url = f"{BASE_URL}/profiles/{profile_id}/default"
    payload = {
        "do": 3,      # 3 represents REDIRECT/SPOOF in Control D's default rule options [cite: 8.3.2]
        "status": 1,  # 1 represents enabled/active [cite: 8.4.1]
        "via": pop_code
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.put(url, json=payload, headers=_get_headers(), timeout=10.0)
            if response.status_code in (200, 201):
                logger.info("controld_default_route_updated", profile_id=profile_id, pop_code=pop_code)
                return True
            else:
                logger.error(f"Failed to update default route on Control D (Status {response.status_code}): {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error updating profile default route: {str(e)}")
            return False


async def fetch_controld_services(profile_id: str) -> list[dict] | None:
    """
    Queries the complete, unfiltered catalog of services and games directly 
    from the target Control D Profile [1].
    """
    # --- FIXED: Appended ?all=1 to retrieve the full catalog [1] ---
    url = f"{BASE_URL}/profiles/{profile_id}/services?all=1" 
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=_get_headers(), timeout=10.0)
            # ... rest of the code is unchanged ...
            if response.status_code == 200:
                data = response.json()
                body = data.get("body", [])
                
                # Defensive dictionary-to-list parser [cite: 1]
                services_list = []
                if isinstance(body, dict):
                    raw_services = body.get("services") or body.get("apps") or {}
                    if isinstance(raw_services, dict):
                        for pk, s_data in raw_services.items():
                            if isinstance(s_data, dict):
                                s_data["pk"] = pk
                                services_list.append(s_data)
                    elif isinstance(raw_services, list):
                        services_list = raw_services
                elif isinstance(body, list):
                    services_list = body
                
                result = []
                for s in services_list:
                    pk_val = s.get("PK") or s.get("pk") or s.get("id")
                    if not pk_val:
                        continue
                    result.append({
                        "pk": pk_val,
                        "name": s.get("name") or pk_val,
                        "category": s.get("category") or "other"
                    })
                return result
            else:
                logger.error(f"Failed to fetch Control D services (Status {response.status_code}): {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error fetching Control D services: {str(e)}")
            return None


class ControlDService:
    def __init__(self, settings_obj=None) -> None:
        self.settings = settings_obj or settings

    async def create_dns_device(self, tg_user_id: int, profile_id: str, duration_hours: int, device_type: str = "mobile", device_name: str | None = None) -> dict | None:
        return await create_dns_device(
            tg_user_id=tg_user_id, 
            profile_id=profile_id, 
            duration_hours=duration_hours, 
            device_type=device_type, 
            device_name=device_name
        )

    async def delete_dns_device(self, device_id: str) -> bool:
        return await delete_dns_device(device_id=device_id)

    async def update_device(self, device_id: str, disable_ttl: int) -> bool:
        return await update_dns_device(device_id=device_id, disable_ttl=disable_ttl)

    async def fetch_controld_profiles(self) -> list[dict] | None:
        return await fetch_controld_profiles()
    
    async def create_device(self, profile_id: str, device_name: str, duration_hours: int) -> dict | None:
        return await create_device(profile_id=profile_id, device_name=device_name, duration_hours=duration_hours)

    async def delete_device(self, device_id: str) -> bool:
        return await delete_device(device_id=device_id)

    async def update_device_profile(self, device_id: str, profile_id: str) -> bool:
        return await update_dns_device_profile(device_id=device_id, profile_id=profile_id)

    async def fetch_controld_proxies(self) -> list[dict] | None:
        return await fetch_controld_proxies()

    async def update_service_route(self, profile_id: str, service_name: str, pop_code: str) -> bool:
        return await update_service_route(profile_id=profile_id, service_name=service_name, pop_code=pop_code)

    async def fetch_controld_services(self, profile_id: str) -> list[dict] | None:
        return await fetch_controld_services(profile_id=profile_id)

    async def update_profile_default(self, profile_id: str, pop_code: str) -> bool:
        """Exposes the overall profile default routing updater inside the class [cite: 1]."""
        from app.services.controld import update_profile_default_route
        return await update_profile_default_route(profile_id, pop_code)
