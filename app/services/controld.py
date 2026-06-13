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


def _get_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.controld_api_token}",
        "Content-Type": "application/json",
        "accept": "application/json"
    }


def generate_dns_stamp(resolver_id: str) -> str:
    """
    Generates a valid DNS Stamp (sdns://) locally for a Control D DoH resolver.
    Bypasses any API delivery issues to guarantee the stamp is always available.
    """
    import struct
    import base64
    
    protocol = b'\x02'  # DoH protocol byte
    properties = struct.pack('<Q', 1)  # DNSSEC enabled (8-byte little-endian)
    ip_addr_len = b'\x00'  # No hardcoded IP address
    hashes_len = b'\x00'  # Empty hashes list
    
    host = b"dns.controld.com"
    host_len = bytes([len(host)])
    
    path = f"/{resolver_id}".encode('utf-8')
    path_len = bytes([len(path)])
    
    # Assemble the binary DNS stamp payload
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
    """
    Creates a new secure DNS endpoint on Control D with a specific profile.
    Extracts both Primary and Secondary legacy IPv4 addresses, resolver details, and Stamps.
    """
    url = f"{BASE_URL}/devices"
    name = device_name or f"tg_user_{tg_user_id}"
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
                
                # Parse Legacy IPs (Primary and Secondary)
                v4_list = resolver_info.get("v4") or resolver_info.get("legacy", {}).get("ipv4") or []
                v6_list = resolver_info.get("v6") or resolver_info.get("legacy", {}).get("ipv6") or []
                
                ipv4_primary = v4_list[0] if len(v4_list) > 0 else "ثبت نشده"
                ipv4_secondary = v4_list[1] if len(v4_list) > 1 else "ثبت نشده"
                ipv6 = v6_list[0] if v6_list else "ثبت نشده"
                
                # Extract Resolver ID and DNS Stamp
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
    """
    Removes a device configuration from Control D on subscription expiry.
    """
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
    """
    Updates an existing device configuration's automatic disable timestamp (disable_ttl).
    """
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
    """
    Fetches all profiles associated with your Control D account.
    """
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


async def create_device(profile_id: str, device_name: str, duration_hours: int) -> dict | None:
    """
    Create a device using aiohttp and return {'device_id': ..., 'doh': '...'} or None on error.
    """
    url = f"{BASE_URL}/devices"
    disable_ttl = int((datetime.now(timezone.utc) + timedelta(hours=duration_hours)).timestamp())
    
    payload = {
        "name": device_name,
        "profile_id": profile_id,
        "analytics": 1,
        "disable_ttl": disable_ttl
    }
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
    """
    Delete a device using aiohttp.
    """
    url = f"{BASE_URL}/devices/{device_id}"
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.delete(url, headers=_get_headers()) as resp:
                if resp.status in (200, 204):
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


class ControlDService:
    """
    Class-based wrapper around Control D async functions.
    """
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