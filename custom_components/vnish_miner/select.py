"""Select platform for the VNish ASIC Miner integration (overclock presets)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VnishDataUpdateCoordinator
from .entity import VnishEntity
from .vnish_client import VnishError

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the VNish preset select entity."""
    coordinator: VnishDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities([VnishPresetSelectEntity(coordinator)])


class VnishPresetSelectEntity(VnishEntity, SelectEntity):
    """Select entity to switch the miner's active overclock/autotune preset."""

    _attr_icon = "mdi:tune"
    _attr_translation_key = "preset"

    def __init__(self, coordinator: VnishDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "preset")

    @property
    def options(self) -> list[str]:
        """Return the list of presets available on the miner."""
        return self._data.presets

    @property
    def current_option(self) -> str | None:
        """Return the currently active preset."""
        return self._data.active_preset

    async def async_select_option(self, option: str) -> None:
        """Switch the miner to the requested preset."""
        try:
            await self.coordinator.client.set_preset(option)
        except VnishError as err:
            raise HomeAssistantError(f"Failed to set preset {option}: {err}") from err
        await self.coordinator.async_request_refresh()
