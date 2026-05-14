from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_DESIRED_RELEASE, DATA_COORDINATOR, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create update-available binary sensor backed by the coordinator."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([UpdaterUpdateAvailableBinarySensor(coordinator, entry)])


class UpdaterUpdateAvailableBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Indicates whether desired release differs from installed release."""
    _attr_has_entity_name = True
    _attr_name = "Update Available"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Bind entity to the config entry with a stable unique_id."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_update_available"

    @property
    def is_on(self):
        """True when backend reports a newer desired release."""
        return bool((self.coordinator.data or {}).get("update_available"))

    @property
    def extra_state_attributes(self):
        """Expose latest target release information."""
        desired = ((self.coordinator.data or {}).get(ATTR_DESIRED_RELEASE) or {})
        return {
            "latest_version": desired.get("version"),
            "channel": desired.get("channel"),
        }
