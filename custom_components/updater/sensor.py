from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_DESIRED_RELEASE, ATTR_LAST_CHECKIN, DATA_COORDINATOR, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create updater sensors backed by the shared coordinator."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        [
            UpdaterInstalledVersionSensor(coordinator, entry),
            UpdaterLatestVersionSensor(coordinator, entry),
            UpdaterLastCheckinSensor(coordinator, entry),
        ]
    )


class UpdaterBaseSensor(CoordinatorEntity, SensorEntity):
    """Base sensor exposing shared release metadata attributes."""
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, key: str) -> None:
        """Bind entity to config entry and create stable unique_id."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def extra_state_attributes(self):
        """Expose desired release context on all updater sensors."""
        data = self.coordinator.data or {}
        desired = data.get(ATTR_DESIRED_RELEASE) or {}
        return {
            "desired_channel": desired.get("channel"),
            "artifact_url": desired.get("artifact_url"),
            "sha256": desired.get("sha256"),
        }


class UpdaterInstalledVersionSensor(UpdaterBaseSensor):
    """Reports the version currently installed on this device."""
    _attr_name = "Installed Version"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "installed_version")

    @property
    def native_value(self):
        """Return installed version from coordinator state."""
        return (self.coordinator.data or {}).get("installed_version")


class UpdaterLatestVersionSensor(UpdaterBaseSensor):
    """Reports the latest version targeted by the control plane."""
    _attr_name = "Latest Version"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "latest_version")

    @property
    def native_value(self):
        """Return desired release version from coordinator state."""
        desired = (self.coordinator.data or {}).get(ATTR_DESIRED_RELEASE) or {}
        return desired.get("version")


class UpdaterLastCheckinSensor(UpdaterBaseSensor):
    """Reports timestamp of the most recent successful check-in."""
    _attr_name = "Last Check-in"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "last_checkin")

    @property
    def native_value(self):
        """Return stored check-in timestamp."""
        return (self.coordinator.data or {}).get(ATTR_LAST_CHECKIN)
