"""Base entity for the VNish ASIC Miner integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import VnishData, VnishDataUpdateCoordinator


class VnishEntity(CoordinatorEntity[VnishDataUpdateCoordinator]):
    """Base entity tying a VNish entity to its coordinator and device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: VnishDataUpdateCoordinator, unique_id_suffix: str
    ) -> None:
        super().__init__(coordinator)
        base_id = coordinator.data.mac if coordinator.data else coordinator.entry.entry_id
        self._attr_unique_id = f"{base_id}_{unique_id_suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this entity."""
        return self.coordinator.device_info

    @property
    def _data(self) -> VnishData:
        """Return the coordinator's current data."""
        return self.coordinator.data
