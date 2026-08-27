"""Unit tests for the VNish client and data parsing.

These tests mock the aiohttp session so they run without a real miner or
a full Home Assistant environment.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Self
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components")
)

from homeassistant.data_entry_flow import AbortFlow, FlowResultType
from vnish_miner import config_flow
from vnish_miner.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
)
from vnish_miner.coordinator import (
    VnishData,
    VnishDataUpdateCoordinator,
    _extract_default_name,
    _extract_mac,
    _parse_info,
    _parse_presets,
    _parse_settings,
    _parse_summary,
    _to_th,
)
from vnish_miner.vnish_client import (
    VnishAuthError,
    VnishClient,
    VnishConnectionError,
    VnishError,
)

FIXTURE_SUMMARY = {
    "hashrate": {
        "hr_realtime": 96000,
        "hr_average": 95500,
        "hr_nominal": 100000,
        "hr_unit": "GH/s",
    },
    "chip_temp": {"min": 55, "max": 78},
    "pcb_temp": {"min": 40, "max": 52},
    "fans": [
        {"speed_percent": 60},
        {"speed_percent": 65},
    ],
    "power_consumption": 3200,
    "miner_status": {"miner_state": "mining"},
    "throttled": False,
}

FIXTURE_INFO = {
    "fw_version": "1.3.5",
    "miner_model": "Antminer S19k Pro",
    "mac": "AA:BB:CC:DD:EE:FF",
    "hostname": "antminer-s19kpro",
}

FIXTURE_STATUS = {
    "restart_required": False,
    "reboot_required": False,
}

FIXTURE_SETTINGS = {
    "miner": {"overclock": {"preset": "2470"}},
}

FIXTURE_PRESETS = {
    "presets": [
        {"name": "2050"},
        {"name": "2180"},
        {"name": "2310"},
        {"name": "2470"},
        {"name": "2600"},
        {"name": "2990"},
        {"name": "3120"},
    ]
}

FIXTURE_PRESETS_PRETTY = {
    "presets": [
        {"name": "disabled", "pretty": "Disabled"},
        {"name": "2050", "pretty": "2050 watt ~ 80 TH"},
        {"name": "2180", "pretty": "2180 watt ~ 85 TH"},
        {"name": "2310", "pretty": "2310 watt ~ 90 TH"},
        {"name": "2470", "pretty": "2470 watt ~ 95 TH"},
        {"name": "2600", "pretty": "2600 watt ~ 100 TH"},
    ]
}


class _FakeResponse:
    """Minimal async context manager mimicking an aiohttp response."""

    def __init__(self, status: int, payload: dict, content_length: int = 1) -> None:
        self.status = status
        self._payload = payload
        self.content_length = content_length

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def json(self, content_type=None) -> dict:
        return self._payload

    async def text(self) -> str:
        return str(self._payload)


class _FakeSession:
    """Fake aiohttp.ClientSession returning canned responses per path."""

    def __init__(self, responses: dict[str, tuple[int, dict]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict | None, dict | None]] = []

    def request(self, method, url, json=None, headers=None, timeout=None):
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        path = f"/{path}"
        self.calls.append((method, path, json, headers))
        status, payload = self._responses.get(path, (404, {}))
        return _FakeResponse(status, payload)


def _client_for(responses: dict[str, tuple[int, dict]]) -> VnishClient:
    session = _FakeSession(responses)
    return VnishClient(host="192.168.1.50", api_key="test-key", session=session)


def test_get_summary_parses_payload() -> None:
    client = _client_for({"/api/v1/summary": (200, FIXTURE_SUMMARY)})
    result = asyncio.run(client.get_summary())
    assert result["hashrate"]["hr_realtime"] == 96000
    assert result["miner_status"]["miner_state"] == "mining"


# -- Summary parsing (VNish 1.3.x nested "miner" payload) --------------------

FIXTURE_SUMMARY_VNISH_1_3_5 = {
    "miner": {
        "hr_realtime": 96000,
        "hr_average": 95500,
        "hr_nominal": 100000,
        "hr_unit": "GH/s",
        "power_consumption": 3200,
        "pcb_temp": {"min": 40, "max": 52},
        "chip_temp": {"min": 55, "max": 78},
        "cooling": {
            "fan_duty": 65,
            "fans": [
                {"rpm_percent": 60},
                {"rpm_percent": 70},
            ],
        },
        "miner_status": {"miner_state": "mining", "throttled": 100},
    }
}


def test_parse_summary_supports_nested_vnish_1_3_5_payload() -> None:
    data = VnishData()
    _parse_summary(FIXTURE_SUMMARY_VNISH_1_3_5, data)

    assert data.hashrate_instant == 96.0
    assert data.hashrate_average == 95.5
    assert data.hashrate_nominal == 100.0
    assert data.hashrate_unit == "TH/s"
    assert data.power_consumption == 3200
    assert data.chip_temp_min == 55
    assert data.chip_temp_max == 78
    assert data.pcb_temp_min == 40
    assert data.pcb_temp_max == 52
    assert data.fan_speed_max == 65
    assert data.miner_status == "mining"
    assert data.throttled is False
    assert data.efficiency == round(3200 / 95.5, 2)

    for field in (
        data.hashrate_instant,
        data.hashrate_average,
        data.hashrate_nominal,
        data.power_consumption,
        data.chip_temp_min,
        data.chip_temp_max,
        data.pcb_temp_min,
        data.pcb_temp_max,
        data.fan_speed_max,
        data.miner_status,
        data.efficiency,
    ):
        assert field is not None


def test_parse_summary_computes_fan_speed_from_fans_list_when_no_fan_duty() -> None:
    data = VnishData()
    payload = {
        "miner": {
            "hr_average": 90000,
            "power_consumption": 3100,
            "cooling": {"fans": [{"rpm_percent": 55}, {"rpm_percent": 80}]},
        }
    }
    _parse_summary(payload, data)
    assert data.fan_speed_max == 80


def test_parse_summary_detects_throttled_state() -> None:
    data = VnishData()
    payload = {
        "miner": {
            "hr_average": 90000,
            "power_consumption": 3300,
            "miner_status": {"miner_state": "mining", "throttled": 80},
        }
    }
    _parse_summary(payload, data)
    assert data.throttled is True


@pytest.mark.parametrize(
    "value, expected", [(False, False), (True, True), (100, False), (80, True)]
)
def test_parse_summary_supports_boolean_and_percentage_throttled_values(
    value, expected
) -> None:
    data = VnishData()
    _parse_summary(
        {"miner": {"miner_status": {"miner_state": "mining", "throttled": value}}},
        data,
    )
    assert data.throttled is expected


def test_parse_summary_supports_root_fan_duty_and_explicit_efficiency() -> None:
    data = VnishData()
    _parse_summary(
        {
            "miner": {
                "fan_duty": 72,
                "power_efficiency": 31.5,
                "cooling": {"fans": None},
            }
        },
        data,
    )
    assert data.fan_speed_max == 72
    assert data.efficiency == 31.5


def test_parse_summary_ignores_malformed_cooling_fans() -> None:
    data = VnishData()
    _parse_summary({"miner": {"cooling": {"fans": 2}, "fan_speed_max": 64}}, data)
    assert data.fan_speed_max == 64


def test_parse_summary_still_supports_legacy_flat_payload() -> None:
    data = VnishData()
    _parse_summary(FIXTURE_SUMMARY, data)

    assert data.hashrate_instant == 96.0
    assert data.hashrate_average == 95.5
    assert data.hashrate_nominal == 100.0
    assert data.chip_temp_min == 55
    assert data.chip_temp_max == 78
    assert data.pcb_temp_min == 40
    assert data.pcb_temp_max == 52
    assert data.fan_speed_max == 65
    assert data.power_consumption == 3200
    assert data.miner_status == "mining"
    assert data.throttled is False


def test_parse_summary_converts_gh_hashrate_to_th_and_rounds_efficiency() -> None:
    data = VnishData()
    _parse_summary(
        {
            "miner": {
                "hr_realtime": 68471.19,
                "hr_average": 68471.19,
                "hr_nominal": 68471.19,
                "power_consumption": 2074.0,
            }
        },
        data,
    )

    assert data.hashrate_instant == 68.47
    assert data.hashrate_average == 68.47
    assert data.hashrate_nominal == 68.47
    assert data.hashrate_unit == "TH/s"
    assert data.efficiency == 30.29


def test_parse_summary_keeps_already_th_hashrate_fields_and_rounds() -> None:
    data = VnishData()
    _parse_summary(
        {"miner": {"instant_hashrate": 68.4712, "average_hashrate": 68.4712}},
        data,
    )

    assert data.hashrate_instant == 68.47
    assert data.hashrate_average == 68.47


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (1000, "GH/s", 1.0),
        (1_250_123.0, "MH/s", 1.25),
        (1_234.567, "TH/s", 1234.57),
        (1.23456, "PH/s", 1234.56),
    ],
)
def test_to_th_uses_explicit_unit_and_rounds(
    value: float, unit: str, expected: float
) -> None:
    assert _to_th(value, unit) == expected


def test_parse_summary_explicit_unit_overrides_field_default() -> None:
    data = VnishData()
    _parse_summary(
        {
            "miner": {
                "instant_hashrate": 1000,
                "average_hashrate": 1_250_123,
                "hr_unit": "GH/s",
            }
        },
        data,
    )

    assert data.hashrate_instant == 1.0
    assert data.hashrate_average == 1250.12


def test_parse_summary_uses_field_units_without_explicit_unit() -> None:
    data = VnishData()
    _parse_summary(
        {
            "miner": {
                "hr_realtime": 1000,
                "hr_average": 68_471.19,
                "instant_hashrate": 1_234.567,
            }
        },
        data,
    )

    # hr_* fields take precedence and are implicitly expressed in GH/s.
    assert data.hashrate_instant == 1.0
    assert data.hashrate_average == 68.47

    th_data = VnishData()
    _parse_summary(
        {"miner": {"instant_hashrate": 1_234.567, "average_hashrate": 1_234.567}},
        th_data,
    )
    assert th_data.hashrate_instant == 1234.57
    assert th_data.hashrate_average == 1234.57


def test_get_info_returns_mac_for_unique_id() -> None:
    client = _client_for({"/api/v1/info": (200, FIXTURE_INFO)})
    result = asyncio.run(client.get_info())
    assert result["mac"] == "AA:BB:CC:DD:EE:FF"
    assert result["fw_version"] == "1.3.5"


def test_get_settings_returns_active_preset() -> None:
    client = _client_for({"/api/v1/settings": (200, FIXTURE_SETTINGS)})
    result = asyncio.run(client.get_settings())
    assert result["miner"]["overclock"]["preset"] == "2470"


def test_get_presets_returns_profile_list() -> None:
    client = _client_for({"/api/v1/autotune/presets": (200, FIXTURE_PRESETS)})
    result = asyncio.run(client.get_presets())
    names = [p["name"] for p in result["presets"]]
    assert names == ["2050", "2180", "2310", "2470", "2600", "2990", "3120"]


def test_parse_presets_builds_pretty_names_and_map() -> None:
    data = VnishData()
    _parse_presets(FIXTURE_PRESETS_PRETTY, data)
    assert data.presets == [
        "Disabled",
        "2050 watt ~ 80 TH",
        "2180 watt ~ 85 TH",
        "2310 watt ~ 90 TH",
        "2470 watt ~ 95 TH",
        "2600 watt ~ 100 TH",
    ]
    assert data.presets_map["2050 watt ~ 80 TH"] == "2050"
    assert data.presets_map["Disabled"] == "disabled"


def test_parse_presets_falls_back_to_name_when_pretty_missing() -> None:
    data = VnishData()
    _parse_presets(FIXTURE_PRESETS, data)
    assert data.presets == ["2050", "2180", "2310", "2470", "2600", "2990", "3120"]
    assert data.presets_map["2470"] == "2470"


def test_parse_presets_falls_back_for_empty_pretty_and_remains_bidirectional() -> None:
    data = VnishData()
    _parse_presets(
        {
            "presets": [
                {"name": "eco", "pretty": ""},
                {"name": "silent", "pretty": None},
            ]
        },
        data,
    )

    assert data.presets == ["eco", "silent"]
    for option in data.presets:
        raw_name = data.presets_map[option]
        _parse_settings({"miner": {"overclock": {"preset": raw_name}}}, data)
        assert data.active_preset == option


def test_parse_presets_disambiguates_duplicate_pretty_labels() -> None:
    data = VnishData()
    _parse_presets(
        {
            "presets": [
                {"name": "2050", "pretty": "Eco"},
                {"name": "2180", "pretty": "Eco"},
            ]
        },
        data,
    )

    assert data.presets == ["Eco", "Eco (2180)"]
    assert data.presets_map == {"Eco": "2050", "Eco (2180)": "2180"}
    for option in data.presets:
        _parse_settings(
            {"miner": {"overclock": {"preset": data.presets_map[option]}}}, data
        )
        assert data.active_preset == option


def test_parse_settings_resolves_active_preset_to_pretty_label() -> None:
    data = VnishData()
    _parse_presets(FIXTURE_PRESETS_PRETTY, data)
    _parse_settings({"miner": {"overclock": {"preset": "2050"}}}, data)
    assert data.active_preset == "2050 watt ~ 80 TH"


def test_parse_settings_falls_back_to_raw_when_preset_unknown() -> None:
    data = VnishData()
    _parse_presets(FIXTURE_PRESETS_PRETTY, data)
    _parse_settings({"miner": {"overclock": {"preset": "9999"}}}, data)
    assert data.active_preset == "9999"


def test_set_preset_sends_expected_payload() -> None:
    session = _FakeSession({"/api/v1/settings": (200, {"ok": True})})
    client = VnishClient(host="192.168.1.50", api_key="test-key", session=session)
    asyncio.run(client.set_preset("2600"))
    method, path, payload, headers = session.calls[0]
    assert method == "POST"
    assert path == "/api/v1/settings"
    assert payload == {"miner": {"overclock": {"preset": "2600"}}}
    assert headers == {"X-API-Key": "test-key"}


def test_pause_and_resume_mining_call_expected_endpoints() -> None:
    session = _FakeSession(
        {
            "/api/v1/mining/pause": (200, {}),
            "/api/v1/mining/resume": (200, {}),
        }
    )
    client = VnishClient(host="192.168.1.50", api_key="test-key", session=session)
    asyncio.run(client.pause_mining())
    asyncio.run(client.resume_mining())
    paths = [call[1] for call in session.calls]
    assert "/api/v1/mining/pause" in paths
    assert "/api/v1/mining/resume" in paths


def test_restart_and_reboot_call_expected_endpoints() -> None:
    session = _FakeSession(
        {
            "/api/v1/mining/restart": (200, {}),
            "/api/v1/system/reboot": (200, {}),
        }
    )
    client = VnishClient(host="192.168.1.50", api_key="test-key", session=session)
    asyncio.run(client.restart_mining())
    asyncio.run(client.reboot_system())
    paths = [call[1] for call in session.calls]
    assert "/api/v1/mining/restart" in paths
    assert "/api/v1/system/reboot" in paths


def test_auth_error_raised_on_401() -> None:
    client = _client_for({"/api/v1/summary": (401, {"error": "unauthorized"})})
    with pytest.raises(VnishAuthError):
        asyncio.run(client.get_summary())


def test_auth_error_raised_on_403() -> None:
    client = _client_for({"/api/v1/summary": (403, {"error": "forbidden"})})
    with pytest.raises(VnishAuthError):
        asyncio.run(client.get_summary())


def test_generic_error_raised_on_5xx() -> None:
    client = _client_for({"/api/v1/summary": (500, {"error": "boom"})})
    with pytest.raises(VnishError):
        asyncio.run(client.get_summary())


def test_connection_error_wraps_timeout() -> None:
    class _TimeoutSession:
        def request(self, *args, **kwargs):
            raise asyncio.TimeoutError()

    client = VnishClient(host="192.168.1.50", api_key="test-key", session=_TimeoutSession())
    with pytest.raises(VnishConnectionError):
        asyncio.run(client.get_summary())


# -- Default name extraction (VNish 1.3.x nested info payload) ------------

NESTED_INFO = {
    "miner": "Antminer S19k Pro",
    "system": {
        "miner_name": "A1-S19kPro",
        "network_status": {
            "hostname": "A1-S19kPro",
            "mac": "02:39:C9:79:A8:3C",
        },
    },
}


def test_extract_default_name_prefers_system_miner_name() -> None:
    assert _extract_default_name(NESTED_INFO, "192.168.1.50") == "A1-S19kPro"


def test_extract_default_name_falls_back_to_network_status_hostname() -> None:
    info = {"system": {"network_status": {"hostname": "A1-S19kPro"}}}
    assert _extract_default_name(info, "192.168.1.50") == "A1-S19kPro"


def test_extract_default_name_falls_back_to_flat_hostname() -> None:
    assert (
        _extract_default_name({"hostname": "antminer-1"}, "192.168.1.50")
        == "antminer-1"
    )


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"system": {"hostname": "system-host"}}, "system-host"),
        ({"network_status": {"hostname": "network-host"}}, "network-host"),
        ({"miner_name": "root-miner"}, "root-miner"),
        ({"host_name": "legacy-host"}, "legacy-host"),
        ({"miner": {"miner_name": "nested-miner"}}, "nested-miner"),
        ({"miner": {"hostname": "miner-host"}}, "miner-host"),
        ({"miner": {"name": "miner-name"}}, "miner-name"),
    ],
)
def test_extract_default_name_supports_vnish_variants(info, expected) -> None:
    assert _extract_default_name(info, "192.168.1.50") == expected


def test_extract_default_name_ignores_container_values() -> None:
    assert _extract_default_name({"miner": {"model": "S19"}}, "192.168.1.50") == (
        "192.168.1.50"
    )


@pytest.mark.parametrize(
    "info",
    [
        {"system": {"network_status": {"mac_address": "AA:BB:CC:DD:EE:FF"}}},
        {"network_status": {"mac": "AA:BB:CC:DD:EE:FF"}},
        {"network_status": {"mac_address": "AA:BB:CC:DD:EE:FF"}},
        {"mac": "AA:BB:CC:DD:EE:FF"},
        {"mac_address": "AA:BB:CC:DD:EE:FF"},
    ],
)
def test_extract_mac_supports_vnish_variants(info) -> None:
    assert _extract_mac(info) == "AA:BB:CC:DD:EE:FF"


def test_extract_default_name_falls_back_to_miner_label() -> None:
    assert (
        _extract_default_name({"miner": "Antminer S19k Pro"}, "192.168.1.50")
        == "Antminer S19k Pro"
    )


def test_extract_default_name_falls_back_to_default_host() -> None:
    assert _extract_default_name({}, "192.168.1.50") == "192.168.1.50"


def test_parse_info_populates_nested_hostname_and_mac() -> None:
    data = VnishData()
    _parse_info(NESTED_INFO, data)
    assert data.hostname == "A1-S19kPro"
    assert data.mac == "02:39:C9:79:A8:3C"


def test_parse_info_still_supports_flat_legacy_payload() -> None:
    data = VnishData()
    _parse_info(FIXTURE_INFO, data)
    assert data.hostname == "antminer-s19kpro"
    assert data.mac == "AA:BB:CC:DD:EE:FF"


def test_device_info_uses_configured_name() -> None:
    coordinator = object.__new__(VnishDataUpdateCoordinator)
    coordinator.entry = _make_config_entry(
        {CONF_HOST: "192.168.1.50", CONF_NAME: "My Miner"}
    )

    coordinator._build_device_info(
        VnishData(mac="AA:BB:CC:DD:EE:FF", hostname="firmware-host")
    )

    assert coordinator.device_info["name"] == "My Miner"


# -- Config flow ------------------------------------------------------------


def _make_hass(existing_entry: object | None = None) -> MagicMock:
    hass = MagicMock()
    hass.config_entries.flow.async_progress_by_handler.return_value = []
    hass.config_entries.async_entry_for_domain_unique_id.return_value = existing_entry
    return hass


def _make_config_entry(data: dict, options: dict | None = None, title: str = "Old Name"):
    entry = MagicMock()
    entry.data = data
    entry.options = options or {}
    entry.title = title
    entry.entry_id = "entry123"
    return entry


def test_config_flow_user_step_advances_to_device_step(monkeypatch) -> None:
    monkeypatch.setattr(config_flow, "_validate_input", AsyncMock(return_value=NESTED_INFO))
    flow = config_flow.VnishConfigFlow()
    flow.hass = _make_hass()
    flow.handler = "vnish_miner"
    flow.context = {}

    result = asyncio.run(
        flow.async_step_user(
            {CONF_HOST: "192.168.1.50", CONF_API_KEY: "key", CONF_PORT: DEFAULT_PORT}
        )
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "device_config"
    assert flow._default_name == "A1-S19kPro"
    assert flow.unique_id == "02:39:C9:79:A8:3C"

    name_key = next(k for k in result["data_schema"].schema if k == CONF_NAME)
    assert name_key.default() == "A1-S19kPro"


def test_config_flow_user_step_reports_auth_error(monkeypatch) -> None:
    monkeypatch.setattr(
        config_flow, "_validate_input", AsyncMock(side_effect=VnishAuthError("bad key"))
    )
    flow = config_flow.VnishConfigFlow()
    flow.hass = _make_hass()
    flow.handler = "vnish_miner"
    flow.context = {}

    result = asyncio.run(
        flow.async_step_user(
            {CONF_HOST: "192.168.1.50", CONF_API_KEY: "key", CONF_PORT: DEFAULT_PORT}
        )
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


def test_config_flow_aborts_if_unique_id_already_configured(monkeypatch) -> None:
    monkeypatch.setattr(config_flow, "_validate_input", AsyncMock(return_value=NESTED_INFO))
    flow = config_flow.VnishConfigFlow()
    flow.hass = _make_hass(existing_entry=MagicMock())
    flow.handler = "vnish_miner"
    flow.context = {}

    with pytest.raises(AbortFlow):
        asyncio.run(
            flow.async_step_user(
                {CONF_HOST: "192.168.1.50", CONF_API_KEY: "key", CONF_PORT: DEFAULT_PORT}
            )
        )


def test_config_flow_device_config_step_creates_entry_with_combined_data() -> None:
    flow = config_flow.VnishConfigFlow()
    flow.hass = _make_hass()
    flow.handler = "vnish_miner"
    flow.context = {}
    flow._connection_data = {
        CONF_HOST: "192.168.1.50",
        CONF_API_KEY: "key",
        CONF_PORT: DEFAULT_PORT,
    }
    flow._default_name = "A1-S19kPro"

    result = asyncio.run(
        flow.async_step_device_config({CONF_NAME: "My Miner", CONF_SCAN_INTERVAL: 30})
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Miner"
    assert result["data"] == {
        CONF_HOST: "192.168.1.50",
        CONF_API_KEY: "key",
        CONF_PORT: DEFAULT_PORT,
        CONF_NAME: "My Miner",
        CONF_SCAN_INTERVAL: 30,
    }


def test_config_flow_preserves_unique_id_through_both_steps(monkeypatch) -> None:
    monkeypatch.setattr(config_flow, "_validate_input", AsyncMock(return_value=NESTED_INFO))
    flow = config_flow.VnishConfigFlow()
    flow.hass = _make_hass()
    flow.handler = "vnish_miner"
    flow.context = {}

    first_result = asyncio.run(
        flow.async_step_user(
            {CONF_HOST: "192.168.1.50", CONF_API_KEY: "key", CONF_PORT: DEFAULT_PORT}
        )
    )
    assert first_result["step_id"] == "device_config"

    result = asyncio.run(
        flow.async_step_device_config(
            {CONF_NAME: "My Miner", CONF_SCAN_INTERVAL: 30}
        )
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Miner"
    assert flow.unique_id == "02:39:C9:79:A8:3C"


# -- Options flow -------------------------------------------------------------


def test_options_flow_updates_name_and_title(monkeypatch) -> None:
    monkeypatch.setattr(config_flow, "_validate_input", AsyncMock(return_value={}))
    entry = _make_config_entry(
        {
            CONF_HOST: "192.168.1.50",
            CONF_PORT: DEFAULT_PORT,
            CONF_API_KEY: "old-key",
            CONF_NAME: "Old Name",
        },
        title="Old Name",
    )
    flow = config_flow.VnishOptionsFlowHandler(entry)
    flow.hass = MagicMock()

    result = asyncio.run(
        flow.async_step_init(
            {CONF_NAME: "New Name", CONF_API_KEY: "new-key", CONF_SCAN_INTERVAL: 20}
        )
    )

    flow.hass.config_entries.async_update_entry.assert_called_once()
    _, kwargs = flow.hass.config_entries.async_update_entry.call_args
    assert kwargs["title"] == "New Name"
    assert kwargs["data"][CONF_NAME] == "New Name"
    assert kwargs["data"][CONF_API_KEY] == "new-key"
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_SCAN_INTERVAL: 20}


def test_options_flow_keeps_title_untouched_when_name_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(config_flow, "_validate_input", AsyncMock(return_value={}))
    entry = _make_config_entry(
        {
            CONF_HOST: "192.168.1.50",
            CONF_PORT: DEFAULT_PORT,
            CONF_API_KEY: "old-key",
            CONF_NAME: "Same Name",
        },
        title="Same Name",
    )
    flow = config_flow.VnishOptionsFlowHandler(entry)
    flow.hass = MagicMock()

    asyncio.run(
        flow.async_step_init(
            {CONF_NAME: "Same Name", CONF_API_KEY: "new-key", CONF_SCAN_INTERVAL: 20}
        )
    )

    _, kwargs = flow.hass.config_entries.async_update_entry.call_args
    assert "title" not in kwargs


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
