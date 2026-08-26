"""DataUpdateCoordinator for the VNish ASIC Miner integration."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, MANUFACTURER
from .vnish_client import VnishAuthError, VnishClient, VnishConnectionError, VnishError

_LOGGER = logging.getLogger(__name__)


def _first(source: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first present value among several possible key names."""
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return default


def _dig(source: dict[str, Any], *path: str, default: Any = None) -> Any:
    """Safely walk a nested dict following ``path``."""
    current: Any = source
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current if current is not None else default


@dataclass
class VnishData:
    """Normalized snapshot of the miner state."""

    mac: str | None = None
    hostname: str | None = None
    fw_version: str | None = None
    model: str | None = None

    miner_status: str | None = None
    throttled: bool = False

    hashrate_instant: float | None = None
    hashrate_average: float | None = None
    hashrate_nominal: float | None = None
    hashrate_unit: str = "GH/s"

    pcb_temp_min: float | None = None
    pcb_temp_max: float | None = None
    chip_temp_min: float | None = None
    chip_temp_max: float | None = None
    fan_speed_max: float | None = None

    power_consumption: float | None = None
    efficiency: float | None = None

    active_preset: str | None = None
    presets: list[str] = field(default_factory=list)

    restart_required: bool = False
    reboot_required: bool = False

    raw_summary: dict[str, Any] = field(default_factory=dict)
    raw_info: dict[str, Any] = field(default_factory=dict)
    raw_status: dict[str, Any] = field(default_factory=dict)
    raw_settings: dict[str, Any] = field(default_factory=dict)
    raw_presets: dict[str, Any] = field(default_factory=dict)


def _to_th(value: float | None, unit: str) -> float | None:
    """Convert a hashrate value to TH/s given its unit."""
    if value is None:
        return None
    unit = (unit or "").upper()
    if unit.startswith("GH"):
        return value / 1000
    if unit.startswith("MH"):
        return value / 1_000_000
    if unit.startswith("PH"):
        return value * 1000
    return value


def _parse_summary(raw: dict[str, Any], data: VnishData) -> None:
    hashrate = _first(raw, "hashrate", default={}) or {}
    unit = _first(hashrate, "hr_unit", "unit", default="GH/s")
    data.hashrate_unit = unit
    data.hashrate_instant = _first(
        hashrate, "hr_realtime", "instant", "realtime", "hr_instant"
    )
    data.hashrate_average = _first(hashrate, "hr_average", "average", "avg")
    data.hashrate_nominal = _first(hashrate, "hr_nominal", "nominal")

    # Some firmware versions expose hashrate fields flat on the summary root.
    if data.hashrate_instant is None:
        data.hashrate_instant = _first(raw, "hashrate_instant", "hr_realtime")
    if data.hashrate_average is None:
        data.hashrate_average = _first(raw, "hashrate_average", "hr_average")
    if data.hashrate_nominal is None:
        data.hashrate_nominal = _first(raw, "hashrate_nominal", "hr_nominal")

    pcb_temp = _first(raw, "pcb_temp", "pcb_sensors", default={}) or {}
    data.pcb_temp_min = _first(pcb_temp, "min")
    data.pcb_temp_max = _first(pcb_temp, "max")

    chip_temp = _first(raw, "chip_temp", "chip_sensors", default={}) or {}
    data.chip_temp_min = _first(chip_temp, "min")
    data.chip_temp_max = _first(chip_temp, "max")

    fans = _first(raw, "fans", "fan_num", default=[])
    fan_speeds: list[float] = []
    if isinstance(fans, list):
        for fan in fans:
            if isinstance(fan, dict):
                speed = _first(fan, "speed_percent", "speed", "rpm_percent")
                if speed is not None:
                    fan_speeds.append(speed)
    if fan_speeds:
        data.fan_speed_max = max(fan_speeds)
    else:
        data.fan_speed_max = _first(raw, "fan_speed", "fan_speed_max")

    data.power_consumption = _first(raw, "power_consumption", "power")

    if data.power_consumption and data.hashrate_average:
        th_average = _to_th(data.hashrate_average, data.hashrate_unit)
        if th_average:
            data.efficiency = round(data.power_consumption / th_average, 2)

    status = _first(raw, "miner_status", default={})
    if isinstance(status, dict):
        data.miner_status = _first(status, "miner_state", "state", "status")
    else:
        data.miner_status = status

    data.throttled = bool(_first(raw, "throttled", "overheat", default=False))
    data.raw_summary = raw


def _parse_info(raw: dict[str, Any], data: VnishData) -> None:
    data.fw_version = _first(raw, "fw_version", "version")
    data.model = _first(raw, "miner_model", "model", "asic_model")
    data.mac = _first(raw, "mac", "mac_address")
    data.hostname = _first(raw, "hostname", "host_name")
    data.raw_info = raw


def _parse_status(raw: dict[str, Any], data: VnishData) -> None:
    data.restart_required = bool(_first(raw, "restart_required", default=False))
    data.reboot_required = bool(_first(raw, "reboot_required", default=False))
    data.raw_status = raw


def _parse_settings(raw: dict[str, Any], data: VnishData) -> None:
    data.active_preset = _dig(raw, "miner", "overclock", "preset")
    data.raw_settings = raw


def _parse_presets(raw: dict[str, Any], data: VnishData) -> None:
    presets = raw
    if isinstance(raw, dict):
        presets = _first(raw, "presets", "profiles", default=raw.get("data", []))
    names: list[str] = []
    if isinstance(presets, list):
        for preset in presets:
            if isinstance(preset, dict):
                name = _first(preset, "name", "preset", "id")
                if name is not None:
                    names.append(str(name))
            else:
                names.append(str(preset))
    data.presets = names
    data.raw_presets = raw if isinstance(raw, dict) else {}


class VnishDataUpdateCoordinator(DataUpdateCoordinator[VnishData]):
    """Coordinator polling the VNish miner REST API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: VnishClient,
        entry: ConfigEntry,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.entry = entry
        self._device_info: DeviceInfo | None = None

    async def _async_update_data(self) -> VnishData:
        data = VnishData()
        try:
            summary, info, status, settings, presets = await asyncio.gather(
                self.client.get_summary(),
                self.client.get_info(),
                self.client.get_status(),
                self.client.get_settings(),
                self.client.get_presets(),
            )
        except VnishAuthError as err:
            raise UpdateFailed(f"Authentication error: {err}") from err
        except VnishConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except VnishError as err:
            raise UpdateFailed(f"Error communicating with miner: {err}") from err

        _parse_summary(summary, data)
        _parse_info(info, data)
        _parse_status(status, data)
        _parse_settings(settings, data)
        _parse_presets(presets, data)

        self._build_device_info(data)
        return data

    def _build_device_info(self, data: VnishData) -> None:
        identifier = data.mac or self.entry.entry_id
        self._device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            connections={("mac", data.mac)} if data.mac else set(),
            name=data.hostname or f"VNish Miner ({self.entry.data.get('host')})",
            manufacturer=MANUFACTURER,
            model=data.model,
            sw_version=data.fw_version,
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return the Home Assistant device info for this miner."""
        if self._device_info is None:
            self._build_device_info(self.data or VnishData())
        return self._device_info
