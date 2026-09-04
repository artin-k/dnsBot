# services/adguard_client.py
from __future__ import annotations

import asyncio
from typing import Any
import aiohttp


class AdGuardHomeClient:
    """Non-blocking, resilient API client for AdGuard Home."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = aiohttp.BasicAuth(username, password)
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

        # Distributed locks preventing Read-Modify-Write race conditions
        self._dns_config_lock = asyncio.Lock()
        self._access_lock = asyncio.Lock()
        self._filter_lock = asyncio.Lock()

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                auth=self._auth,
                timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _execute(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        session = await self.get_session()
        url = f"{self._base_url}{path}"

        try:
            async with session.request(method, url, json=payload) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise AdGuardAPIError(resp.status, text)

                # 204 or empty OK responses
                if resp.status == 200 and resp.content_length == 0:
                    return {}
                if resp.content_type == "application/json":
                    return await resp.json()
                return await resp.text()
        except asyncio.TimeoutError as exc:
            raise AdGuardNetworkError(f"Request timed out for path: {path}") from exc
        except aiohttp.ClientError as exc:
            raise AdGuardNetworkError(f"I/O error communicating with AdGuard: {exc}") from exc

    # =========================================================================
    # DNS Config: Upstreams, Cache, Limits (Read-Modify-Write)
    # =========================================================================
    async def modify_dns_config(self, **mutations: Any) -> dict[str, Any]:
        """Safely mutates DNS configurations without wiping unmentioned fields.

        Keyword Args:
            upstream_dns: list[str]
            fallback_dns: list[str]
            bootstrap_dns: list[str]
            rate_limit: int
            cache_size: int
            cache_ttl_min: int
            cache_ttl_max: int
        """
        async with self._dns_config_lock:
            # 1. READ
            config: dict[str, Any] = await self._execute("GET", "/control/dns_config")

            # 2. VALIDATE & MODIFY
            if "upstream_dns" in mutations:
                config["upstream_dns"] = [
                    validate_upstream_spec(u) for u in mutations["upstream_dns"]
                ]
            if "fallback_dns" in mutations:
                config["fallback_dns"] = [
                    validate_upstream_spec(f) for f in mutations["fallback_dns"]
                ]
            if "bootstrap_dns" in mutations:
                config["bootstrap_dns"] = [
                    validate_upstream_spec(b) for b in mutations["bootstrap_dns"]
                ]
            if "cache_size" in mutations:
                config["cache_size"] = int(mutations["cache_size"])
            if "rate_limit" in mutations:
                config["rate_limit"] = int(mutations["rate_limit"])

            # 3. WRITE
            await self._execute("POST", "/control/dns_config", payload=config)
            return config

    # =========================================================================
    # Access Control: Lists (Read-Modify-Write)
    # =========================================================================
    async def toggle_access_client(
        self,
        target: str,
        category: str = "allowed_clients",
        remove: bool = False,
    ) -> None:
        """Applies atomic alterations to ACL lists (allowed/disallowed IPs)."""
        valid_categories = {"allowed_clients", "disallowed_clients", "blocked_hosts"}
        if category not in valid_categories:
            raise ValueError(f"Category must be one of {valid_categories}")

        sanitized_target = sanitize_network_target(target)

        async with self._access_lock:
            # 1. READ
            data: dict[str, list[str]] = await self._execute("GET", "/control/access/list")

            # 2. MODIFY
            current_list = set(data.get(category, []))
            if remove:
                current_list.discard(sanitized_target)
            else:
                current_list.add(sanitized_target)

            data[category] = sorted(current_list)

            # 3. WRITE
            await self._execute("POST", "/control/access/set", payload=data)

    # =========================================================================
    # Filtering: Custom User Rules (Read-Modify-Write)
    # =========================================================================
    async def append_filtering_rule(self, adblock_rule: str) -> None:
        """Atomically appends an adblock/filtering rule to custom user rules."""
        rule_sanitized = adblock_rule.strip()
        if not rule_sanitized:
            raise ValidationError("Filtering rule cannot be empty.")

        async with self._filter_lock:
            # 1. READ
            status: dict[str, Any] = await self._execute("GET", "/control/filtering/status")
            existing_rules: list[str] = status.get("user_rules", [])

            # 2. MODIFY
            if rule_sanitized not in existing_rules:
                existing_rules.append(rule_sanitized)
            else:
                return  # Rule exists; avoid unnecessary writes.

            # 3. WRITE
            await self._execute("POST", "/control/filtering/set_rules", payload={"rules": existing_rules})

    # =========================================================================
    # Dynamic Client Provisioning
    # =========================================================================
    async def provision_client(
        self,
        name: str,
        identifiers: list[str],
        upstreams: list[str] | None = None,
    ) -> None:
        """Provisions a persistent client configuration directly."""
        sanitized_ids = [sanitize_network_target(i) for i in identifiers]
        validated_upstreams = [validate_upstream_spec(u) for u in (upstreams or [])]

        payload = {
            "name": name.strip(),
            "ids": sanitized_ids,
            "use_global_settings": len(validated_upstreams) == 0,
            "filtering_enabled": True,
            "upstreams": validated_upstreams,
        }
        await self._execute("POST", "/control/clients/add", payload=payload)