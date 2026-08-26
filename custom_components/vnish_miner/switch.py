"""Switch platform for the VNish ASIC Miner integration."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VnishDataUpdateCoordinator
from .entity import VnishEntity
from .vnish_client import VnishError

_LOGGER = logging.getLogger(__name__)

PAUSED_STATES = {"paused", "pause", "stopped", "stopping"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the VNish mining switch."""
    coordinator: VnishDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities([VnishMiningSwitchEntity(coordinator)])


class VnishMiningSwitchEntity(VnishEntity, SwitchEntity):
    """Switch entity to pause/resume mining on the ASIC miner."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:pickaxe"
    _attr_translation_key = "mining"

    def __init__(self, coordinator: VnishDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "mining")

    @property
    def is_on(self) -> bool | None:
        """Return True if the miner is actively mining (not paused)."""
        status = self._data.miner_status
        if status is None:
            return None
        return status.lower() not in PAUSED_STATES

    async def async_turn_on(self, **kwargs) -> None:
        """Resume mining."""
        try:
            await self.coordinator.client.resume_mining()
        except VnishError as err:
            _LOGGER.error("Failed to resume mining: %s", err)
            raise
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Pause mining."""
        try:
            await self.coordinator.client.pause_mining()
        except VnishError as err:
            _LOGGER.error("Failed to pause mining: %s", err)
            raise
        await self.coordinator.async_request_refresh()
