from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import ClientSession


class UpdaterApi:
    def __init__(self, session: ClientSession, server_url: str) -> None:
        """Create API client and normalize URL to always target `/api` base path."""
        self._session = session
        normalized = server_url.rstrip("/")
        self._server_url = normalized if normalized.endswith("/api") else f"{normalized}/api"

    async def register_device(
        self,
        *,
        api_token: str,
        customer_id: str,
        device_name: str,
        channel: str,
        installation_id: str,
        ha_version: str,
        integration_version: str,
    ) -> dict[str, Any]:
        """Register this HA instance as a device for the provided customer."""
        return await self._request(
            "POST",
            "/devices",
            token=api_token,
            json={
                "customer_id": customer_id,
                "name": device_name,
                "channel": channel,
                "installation_id": installation_id,
                "ha_version": ha_version,
                "integration_version": integration_version,
            },
        )

    async def get_desired_release(self, *, device_id: str, device_token: str) -> dict[str, Any]:
        """Fetch target release metadata for the registered device."""
        try:
            return await self._request(
                "GET",
                f"/devices/{device_id}/desired-release",
                token=device_token,
            )
        except RuntimeError as err:
            # No release assigned yet should not break coordinator startup/polling.
            if "No release available for this device" in str(err):
                return {}
            raise

    async def get_tunnel_credentials(
        self, *, device_id: str, device_token: str
    ) -> dict[str, Any] | None:
        """Fetch tunnel hostname + JWT for SignApps Tunnel add-on. None if not provisioned (404)."""
        try:
            return await self._request(
                "GET",
                f"/devices/{device_id}/tunnel-credentials",
                token=device_token,
            )
        except RuntimeError as err:
            err_s = str(err).lower()
            if "404" in str(err) or "not provisioned" in err_s or "tunnel not" in err_s:
                return None
            raise

    async def checkin(
        self,
        *,
        device_id: str,
        device_token: str,
        installed_version: str,
        ha_version: str,
        uptime_seconds: int | None,
        load_1m: float | None,
        cpu_count: int | None,
        mem_total_mb: int | None,
        mem_available_mb: int | None,
        status: str,
    ) -> dict[str, Any]:
        """Report current install/health status for the registered device."""
        return await self._request(
            "POST",
            f"/devices/{device_id}/checkin",
            token=device_token,
            json={
                "installed_version": installed_version,
                "ha_version": ha_version,
                "uptime_seconds": uptime_seconds,
                "load_1m": load_1m,
                "cpu_count": cpu_count,
                "mem_total_mb": mem_total_mb,
                "mem_available_mb": mem_available_mb,
                "status": status,
            },
        )

    async def _request(self, method: str, path: str, *, token: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        """Issue authenticated API request and normalize backend errors."""
        headers = {"Authorization": f"Bearer {token}"}
        async with self._session.request(
            method,
            f"{self._server_url}{path}",
            headers=headers,
            json=json,
            timeout=20,
        ) as response:
            body = await response.json(content_type=None)
            if response.status >= 400:
                error = body.get("error", f"HTTP {response.status}") if isinstance(body, dict) else str(body)
                raise RuntimeError(error)
            return body

    async def download_file(self, *, artifact_url: str, target_path: str) -> None:
        """Download release artifact (zip) from presigned URL to local path."""
        async with self._session.get(artifact_url, timeout=60) as response:
            if response.status >= 400:
                raise RuntimeError(f"Artifact download failed: HTTP {response.status}")
            data = await response.read()

        def _write_bytes() -> None:
            with open(target_path, "wb") as file:
                file.write(data)

        await asyncio.get_running_loop().run_in_executor(None, _write_bytes)
