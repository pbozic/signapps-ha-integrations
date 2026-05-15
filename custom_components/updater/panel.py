from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import INTEGRATION_VERSION

_LOGGER = logging.getLogger(__name__)

PANEL_HASS_DATA_KEY = "updater_signapps_panel_registered"

PANEL_COMPONENT_NAME = "signapps-react"
PANEL_URL_PATH = "signapps"
PANEL_JS_URL = f"/local/signapps-dashboard/signapps-panel.js?v={INTEGRATION_VERSION}"
PANEL_ICON = "mdi:view-dashboard-variant"


def _load_react_runtime_config(config_dir: Path) -> dict | None:
    """Sync file read — call via async_add_executor_job only."""
    www_config = config_dir / "www" / "signapps-dashboard" / "config.json"
    if not www_config.exists():
        return None
    try:
        data = json.loads(www_config.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as err:
        _LOGGER.warning("Unable to read %s: %s", www_config, err)
        return None


def _panel_bundle_exists(config_dir: Path) -> bool:
    return (config_dir / "www" / "signapps-dashboard" / "signapps-panel.js").exists()


async def async_setup_signapps_panel(hass: HomeAssistant) -> bool:
    """Register the SignApps React panel via API (no configuration.yaml panel_custom block)."""
    if hass.data.get(PANEL_HASS_DATA_KEY):
        _LOGGER.debug("SignApps React panel already registered this session")
        return True

    config_dir = Path(hass.config.path())
    react_config = await hass.async_add_executor_job(_load_react_runtime_config, config_dir)
    if not react_config or not react_config.get("enabled"):
        _LOGGER.debug("SignApps React panel not enabled in runtime config")
        return False

    bundle_ok = await hass.async_add_executor_job(_panel_bundle_exists, config_dir)
    if not bundle_ok:
        _LOGGER.warning(
            "SignApps panel bundle missing at %s",
            config_dir / "www" / "signapps-dashboard" / "signapps-panel.js",
        )
        return False

    title = str(react_config.get("title") or "SignApps")

    try:
        from homeassistant.components import frontend
        from homeassistant.components.panel_custom import async_register_panel
    except ImportError as err:
        _LOGGER.error("panel_custom integration unavailable: %s", err)
        return False

    if frontend.async_panel_exists(hass, PANEL_URL_PATH):
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)

    try:
        await async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=PANEL_COMPONENT_NAME,
            sidebar_title=title,
            sidebar_icon=PANEL_ICON,
            js_url=PANEL_JS_URL,
            embed_iframe=True,
            require_admin=False,
        )
    except ValueError as err:
        if "Overwriting panel" in str(err):
            hass.data[PANEL_HASS_DATA_KEY] = True
            _LOGGER.debug("SignApps panel already registered in frontend: %s", err)
            return True
        raise

    hass.data[PANEL_HASS_DATA_KEY] = True
    _LOGGER.info("Registered SignApps React panel at /%s", PANEL_URL_PATH)
    return True
