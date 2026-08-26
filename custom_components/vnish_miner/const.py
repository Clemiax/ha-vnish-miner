"""Constants for the VNish ASIC Miner integration."""
from __future__ import annotations

DOMAIN = "vnish_miner"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_KEY = "api_key"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_NAME = "name"

DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 15

MANUFACTURER = "Bitmain / VNish"

ATTR_HASHRATE_INSTANT = "hashrate_instant"
ATTR_HASHRATE_AVERAGE = "hashrate_average"
ATTR_HASHRATE_NOMINAL = "hashrate_nominal"
