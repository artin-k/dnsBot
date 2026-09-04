# app/services/vpn_detector.py
from __future__ import annotations

import ipaddress
import time
from typing import NamedTuple
import httpx
import structlog

logger = structlog.get_logger(__name__)


class IPVerificationResult(NamedTuple):
    is_iran: bool
    is_vpn: bool
    country: str
    country_code: str
    isp: str
    error_message: str | None


# In-memory cache: ip -> (IPVerificationResult, expire_timestamp)
_CACHE: dict[str, tuple[IPVerificationResult, float]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes cache


async def verify_user_ip(ip: str) -> IPVerificationResult:
    """
    STRICT IRAN ENFORCER:
    Checks whether the supplied IP belongs to an Iranian network (IR).
    Blocks foreign IPs, private/bogon IPs, and foreign VPN/Datacenter proxies.
    """
    clean_ip = (ip or "").strip()

    # 1. Validate format & reject private / local / loopback IPs
    try:
        ip_obj = ipaddress.ip_address(clean_ip)
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved or ip_obj.is_link_local:
            return IPVerificationResult(
                is_iran=False,
                is_vpn=True,
                country="Local",
                country_code="LOC",
                isp="Private Network",
                error_message="آدرس وارد شده یک آی‌پی محلی (لوکال/خصوصی) است. لطفاً آی‌پی عمومی اینترنت خود را وارد کنید.",
            )
    except ValueError:
        return IPVerificationResult(
            is_iran=False,
            is_vpn=True,
            country="Unknown",
            country_code="UNK",
            isp="Invalid",
            error_message="فرمت آدرس آی‌پی نامعتبر است.",
        )

    # 2. Check in-memory cache
    now = time.time()
    if clean_ip in _CACHE:
        cached_result, expire_at = _CACHE[clean_ip]
        if now < expire_at:
            return cached_result

    # 3. Provider 1: api.country.is (Ultra fast, ~25-50ms, no rate-limits)
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(f"https://api.country.is/{clean_ip}")
            if resp.status_code == 200:
                data = resp.json()
                cc = str(data.get("country", "")).upper()
                if cc:
                    if cc != "IR":
                        result = IPVerificationResult(
                            is_iran=False,
                            is_vpn=True,
                            country=cc,
                            country_code=cc,
                            isp="فیلترشکن یا سرور خارجی",
                            error_message=f"فیلترشکن شما روشن است! آی‌پی شما متعلق به کشور {cc} شناسایی شد.",
                        )
                        _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                        logger.info("ip_blocked_non_iran", ip=clean_ip, country=cc)
                        return result
                    else:
                        result = IPVerificationResult(
                            is_iran=True,
                            is_vpn=False,
                            country="ایران",
                            country_code="IR",
                            isp="اپراتور داخلی ایران",
                            error_message=None,
                        )
                        _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                        logger.info("ip_approved_iran", ip=clean_ip)
                        return result
    except Exception as exc:
        logger.warning("country_is_check_failed_fallback", ip=clean_ip, error=str(exc))

    # 4. Provider 2: ip-api.com (Detects proxy / hosting / datacenter flags)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"http://ip-api.com/json/{clean_ip}?fields=status,country,countryCode,isp,proxy,hosting"
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    cc = str(data.get("countryCode", "")).upper()
                    country = data.get("country", cc)
                    isp = data.get("isp", "Foreign ISP")
                    is_proxy = data.get("proxy", False) or data.get("hosting", False)

                    if cc != "IR" or is_proxy:
                        result = IPVerificationResult(
                            is_iran=False,
                            is_vpn=True,
                            country=country,
                            country_code=cc,
                            isp=isp,
                            error_message=f"فیلترشکن یا پروکسی فعال است (کشور: {country} [{cc}]).",
                        )
                        _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                        return result

                    result = IPVerificationResult(
                        is_iran=True,
                        is_vpn=False,
                        country="ایران",
                        country_code="IR",
                        isp=isp,
                        error_message=None,
                    )
                    _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                    return result
    except Exception as exc:
        logger.warning("ip_api_check_failed_fallback", ip=clean_ip, error=str(exc))

    # 5. Provider 3: ipwho.is
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"https://ipwho.is/{clean_ip}")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    cc = str(data.get("country_code", "")).upper()
                    country = data.get("country", cc)
                    isp = data.get("connection", {}).get("isp", "Foreign")

                    if cc != "IR":
                        result = IPVerificationResult(
                            is_iran=False,
                            is_vpn=True,
                            country=country,
                            country_code=cc,
                            isp=isp,
                            error_message=f"فیلترشکن شما روشن است! آی‌پی متعلق به کشور {country} است.",
                        )
                        _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                        return result
                    else:
                        result = IPVerificationResult(
                            is_iran=True,
                            is_vpn=False,
                            country="ایران",
                            country_code="IR",
                            isp=isp,
                            error_message=None,
                        )
                        _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                        return result
    except Exception as exc:
        logger.warning("ipwhois_check_failed", ip=clean_ip, error=str(exc))

    # 6. Fallback default: Strict block if country verification fails
    return IPVerificationResult(
        is_iran=False,
        is_vpn=True,
        country="نامشخص",
        country_code="UNK",
        isp="نامشخص",
        error_message="امکان اعتبارسنجی لوکیشن آی‌پی وجود نداشت. لطفاً مطمئن شوید فیلترشکن کاملاً خاموش است و دوباره امتحان کنید.",
    )