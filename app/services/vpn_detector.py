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


# In-memory cache (ip -> (result, expire_timestamp))
_CACHE: dict[str, tuple[IPVerificationResult, float]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes


async def verify_user_ip(ip: str) -> IPVerificationResult:
    """
    STRICT IRAN ENFORCER:
    Verifies if an IP is located in Iran (IR).
    If it is from any other country (VPN/Proxy/Datacenter), returns is_iran=False.
    """
    clean_ip = ip.strip()

    # 1. Reject local/private IPs (e.g. 127.0.0.1, 192.168.x.x, 10.x.x.x)
    try:
        ip_obj = ipaddress.ip_address(clean_ip)
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved or ip_obj.is_link_local:
            return IPVerificationResult(
                is_iran=False,
                is_vpn=True,
                country="Local",
                country_code="LOC",
                isp="Private Network",
                error_message="آدرس وارد شده یک آی‌پی محلی (لوکال) است. لطفاً آی‌پی عمومی اینترنت خود را وارد کنید.",
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

    # 3. Primary Engine: api.country.is (Blazing fast, ~30ms, no rate limits, works from Iran)
    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
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
                            isp="VPN / Foreign Server",
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
                            isp="Iran Domestic ISP",
                            error_message=None,
                        )
                        _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                        logger.info("ip_approved_iran", ip=clean_ip)
                        return result
        except Exception as exc:
            logger.warning("country_is_check_failed_trying_backup", ip=clean_ip, error=str(exc))

    # 4. Backup Engine: ipwho.is
    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
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
                            error_message=f"فیلترشکن شما روشن است! آی‌پی شما متعلق به کشور {country} ({cc}) است.",
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
                            isp=isp,
                            error_message=None,
                        )
                        _CACHE[clean_ip] = (result, now + CACHE_TTL_SECONDS)
                        return result
        except Exception as exc:
            logger.warning("ipwhois_check_failed", ip=clean_ip, error=str(exc))

    # 5. Strict Default: If lookups fail completely, BLOCK foreign IPs by default (Security first!)
    return IPVerificationResult(
        is_iran=False,
        is_vpn=True,
        country="Unknown",
        country_code="UNK",
        isp="Unknown",
        error_message="امکان اعتبارسنجی کشور آی‌پی وجود نداشت. لطفاً مطمئن شوید فیلترشکن خاموش است و مجدداً تلاش کنید.",
    )