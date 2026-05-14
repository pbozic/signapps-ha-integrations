from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_API_TOKEN,
    CONF_CHANNEL,
    CONF_CUSTOMER_ID,
    CONF_DEVICE_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SERVER_URL,
    DEFAULT_CHANNEL,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)


class UpdaterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Collect initial integration configuration from the HA UI."""
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CUSTOMER_ID])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"{DEFAULT_NAME} ({user_input[CONF_CUSTOMER_ID]})",
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_SERVER_URL): str,
                vol.Required(CONF_CUSTOMER_ID): str,
                vol.Required(CONF_API_TOKEN): str,
                vol.Optional(CONF_DEVICE_NAME, default=DEFAULT_NAME): str,
                vol.Optional(CONF_CHANNEL, default=DEFAULT_CHANNEL): vol.In(["stable", "beta"]),
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=30)
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Expose options flow so settings can be edited after initial setup."""
        return UpdaterOptionsFlow(config_entry)


class UpdaterOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Store config entry for options editing."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Handle options updates for server/customer/token and poll interval."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data = {**self._config_entry.data, **self._config_entry.options}
        schema = vol.Schema(
            {
                vol.Optional(CONF_SERVER_URL, default=data.get(CONF_SERVER_URL, "")): str,
                vol.Optional(CONF_CUSTOMER_ID, default=data.get(CONF_CUSTOMER_ID, "")): str,
                vol.Optional(CONF_API_TOKEN, default=data.get(CONF_API_TOKEN, "")): str,
                vol.Optional(CONF_DEVICE_NAME, default=data.get(CONF_DEVICE_NAME, DEFAULT_NAME)): str,
                vol.Optional(CONF_CHANNEL, default=data.get(CONF_CHANNEL, DEFAULT_CHANNEL)): vol.In(
                    ["stable", "beta"]
                ),
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ): vol.All(vol.Coerce(int), vol.Range(min=30)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
