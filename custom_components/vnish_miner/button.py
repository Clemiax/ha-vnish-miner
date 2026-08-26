"""Button platform for the VNish ASIC Miner integration."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VnishDataUpdateCoordinator
from .entity import VnishEntity
from .vnish_client import VnishClient, VnishError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class VnishButtonEntityDescription(ButtonEntityDescription):
    """Describes a VNish button entity."""

    press_fn: Callable[[VnishClient], Awaitable[None]]


BUTTON_DESCRIPTIONS: tuple[VnishButtonEntityDescription, ...] = (
    VnishButtonEntityDescription(
        key="restart_mining",
        translation_key="restart_mining",
        icon="mdi:restart",
        press_fn=lambda client: client.restart_mining(),
    ),
    VnishButtonEntityDescription(
        key="reboot_hardware",
        translation_key="reboot_hardware",
        icon="mdi:power-cycle",
        press_fn=lambda client: client.reboot_system(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VNish buttons from a config entry."""
    coordinator: VnishDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities(
        VnishButton(coordinator, description) for description in BUTTON_DESCRIPTIONS
    )


class VnishButton(VnishEntity, ButtonEntity):
    """Representation of a VNish action button."""

    entity_description: VnishButtonEntityDescription

    def __init__(
        self,
        coordinator: VnishDataUpdateCoordinator,
        description: VnishButtonEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Execute the button action."""
        try:
            await self.entity_description.press_fn(self.coordinator.client)
        except VnishError as err:
            _LOGGER.error(
                "Failed to execute %s: %s", self.entity_description.key, err
            )
            raise
        await self.coordinator.async_request_refresh()
