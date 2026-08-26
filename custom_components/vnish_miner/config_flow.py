"""Config flow for the VNish ASIC Miner integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .vnish_client import VnishAuthError, VnishClient, VnishConnectionError, VnishError

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_API_KEY): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): int,
    }
)


async def _validate_input(hass: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Validate that the user input allows connecting to the miner.

    Returns the miner ``info`` payload (used to build the unique id).
    """
    session = async_get_clientsession(hass)
    client = VnishClient(
        host=data[CONF_HOST],
        api_key=data[CONF_API_KEY],
        port=data.get(CONF_PORT, DEFAULT_PORT),
        session=session,
    )
    info = await client.get_info()
    return info


class VnishConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for VNish ASIC Miner."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_input(self.hass, user_input)
            except VnishAuthError:
                errors["base"] = "invalid_auth"
            except VnishConnectionError:
                errors["base"] = "cannot_connect"
            except VnishError:
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception during config flow validation")
                errors["base"] = "unknown"
            else:
                mac = info.get("mac") or info.get("mac_address")
                unique_id = mac or f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                title = info.get("hostname") or user_input[CONF_HOST]
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "VnishOptionsFlowHandler":
        """Get the options flow for this handler."""
        return VnishOptionsFlowHandler(config_entry)


class VnishOptionsFlowHandler(OptionsFlow):
    """Handle options for the VNish ASIC Miner integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            validation_data = {
                CONF_HOST: self.config_entry.data[CONF_HOST],
                CONF_PORT: self.config_entry.data.get(CONF_PORT, DEFAULT_PORT),
                CONF_API_KEY: user_input[CONF_API_KEY],
            }
            try:
                await _validate_input(self.hass, validation_data)
            except VnishAuthError:
                errors["base"] = "invalid_auth"
            except VnishConnectionError:
                errors["base"] = "cannot_connect"
            except VnishError:
                errors["base"] = "unknown"
            else:
                new_data = {**self.config_entry.data, CONF_API_KEY: user_input[CONF_API_KEY]}
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                return self.async_create_entry(
                    title="",
                    data={CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL]},
                )

        current_api_key = self.config_entry.data.get(CONF_API_KEY, "")
        current_scan_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        options_schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY, default=current_api_key): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=current_scan_interval
                ): int,
            }
        )

        return self.async_show_form(
            step_id="init", data_schema=options_schema, errors=errors
        )
