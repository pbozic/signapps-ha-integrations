"""Write Cloudflare tunnel material for the SignApps Tunnel add-on (see ha-cloudflare-plan.md §19)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def atomic_write_tunnel_credentials(hass: HomeAssistant, payload: dict[str, Any]) -> None:
    """Atomically write /config/signapps_tunnel/credentials.json (integration + add-on contract)."""
    base = Path(hass.config.path("signapps_tunnel"))
    base.mkdir(parents=True, exist_ok=True)
    dest = base / "credentials.json"
    tmp = dest.with_suffix(".tmp")
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)
    try:
        dest.chmod(0o600)
    except OSError:
        _LOGGER.debug("Could not chmod tunnel credentials file")
