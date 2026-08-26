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

from .const import CONF_HOST, CONF_NAME, DOMAIN, MANUFACTURER
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
    presets_map: dict[str, str] = field(default_factory=dict)

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
    """Parse ``/api/v1/summary``.

    VNish 1.3.x nests every metric under a root ``"miner"`` object; older
    firmware exposes them flat (optionally under a ``"hashrate"`` sub-object).
    Fields are looked up on ``miner`` first, falling back to the legacy
    flat/nested locations on ``raw`` so both payload shapes are supported.
    """
    miner = raw.get("miner") if isinstance(raw.get("miner"), dict) else raw
    legacy_hashrate = _first(raw, "hashrate", default={}) or {}
    if not isinstance(legacy_hashrate, dict):
        legacy_hashrate = {}

    data.hashrate_unit = _first(
        miner,
        "hr_unit",
        "unit",
        default=_first(legacy_hashrate, "hr_unit", "unit", default="GH/s"),
    )
    data.hashrate_instant = _first(
        miner,
        "hr_realtime",
        "instant_hashrate",
        "hr_instant",
        default=_first(
            legacy_hashrate,
            "hr_realtime",
            "instant",
            "realtime",
            "hr_instant",
            default=_first(raw, "hashrate_instant", "hr_realtime"),
        ),
    )
    data.hashrate_average = _first(
        miner,
        "hr_average",
        "average_hashrate",
        "hr_avg",
        default=_first(
            legacy_hashrate,
            "hr_average",
            "average",
            "avg",
            default=_first(raw, "hashrate_average", "hr_average"),
        ),
    )
    data.hashrate_nominal = _first(
        miner,
        "hr_nominal",
        default=_first(
            legacy_hashrate,
            "hr_nominal",
            "nominal",
            default=_first(raw, "hashrate_nominal", "hr_nominal"),
        ),
    )

    pcb_temp = _first(miner, "pcb_temp", default=None)
    if not isinstance(pcb_temp, dict):
        pcb_temp = _first(raw, "pcb_temp", "pcb_sensors", default={}) or {}
    data.pcb_temp_min = _first(pcb_temp, "min")
    data.pcb_temp_max = _first(pcb_temp, "max")

    chip_temp = _first(miner, "chip_temp", default=None)
    if not isinstance(chip_temp, dict):
        chip_temp = _first(raw, "chip_temp", "chip_sensors", default={}) or {}
    data.chip_temp_min = _first(chip_temp, "min")
    data.chip_temp_max = _first(chip_temp, "max")

    cooling = _first(miner, "cooling", default={})
    if not isinstance(cooling, dict):
        cooling = {}
    fan_speed_max = _first(cooling, "fan_duty")
    if fan_speed_max is None:
        fan_speeds = [
            speed
            for fan in _first(cooling, "fans", default=[]) or []
            if isinstance(fan, dict)
            for speed in [_first(fan, "rpm_percent", "speed_percent", "speed")]
            if speed is not None
        ]
        if fan_speeds:
            fan_speed_max = max(fan_speeds)
    if fan_speed_max is None:
        legacy_fans = _first(raw, "fans", "fan_num", default=[])
        fan_speeds = [
            speed
            for fan in legacy_fans
            if isinstance(fan, dict)
            for speed in [_first(fan, "speed_percent", "speed", "rpm_percent")]
            if speed is not None
        ] if isinstance(legacy_fans, list) else []
        if fan_speeds:
            fan_speed_max = max(fan_speeds)
        else:
            fan_speed_max = _first(
                miner,
                "fan_speed_max",
                default=_first(raw, "fan_speed", "fan_speed_max"),
            )
    data.fan_speed_max = fan_speed_max

    data.power_consumption = _first(
        miner,
        "power_consumption",
        "power_usage",
        "power",
        default=_first(raw, "power_consumption", "power"),
    )

    efficiency = _first(miner, "power_efficiency")
    if efficiency is None and data.power_consumption and data.hashrate_average:
        th_average = _to_th(data.hashrate_average, data.hashrate_unit)
        if th_average:
            efficiency = round(data.power_consumption / th_average, 2)
    data.efficiency = efficiency

    status = _first(
        miner, "miner_status", default=_first(raw, "miner_status", "state", default={})
    )
    if isinstance(status, dict):
        data.miner_status = _first(
            status, "miner_state", "state", "status", default=_first(raw, "state")
        )
    else:
        data.miner_status = status

    status_block = status if isinstance(status, dict) else {}
    throttled_pct = _first(status_block, "throttled", default=100)
    is_throttled = isinstance(throttled_pct, (int, float)) and throttled_pct < 100
    if not is_throttled:
        is_throttled = bool(
            _first(miner, "throttled", default=_first(raw, "throttled", "overheat", default=False))
        )
    data.throttled = bool(is_throttled)

    data.raw_summary = raw


