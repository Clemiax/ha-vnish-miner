"""Async REST client for the VNish firmware API."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

DEFAULT_TIMEOUT = 10


class VnishError(Exception):
    """Base exception for the VNish client."""


class VnishConnectionError(VnishError):
    """Raised when the miner cannot be reached."""


class VnishAuthError(VnishError):
    """Raised when the API key is missing or invalid."""


class VnishClient:
    """Small async wrapper around the VNish firmware REST API."""

    def __init__(
        self,
        host: str,
        api_key: str,
        port: int = 80,
        session: aiohttp.ClientSession | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._api_key = api_key
        self._session = session
        self._own_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def base_url(self) -> str:
        """Return the base URL of the miner API."""
        return f"http://{self._host}:{self._port}"

    async def __aenter__(self) -> "VnishClient":
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying session if it was created internally."""
        if self._own_session and self._session is not None:
            await self._session.close()

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self._api_key}

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession()

        url = f"{self.base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                json=json,
                headers=self._headers(),
                timeout=self._timeout,
            ) as response:
                if response.status in (401, 403):
                    raise VnishAuthError(
                        f"Authentication failed for {url} (status {response.status})"
                    )
                if response.status >= 400:
                    text = await response.text()
                    raise VnishError(
                        f"Unexpected status {response.status} from {url}: {text}"
                    )
                if response.content_length == 0:
                    return {}
                try:
                    return await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    return {}
        except asyncio.TimeoutError as err:
            raise VnishConnectionError(f"Timeout connecting to {url}") from err
        except aiohttp.ClientConnectionError as err:
            raise VnishConnectionError(f"Cannot connect to {url}: {err}") from err
        except aiohttp.ClientError as err:
            raise VnishConnectionError(f"Error communicating with {url}: {err}") from err

    # -- GET endpoints ---------------------------------------------------

    async def get_summary(self) -> dict[str, Any]:
        """Return mining metrics (hashrate, temps, fans, power, status)."""
        return await self._request("GET", "/api/v1/summary")

    async def get_info(self) -> dict[str, Any]:
        """Return firmware/model/MAC/hostname information."""
        return await self._request("GET", "/api/v1/info")

    async def get_status(self) -> dict[str, Any]:
        """Return runtime status (restart_required, reboot_required, ...)."""
        return await self._request("GET", "/api/v1/status")

    async def get_settings(self) -> dict[str, Any]:
        """Return the current miner settings (including active preset)."""
        return await self._request("GET", "/api/v1/settings")

    async def get_presets(self) -> dict[str, Any]:
        """Return the list of available autotune/overclock presets."""
        return await self._request("GET", "/api/v1/autotune/presets")

    # -- POST endpoints ---------------------------------------------------

    async def set_preset(self, preset: str) -> dict[str, Any]:
        """Switch the active overclock/autotune preset."""
        payload = {"miner": {"overclock": {"preset": preset}}}
        return await self._request("POST", "/api/v1/settings", json=payload)

    async def pause_mining(self) -> dict[str, Any]:
        """Pause mining."""
        return await self._request("POST", "/api/v1/mining/pause")

    async def resume_mining(self) -> dict[str, Any]:
        """Resume mining."""
        return await self._request("POST", "/api/v1/mining/resume")

    async def restart_mining(self) -> dict[str, Any]:
        """Restart the mining process (software restart)."""
        return await self._request("POST", "/api/v1/mining/restart")

    async def reboot_system(self) -> dict[str, Any]:
        """Reboot the ASIC hardware."""
        return await self._request("POST", "/api/v1/system/reboot")
