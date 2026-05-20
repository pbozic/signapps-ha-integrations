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
PANEL_JS_PATH = "/local/signapps-dashboard/signapps-panel.js"
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


def _panel_js_cache_bust(config_dir: Path) -> str:
    """Cache-bust query for signapps-panel.js (dashboard build version preferred)."""
    version_file = config_dir / "www" / "signapps-dashboard" / "version.txt"
    if version_file.exists():
        try:
            value = version_file.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError as err:
            _LOGGER.debug("Unable to read %s: %s", version_file, err)

    manifest_path = config_dir / "updater" / "state" / "release-manifest.json"
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                dash = payload.get("signapps_dashboard_version")
                if isinstance(dash, str) and dash.strip():
                    return dash.strip()
        except (OSError, json.JSONDecodeError) as err:
            _LOGGER.debug("Unable to read %s: %s", manifest_path, err)

    return INTEGRATION_VERSION


def _panel_js_url(config_dir: Path) -> str:
    bust = _panel_js_cache_bust(config_dir)
    return f"{PANEL_JS_PATH}?v={bust}"


async def async_setup_signapps_panel(hass: HomeAssistant) -> bool:
    """Register the Signapps React panel via API (no configuration.yaml panel_custom block)."""
    if hass.data.get(PANEL_HASS_DATA_KEY):
        _LOGGER.debug("Signapps React panel already registered this session")
        return True

    config_dir = Path(hass.config.path())
    react_config = await hass.async_add_executor_job(_load_react_runtime_config, config_dir)
    if not react_config or not react_config.get("enabled"):
        _LOGGER.debug("Signapps React panel not enabled in runtime config")
        return False

    bundle_ok = await hass.async_add_executor_job(_panel_bundle_exists, config_dir)
    if not bundle_ok:
        _LOGGER.warning(
            "Signapps panel bundle missing at %s",
            config_dir / "www" / "signapps-dashboard" / "signapps-panel.js",
        )
        return False

    title = str(react_config.get("title") or "Signapps")
    js_url = await hass.async_add_executor_job(_panel_js_url, config_dir)

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
            js_url=js_url,
            embed_iframe=True,
            require_admin=False,
        )
    except ValueError as err:
        if "Overwriting panel" in str(err):
            hass.data[PANEL_HASS_DATA_KEY] = True
            _LOGGER.debug("Signapps panel already registered in frontend: %s", err)
            return True
        raise

    hass.data[PANEL_HASS_DATA_KEY] = True
    _LOGGER.info("Registered Signapps React panel at /%s", PANEL_URL_PATH)
    return True
