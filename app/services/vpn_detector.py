# app/services/vpn_detector.py
from __future__ import annotations

import ipaddress
import time
from typing import NamedTuple
import httpx
import structlog

logger = structlog.get_logger(__name__)


class IPVerificationResult(NamedTuple):
    is_vpn: bool
    country: str
    country_code: str
    isp: str
    error_message: str | None


# In-memory cache (ip -> (result, expire_timestamp)) to prevent repeated lookups
_CACHE: dict[str, tuple[IPVerificationResult, float]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes


async def verify_user_ip(ip: str) -> IPVerificationResult:
    """
    Inspects whether an IP address belongs to a VPN, proxy, datacenter, or non-domestic network.
    Returns an IPVerificationResult.
    """
    clean_ip = ip.strip()

    # 1. Reject local/private IPs (e.g. 127.0.0.1, 192.168.x.x, 10.x.x.x)
    try:
        ip_obj = ipaddress.ip_address(clean_ip)
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved or ip_obj.is_link_local:
            return IPVerificationResult(
                is_vpn=True,
                country="Local",
                country_code="LOC",
                isp="Private Network",
                error_message="آدرس وارد شده یک آی‌پی محلی (لوکال) است. لطفاً آی‌پی عمومی اینترنت خود را استفاده کنید.",
            )
    except ValueError:
        return IPVerificationResult(
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

    # 3. Primary Lookup via ipwho.is (fast, HTTPS, free security/proxy flags)
    async with httpx.AsyncClient(timeout=3.5) as client:
        try:
            resp = await client.get(f"https://ipwho.is/{clean_ip}")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    cc = data.get("country_code", "")
                    country = data.get("country", "نامشخص")
                    isp = data.get("connection", {}).get("isp", "نامشخص")
                    sec = data.get("security", {})
                    is_proxy_or_vpn = sec.get("vpn", False) or sec.get("proxy", False) or sec.get("hosting", False)

                    # Any non-IR IP for an Iranian gaming DNS service indicates active VPN/proxy
                    if cc != "IR":
                        result = IPVerificationResult(
                            is_vpn=True,
                            country=country,
                            country_code=cc,
                            isp=isp,
                            error_message=f"فیلترشکن شما روشن است! آی‌پی شما متعلق به کشور {country} ({cc}) شناسایی شد.",
                        )
                        _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                        return result

                    if is_proxy_or_vpn:
                        result = IPVerificationResult(
                            is_vpn=True,
                            country=country,
                            country_code=cc,
                            isp=isp,
                            error_message=f"آی‌پی دیتاسنتر یا پروکسی شناسایی شد ({isp}). لطفاً اینترنت مستقیم خانگی یا همراه را متصل کنید.",
                        )
                        _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                        return result

                    # Valid domestic ISP IP
                    result = IPVerificationResult(
                        is_vpn=False,
                        country=country,
                        country_code=cc,
                        isp=isp,
                        error_message=None,
                    )
                    _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                    return result
        except Exception as exc:
            logger.warning("ipwhois_lookup_failed_trying_fallback", ip=clean_ip, error=str(exc))

    # 4. Fallback Lookup via ip-api.com
    async with httpx.AsyncClient(timeout=3.5) as client:
        try:
            resp = await client.get(
                f"http://ip-api.com/json/{clean_ip}?fields=status,country,countryCode,isp,proxy,hosting"
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    cc = data.get("countryCode", "")
                    country = data.get("country", "نامشخص")
                    isp = data.get("isp", "نامشخص")
                    is_proxy_or_vpn = data.get("proxy", False) or data.get("hosting", False)

                    if cc != "IR":
                        result = IPVerificationResult(
                            is_vpn=True,
                            country=country,
                            country_code=cc,
                            isp=isp,
                            error_message=f"فیلترشکن شما روشن است! آی‌پی شما متعلق به کشور {country} ({cc}) شناسایی شد.",
                        )
                        _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                        return result

                    if is_proxy_or_vpn:
                        result = IPVerificationResult(
                            is_vpn=True,
                            country=country,
                            country_code=cc,
                            isp=isp,
                            error_message=f"آی‌پی سرور مجازی یا دیتاسنتر شناسایی شد ({isp}).",
                        )
                        _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                        return result

                    result = IPVerificationResult(
                        is_vpn=False,
                        country=country,
                        country_code=cc,
                        isp=isp,
                        error_message=None,
                    )
                    _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                    return result
        except Exception as exc:
            logger.warning("ip_api_lookup_failed", ip=clean_ip, error=str(exc))

    # 5. Fail-open fallback: If both intelligence APIs are unreachable, allow registration
    return IPVerificationResult(
        is_vpn=False,
        country="Unknown",
        country_code="UNK",
        isp="Unknown",
        error_message=None,
    )