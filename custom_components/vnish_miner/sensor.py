"""Sensor platform for the VNish ASIC Miner integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VnishData, VnishDataUpdateCoordinator
from .entity import VnishEntity

HASHRATE_GH = "GH/s"
EFFICIENCY_J_TH = "J/TH"


@dataclass(frozen=True, kw_only=True)
class VnishSensorEntityDescription(SensorEntityDescription):
    """Describes a VNish sensor entity."""

    value_fn: Callable[[VnishData], float | str | None]


SENSOR_DESCRIPTIONS: tuple[VnishSensorEntityDescription, ...] = (
    VnishSensorEntityDescription(
        key="hashrate_instant",
        translation_key="hashrate_instant",
        native_unit_of_measurement=HASHRATE_GH,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
        value_fn=lambda data: data.hashrate_instant,
    ),
    VnishSensorEntityDescription(
        key="hashrate_average",
        translation_key="hashrate_average",
        native_unit_of_measurement=HASHRATE_GH,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer-medium",
        value_fn=lambda data: data.hashrate_average,
    ),
    VnishSensorEntityDescription(
        key="hashrate_nominal",
        translation_key="hashrate_nominal",
        native_unit_of_measurement=HASHRATE_GH,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer-slow",
        value_fn=lambda data: data.hashrate_nominal,
    ),
    VnishSensorEntityDescription(
        key="power_consumption",
        translation_key="power_consumption",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.power_consumption,
    ),
    VnishSensorEntityDescription(
        key="chip_temp_max",
        translation_key="chip_temp_max",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.chip_temp_max,
    ),
    VnishSensorEntityDescription(
        key="pcb_temp_max",
        translation_key="pcb_temp_max",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.pcb_temp_max,
    ),
    VnishSensorEntityDescription(
        key="fan_speed_max",
        translation_key="fan_speed_max",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fan",
        value_fn=lambda data: data.fan_speed_max,
    ),
    VnishSensorEntityDescription(
        key="efficiency",
        translation_key="efficiency",
        native_unit_of_measurement=EFFICIENCY_J_TH,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt-outline",
        value_fn=lambda data: data.efficiency,
    ),
    VnishSensorEntityDescription(
        key="miner_status",
        translation_key="miner_status",
        icon="mdi:pickaxe",
        value_fn=lambda data: data.miner_status,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VNish sensors from a config entry."""
    coordinator: VnishDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities(
        VnishSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    )


class VnishSensor(VnishEntity, SensorEntity):
    """Representation of a VNish miner sensor."""

    entity_description: VnishSensorEntityDescription

    def __init__(
        self,
        coordinator: VnishDataUpdateCoordinator,
        description: VnishSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self._data)
