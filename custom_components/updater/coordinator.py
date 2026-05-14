from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UpdaterApi
from .tunnel_credentials import atomic_write_tunnel_credentials


class UpdaterCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api: UpdaterApi,
        device_id: str,
        device_token: str,
        installed_version: str,
        scan_interval_seconds: int,
    ) -> None:
        """Initialize periodic updater sync for one registered device."""
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="updater_coordinator",
            update_interval=timedelta(seconds=scan_interval_seconds),
        )
        self._api = api
        self._device_id = device_id
        self._device_token = device_token
        self._installed_version = installed_version
        self.last_checkin: str | None = None

    def _collect_system_metrics(self) -> dict[str, int | float | None]:
        """Collect best-effort system metrics from HA host."""
        uptime_seconds: int | None = None
        load_1m: float | None = None
        cpu_count: int | None = os.cpu_count()
        mem_total_mb: int | None = None
        mem_available_mb: int | None = None

        try:
            with open("/proc/uptime", "r", encoding="utf-8") as file:
                uptime_seconds = int(float(file.read().split()[0]))
        except Exception:
            uptime_seconds = None

        try:
            load_1m = float(os.getloadavg()[0])
        except Exception:
            load_1m = None

        try:
            meminfo: dict[str, int] = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as file:
                for line in file:
                    key, raw = line.split(":", 1)
                    parts = raw.strip().split()
                    if not parts:
                        continue
                    # /proc/meminfo values are in kB
                    meminfo[key] = int(parts[0])
            if "MemTotal" in meminfo:
                mem_total_mb = int(meminfo["MemTotal"] / 1024)
            if "MemAvailable" in meminfo:
                mem_available_mb = int(meminfo["MemAvailable"] / 1024)
        except Exception:
            mem_total_mb = None
            mem_available_mb = None

        return {
            "uptime_seconds": uptime_seconds,
            "load_1m": load_1m,
            "cpu_count": cpu_count,
            "mem_total_mb": mem_total_mb,
            "mem_available_mb": mem_available_mb,
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch desired release, send check-in, and expose computed integration state."""
        try:
            desired = await self._api.get_desired_release(
                device_id=self._device_id,
                device_token=self._device_token,
            )
        except Exception as err:
            raise UpdateFailed(f"Unable to fetch desired release: {err}") from err

        desired_version = desired.get("version")
        status = "update_available" if desired_version and desired_version != self._installed_version else "up_to_date"
        if not desired_version:
            self.logger.debug("No desired release available yet for device %s", self._device_id)

        try:
            metrics = self._collect_system_metrics()
            await self._api.checkin(
                device_id=self._device_id,
                device_token=self._device_token,
                installed_version=self._installed_version,
                ha_version=HA_VERSION,
                uptime_seconds=metrics["uptime_seconds"],
                load_1m=metrics["load_1m"],
                cpu_count=metrics["cpu_count"],
                mem_total_mb=metrics["mem_total_mb"],
                mem_available_mb=metrics["mem_available_mb"],
                status=status,
            )
            self.last_checkin = datetime.now(timezone.utc).isoformat()
        except Exception as err:
            raise UpdateFailed(f"Unable to send checkin: {err}") from err

        try:
            raw = await self._api.get_tunnel_credentials(
                device_id=self._device_id,
                device_token=self._device_token,
            )
            if raw is None:
                payload: dict[str, Any] = {"schema_version": 1, "error": "not_provisioned"}
            else:
                payload = {
                    "schema_version": 1,
                    "hostname": raw["hostname"],
                    "tunnel_token": raw["tunnel_token"],
                    "updated_at": raw.get("updated_at", ""),
                }
            await self.hass.async_add_executor_job(
                atomic_write_tunnel_credentials, self.hass, payload
            )
        except Exception as err:
            self.logger.debug("Tunnel credentials sync skipped: %s", err)

        return {
            "device_id": self._device_id,
            "installed_version": self._installed_version,
            "desired_release": desired,
            "update_available": status == "update_available",
            "last_checkin": self.last_checkin,
        }
