# app/services/adguard.py
from __future__ import annotations

import asyncio
import ipaddress
import re
from typing import Any
import aiohttp
import structlog

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)

UPSTREAM_PATTERN = re.compile(
    r"^(?:(?:https|tls|quic|tcp|udp)://)?"
    r"(?:[a-zA-Z0-9-._]+|\[[0-9a-fA-F:]+\])"
    r"(?::\d{1,5})?"
    r"(?:/[a-zA-Z0-9-._~%!$&'()*+,;=:@/]*)*$"
    r"|^sdns://[a-zA-Z0-9-_]+$"
)


def validate_network_target(target: str) -> str:
    """Validates IPv4/IPv6 address or CIDR notation."""
    cleaned = target.strip()
    try:
        if "/" in cleaned:
            return str(ipaddress.ip_network(cleaned, strict=False))
        return str(ipaddress.ip_address(cleaned))
    except ValueError as exc:
        raise ValueError(f"Invalid IP/CIDR target: {cleaned}") from exc


def validate_upstream_spec(upstream: str) -> str:
    cleaned = upstream.strip()
    if not UPSTREAM_PATTERN.match(cleaned):
        raise ValueError(f"Malformed upstream specification: {cleaned}")
    return cleaned


class AdGuardHomeService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._base_url = self.settings.adguard_url.rstrip("/")
        self._auth = (
            aiohttp.BasicAuth(self.settings.adguard_username, self.settings.adguard_password)
            if self.settings.adguard_username
            else None
        )
        self._timeout = aiohttp.ClientTimeout(total=8.0)
        self._access_lock = asyncio.Lock()
        self._dns_lock = asyncio.Lock()

    def is_configured(self) -> bool:
        return bool(self.settings.adguard_url and self.settings.adguard_username)

    async def _request(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.is_configured():
            logger.warning("adguard_not_configured_skipping_request", endpoint=endpoint)
            return None

        url = f"{self._base_url}{endpoint}"
        async with aiohttp.ClientSession(auth=self._auth, timeout=self._timeout) as session:
            try:
                async with session.request(method, url, json=payload) as resp:
                    if resp.status >= 400:
                        err_text = await resp.text()
                        logger.error("adguard_api_error", status=resp.status, text=err_text, endpoint=endpoint)
                        return None

                    if resp.status in (200, 201):
                        if resp.content_type == "application/json":
                            return await resp.json()
                        return {"status": "ok"}
                    return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.error("adguard_network_error", endpoint=endpoint, error=str(exc))
                return None

    async def _client_request(self, endpoint: str, payload: dict[str, Any]) -> tuple[int | None, str]:
        if not self.is_configured():
            return 200, "not configured"

        url = f"{self._base_url}{endpoint}"
        async with aiohttp.ClientSession(auth=self._auth, timeout=self._timeout) as session:
            try:
                async with session.post(url, json=payload) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        logger.warning(
                            "adguard_client_api_error",
                            status=resp.status,
                            text=text,
                            endpoint=endpoint,
                        )
                    return resp.status, text
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.error("adguard_client_network_error", endpoint=endpoint, error=str(exc))
                return None, str(exc)

    # --- Access Control List (Whitelist Authorization & Deauthorization) ---
    async def allow_client_ip(self, ip_address: str) -> bool:
        """Authorizes a client IP in AdGuard Home's allowed_clients list."""
        if not self.is_configured():
            return True

        try:
            valid_ip = validate_network_target(ip_address)
        except ValueError as e:
            logger.error("invalid_ip_for_adguard", ip=ip_address, error=str(e))
            return False

        async with self._access_lock:
            # 1. READ current ACL
            data = await self._request("GET", "/control/access/list")
            if data is None or not isinstance(data, dict):
                logger.error("failed_to_fetch_adguard_access_list", ip=valid_ip)
                return False

            # Safe conversion handling null/None from AdGuard API
            allowed = set(data.get("allowed_clients") or [])
            if valid_ip in allowed:
                logger.info("adguard_ip_already_allowed", ip=valid_ip)
                return True

            # 2. MODIFY
            allowed.add(valid_ip)
            data["allowed_clients"] = sorted(allowed)
            data["disallowed_clients"] = data.get("disallowed_clients") or []
            data["blocked_hosts"] = data.get("blocked_hosts") or []

            # 3. WRITE
            res = await self._request("POST", "/control/access/set", payload=data)
            if res is not None:
                logger.info("adguard_ip_authorized_successfully", ip=valid_ip)
                return True
            return False

    async def deauthorize_client_ip(self, ip_address: str) -> bool:
        """Deauthorizes (removes) a client IP from AdGuard Home's allowed_clients list."""
        if not self.is_configured():
            return True

        try:
            valid_ip = validate_network_target(ip_address)
        except ValueError:
            return True

        async with self._access_lock:
            # 1. READ current ACL
            data = await self._request("GET", "/control/access/list")
            if data is None or not isinstance(data, dict):
                logger.error("failed_to_fetch_adguard_access_list_for_deauth", ip=valid_ip)
                return False

            # Safe conversion handling null/None from AdGuard API
            allowed = set(data.get("allowed_clients") or [])
            if valid_ip not in allowed:
                return True  # Already absent

            # 2. REMOVE
            allowed.discard(valid_ip)
            data["allowed_clients"] = sorted(allowed)
            data["disallowed_clients"] = data.get("disallowed_clients") or []
            data["blocked_hosts"] = data.get("blocked_hosts") or []

            # 3. WRITE
            res = await self._request("POST", "/control/access/set", payload=data)
            if res is not None:
                logger.info("adguard_ip_deauthorized_successfully", ip=valid_ip)
                return True
            return False

    async def sync_user_client(self, service_id: int, username: str | None, ip_address: str | None = None) -> bool:
        """
        Creates or strictly updates a dedicated client in AdGuard Home.
        Enforces exactly ONE active IP per user by overwriting the 'ids' array.
        """
        if not self.is_configured():
            return True

        clean_username = re.sub(r"[^A-Za-z0-9_-]+", "_", (username or "").strip()).strip("_")
        if not clean_username:
            clean_username = f"u{service_id}"

        client_name = f"User_{service_id}_{clean_username}"

        if ip_address:
            try:
                strict_ids = [validate_network_target(ip_address)]
            except ValueError as exc:
                logger.error(
                    "invalid_ip_for_adguard_client_sync",
                    service_id=service_id,
                    ip=ip_address,
                    error=str(exc),
                )
                return False
        else:
            strict_ids = []

        payload = {
            "name": client_name,
            "ids": strict_ids,
            "use_global_settings": True,
            "filtering_enabled": True,
            "parental_enabled": False,
            "safesearch_enabled": False,
            "safebrowsing_enabled": False,
            "use_global_blocked_services": True,
            "upstreams": [],
        }

        add_status, add_text = await self._client_request("/control/clients/add", payload)
        if add_status in (200, 201):
            logger.info("adguard_client_created", name=client_name, ids=strict_ids)
            return True

        if add_status != 400:
            logger.error(
                "adguard_client_add_failed_not_retryable",
                name=client_name,
                status=add_status,
                text=add_text,
            )
            return False

        update_payload = {
            "name": client_name,
            "data": payload,
        }
        update_status, update_text = await self._client_request("/control/clients/update", update_payload)
        if update_status in (200, 201):
            logger.info("adguard_client_strictly_updated", name=client_name, ids=strict_ids)
            return True

        logger.error(
            "adguard_client_sync_completely_failed",
            name=client_name,
            add_status=add_status,
            add_text=add_text,
            update_status=update_status,
            update_text=update_text,
        )
        return False
