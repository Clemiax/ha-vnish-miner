"""Unit tests for the VNish client and data parsing.

These tests mock the aiohttp session so they run without a real miner or
a full Home Assistant environment.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components" / "vnish_miner")
)

from vnish_client import (  # noqa: E402
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


class _FakeResponse:
    """Minimal async context manager mimicking an aiohttp response."""

    def __init__(self, status: int, payload: dict, content_length: int = 1) -> None:
        self.status = status
        self._payload = payload
        self.content_length = content_length

    async def __aenter__(self) -> "_FakeResponse":
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
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method, url, json=None, headers=None, timeout=None):
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        path = f"/{path}"
        self.calls.append((method, path, json))
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


def test_set_preset_sends_expected_payload() -> None:
    session = _FakeSession({"/api/v1/settings": (200, {"ok": True})})
    client = VnishClient(host="192.168.1.50", api_key="test-key", session=session)
    asyncio.run(client.set_preset("2600"))
    method, path, payload = session.calls[0]
    assert method == "POST"
    assert path == "/api/v1/settings"
    assert payload == {"miner": {"overclock": {"preset": "2600"}}}


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
