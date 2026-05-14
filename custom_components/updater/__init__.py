from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.const import __version__ as HA_VERSION
import voluptuous as vol

from .api import UpdaterApi
from .const import (
    CONF_API_TOKEN,
    CONF_CHANNEL,
    CONF_CUSTOMER_ID,
    CONF_DEVICE_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SERVER_URL,
    DATA_API,
    DATA_COORDINATOR,
    DATA_ENTRY,
    DATA_STORE,
    DATA_STATE,
    DEFAULT_CHANNEL,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    INSTALLATION_STORE_KEY,
    INTEGRATION_VERSION,
    LOCAL_VALUES_FILE,
    PLATFORMS,
    SERVICE_INSTALL_DESIRED_RELEASE,
    SERVICE_RESTORE_LAST_BACKUP,
    STORE_KEY,
    STORE_VERSION,
)
from .coordinator import UpdaterCoordinator
from .installer import install_desired_release, restore_last_backup

_LOGGER = logging.getLogger(__name__)


def _entry_value(entry: ConfigEntry, key: str, default: Any = None) -> Any:
    """Read config value with options overriding initial entry data."""
    return entry.options.get(key, entry.data.get(key, default))


def _load_local_values(hass: HomeAssistant) -> dict[str, Any]:
    """Load optional local values file from HA config dir."""
    path = Path(hass.config.path(LOCAL_VALUES_FILE))
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception as err:
        _LOGGER.warning("Unable to read %s: %s", LOCAL_VALUES_FILE, err)
        return {}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up updater integration, register/load device credentials, and start polling."""
    hass.data.setdefault(DOMAIN, {})

    server_url = _entry_value(entry, CONF_SERVER_URL)
    customer_id = _entry_value(entry, CONF_CUSTOMER_ID)
    api_token = _entry_value(entry, CONF_API_TOKEN)
    device_name = _entry_value(entry, CONF_DEVICE_NAME, DEFAULT_NAME)
    channel = _entry_value(entry, CONF_CHANNEL, DEFAULT_CHANNEL)
    scan_interval = int(_entry_value(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    # Optional local override file in /config/updater.values.json.
    local_values = _load_local_values(hass)
    server_url = local_values.get(CONF_SERVER_URL, server_url)
    customer_id = local_values.get(CONF_CUSTOMER_ID, customer_id)
    api_token = local_values.get(CONF_API_TOKEN, api_token)
    device_name = local_values.get(CONF_DEVICE_NAME, device_name)
    channel = local_values.get(CONF_CHANNEL, channel)
    scan_interval = int(local_values.get(CONF_SCAN_INTERVAL, scan_interval))

    session = async_get_clientsession(hass)
    api = UpdaterApi(session, server_url)
    installation_store = Store[dict[str, Any]](hass, STORE_VERSION, INSTALLATION_STORE_KEY)
    installation_data = await installation_store.async_load() or {}
    installation_id = installation_data.get("installation_id")
    if not installation_id:
        installation_id = str(uuid4())
        await installation_store.async_save({"installation_id": installation_id})
    store = Store[dict[str, Any]](hass, STORE_VERSION, f"{STORE_KEY}_{entry.entry_id}")
    state = await store.async_load() or {}
    installed_version = state.get("installed_version", "0.1.0")

    if state.get("last_update_status") == "pending_restart" and state.get("pending_version"):
        # If HA booted again after a restart request, treat staged update as successful.
        installed_version = state["pending_version"]
        state["installed_version"] = installed_version
        state["pending_version"] = None
        state["last_update_status"] = "installed"
        await store.async_save(state)

    device_id = state.get("device_id")
    device_token = state.get("device_token")

    if not device_id or not device_token:
        try:
            registration = await api.register_device(
                api_token=api_token,
                customer_id=customer_id,
                device_name=device_name,
                channel=channel,
                installation_id=installation_id,
                ha_version=HA_VERSION,
                integration_version=INTEGRATION_VERSION,
            )
        except Exception as err:
            raise ConfigEntryNotReady(f"Unable to register device: {err}") from err
        device_id = registration["device_id"]
        device_token = registration["token"]
        state.update({"device_id": device_id, "device_token": device_token})
        await store.async_save(state)

    coordinator = UpdaterCoordinator(
        hass,
        api=api,
        device_id=device_id,
        device_token=device_token,
        installed_version=installed_version,
        scan_interval_seconds=scan_interval,
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Unable to fetch updater data: {err}") from err

    install_lock = asyncio.Lock()

    async def _auto_install_if_needed() -> None:
        if install_lock.locked():
            return

        desired = (coordinator.data or {}).get("desired_release") or {}
        desired_version = desired.get("version")
        if not desired_version:
            return
        if state.get("installed_version") == desired_version:
            return
        if state.get("pending_version") == desired_version:
            return

        async with install_lock:
            try:
                await install_desired_release(
                    hass,
                    api=api,
                    coordinator=coordinator,
                    state=state,
                    store=store,
                    entry=entry,
                )
            except Exception as err:
                _LOGGER.error("Auto install failed for version %s: %s", desired_version, err)

    def _schedule_auto_install() -> None:
        hass.async_create_task(_auto_install_if_needed())

    coordinator.async_add_listener(_schedule_auto_install)
    _schedule_auto_install()

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API: api,
        DATA_COORDINATOR: coordinator,
        DATA_STATE: state,
        DATA_STORE: store,
        DATA_ENTRY: entry,
    }

    if not hass.services.has_service(DOMAIN, SERVICE_INSTALL_DESIRED_RELEASE):
        async def _handle_install(call):
            target_entry_id = call.data.get("entry_id", entry.entry_id)
            runtime = hass.data[DOMAIN].get(target_entry_id)
            if not runtime:
                raise RuntimeError(f"Entry not found: {target_entry_id}")
            return await install_desired_release(
                hass,
                api=runtime[DATA_API],
                coordinator=runtime[DATA_COORDINATOR],
                state=runtime[DATA_STATE],
                store=runtime[DATA_STORE],
                entry=runtime[DATA_ENTRY],
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_INSTALL_DESIRED_RELEASE,
            _handle_install,
            schema=vol.Schema({vol.Optional("entry_id"): str}),
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_RESTORE_LAST_BACKUP):
        async def _handle_restore(call):
            target_entry_id = call.data.get("entry_id", entry.entry_id)
            runtime = hass.data[DOMAIN].get(target_entry_id)
            if not runtime:
                raise RuntimeError(f"Entry not found: {target_entry_id}")
            return await restore_last_backup(
                hass,
                state=runtime[DATA_STATE],
                store=runtime[DATA_STORE],
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_RESTORE_LAST_BACKUP,
            _handle_restore,
            schema=vol.Schema({vol.Optional("entry_id"): str}),
            supports_response=SupportsResponse.ONLY,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload updater integration platforms and remove runtime state."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            if hass.services.has_service(DOMAIN, SERVICE_INSTALL_DESIRED_RELEASE):
                hass.services.async_remove(DOMAIN, SERVICE_INSTALL_DESIRED_RELEASE)
            if hass.services.has_service(DOMAIN, SERVICE_RESTORE_LAST_BACKUP):
                hass.services.async_remove(DOMAIN, SERVICE_RESTORE_LAST_BACKUP)
    return unload_ok
