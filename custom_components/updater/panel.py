from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PANEL_COMPONENT_NAME = "signapps-react"
PANEL_URL_PATH = "signapps"
PANEL_MODULE_URL = "/local/signapps-dashboard/signapps-panel.js"
PANEL_ICON = "mdi:view-dashboard-variant"


def _load_react_runtime_config(config_dir: Path) -> dict | None:
    www_config = config_dir / "www" / "signapps-dashboard" / "config.json"
    if www_config.exists():
        try:
            data = json.loads(www_config.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as err:
            _LOGGER.warning("Unable to read %s: %s", www_config, err)
    return None


async def async_setup_signapps_panel(hass: HomeAssistant) -> bool:
    """Register the SignApps React panel via API (no configuration.yaml panel_custom block)."""
    config_dir = Path(hass.config.path())
    react_config = _load_react_runtime_config(config_dir)
    if not react_config or not react_config.get("enabled"):
        _LOGGER.debug("SignApps React panel not enabled in runtime config")
        return False

    module_path = config_dir / "www" / "signapps-dashboard" / "signapps-panel.js"
    if not module_path.exists():
        _LOGGER.warning("SignApps panel bundle missing at %s", module_path)
        return False

    title = str(react_config.get("title") or "SignApps")

    try:
        from homeassistant.components.panel_custom import async_register_panel
    except ImportError as err:
        _LOGGER.error("panel_custom integration unavailable: %s", err)
        return False

    await async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT_NAME,
        sidebar_title=title,
        sidebar_icon=PANEL_ICON,
        module_url=PANEL_MODULE_URL,
        embed_iframe=True,
        require_admin=False,
    )
    _LOGGER.info("Registered SignApps React panel at /%s", PANEL_URL_PATH)
    return True