def _first_text(*values: Any) -> str | None:
    """Return the first non-empty scalar string representation."""
    for value in values:
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                return text
    return None


def _extract_default_name(
    info: dict[str, Any], default_host: str | None = None
) -> str | None:
    """Derive a sensible default device/miner name from an ``/api/v1/info`` payload.

    Checks, in order, the nested VNish 1.3.x layout, then flatter/legacy layouts,
    before falling back to ``default_host`` (e.g. the configured host/IP).
    """
    return _first_text(
        _dig(info, "system", "miner_name"),
        _dig(info, "system", "network_status", "hostname"),
        _dig(info, "system", "hostname"),
        _dig(info, "network_status", "hostname"),
        info.get("hostname"),
        info.get("host_name"),
        info.get("miner_name"),
        _dig(info, "miner", "miner_name"),
        _dig(info, "miner", "hostname"),
        _dig(info, "miner", "name"),
        info.get("miner"),
        default_host,
    )


def _extract_mac(info: dict[str, Any]) -> str | None:
    """Extract a MAC address from nested and legacy VNish info payloads."""
    return _first_text(
        _dig(info, "system", "network_status", "mac"),
        _dig(info, "system", "network_status", "mac_address"),
        _dig(info, "network_status", "mac"),
        _dig(info, "network_status", "mac_address"),
        info.get("mac"),
        info.get("mac_address"),
    )


def _parse_info(raw: dict[str, Any], data: VnishData) -> None:
    data.fw_version = _first(raw, "fw_version", "version")
    data.model = _first(raw, "miner_model", "model", "asic_model")
    data.mac = _extract_mac(raw)
    data.hostname = _extract_default_name(raw)
    data.raw_info = raw


def _parse_status(raw: dict[str, Any], data: VnishData) -> None:
    data.restart_required = bool(_first(raw, "restart_required", default=False))
    data.reboot_required = bool(_first(raw, "reboot_required", default=False))
    data.raw_status = raw


def _parse_settings(raw: dict[str, Any], data: VnishData) -> None:
    raw_preset = _dig(raw, "miner", "overclock", "preset")
    data.active_preset = _pretty_for_raw(data.presets_map, raw_preset)
    data.raw_settings = raw


def _pretty_for_raw(presets_map: dict[str, str], raw_name: Any) -> str | None:
    """Return the pretty label matching a raw preset name."""
    if raw_name is None:
        return None
    raw_name = str(raw_name)
    for pretty, raw in presets_map.items():
        if raw == raw_name:
            return pretty
    return raw_name


def _parse_presets(raw: dict[str, Any], data: VnishData) -> None:
    presets = raw
    if isinstance(raw, dict):
        presets = _first(raw, "presets", "profiles", default=raw.get("data", []))
    pretty_names: list[str] = []
    presets_map: dict[str, str] = {}
    raw_names: set[str] = set()
    if isinstance(presets, list):
        for preset in presets:
            if isinstance(preset, dict):
                name = _first(preset, "name", "preset", "id")
                if name is None:
                    continue
                raw_name = str(name)
                pretty_value = _first(preset, "pretty")
                pretty = str(pretty_value).strip() if pretty_value is not None else ""
                pretty = pretty or raw_name
            else:
                raw_name = str(preset)
                pretty = raw_name

            # A select option must identify exactly one raw preset. Ignore exact
            # duplicate presets and disambiguate duplicate/colliding labels.
            if raw_name in raw_names:
                continue
            raw_names.add(raw_name)
            if pretty in presets_map:
                base = f"{pretty} ({raw_name})"
                pretty = base
                suffix = 2
                while pretty in presets_map:
                    pretty = f"{base} [{suffix}]"
                    suffix += 1
            pretty_names.append(pretty)
            presets_map[pretty] = raw_name
    data.presets = pretty_names
    data.presets_map = presets_map
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
        _parse_presets(presets, data)
        _parse_settings(settings, data)

        self._build_device_info(data)
        return data

    def _build_device_info(self, data: VnishData) -> None:
        identifier = data.mac or self.entry.entry_id
        name = (
            self.entry.data.get(CONF_NAME)
            or data.hostname
            or f"VNish Miner ({self.entry.data.get(CONF_HOST)})"
        )
        self._device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            connections={("mac", data.mac)} if data.mac else set(),
            name=name,
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
