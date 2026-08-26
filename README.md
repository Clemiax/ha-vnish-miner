# VNish ASIC Miner — Home Assistant Integration

A full-featured Home Assistant integration for ASIC miners running **VNish**
firmware. Monitor performance and control mining directly from Home
Assistant, and build automations around electricity pricing, temperature
safety, and miner health.

Tested on an **Antminer S19k Pro** running **VNish 1.3.5**, and compatible
with the **S19 / S21 / T21 / L7** series — the VNish REST API is shared
across these models.

## Features

- **Full sensor suite** — instant, average, and nominal hashrate (TH/s,
  rounded to 2 decimals), power consumption (W), efficiency (J/TH, rounded to
  2 decimals), max chip and PCB temperature (°C), max fan speed / duty cycle
  (%), and miner status.
- **Dynamic Overclock Preset select entity** — lists every autotune/overclock
  preset available on the miner with clean, readable labels (e.g. `2050 watt
  ~ 80 TH`, `2600 watt ~ 100 TH`), while transparently mapping each label back
  to the raw preset ID the VNish API expects.
- **Pause / Resume Mining switch** — a simple on/off control for mining
  operations.
- **Action buttons** — trigger a software restart of the VNish mining process
  or a full hardware reboot of the ASIC miner.
- **Two-step config flow** — automatically detects the miner's hostname/name
  (e.g. `A1-S19kPro`) from the device, with the option to fully customize the
  name during onboarding or later from the integration's options.

## Installation

### Via HACS (recommended)

1. In HACS, open **Integrations** → **⋮** menu → **Custom repositories**.
2. Add `https://github.com/Clemiax/ha-vnish-miner` as an *Integration*
   repository.
3. Search for **VNish ASIC Miner** in HACS and install it.
4. Restart Home Assistant.

### Manual installation

1. Copy the `custom_components/vnish_miner` folder into the
   `custom_components` directory of your Home Assistant configuration.
2. Restart Home Assistant.

## Configuration

In Home Assistant: **Settings → Devices & services → Add integration →
VNish ASIC Miner**.

| Parameter | Description | Default |
|---|---|---|
| Host | IP address or hostname of the miner | — |
| Port | REST API port of the miner | `80` |
| API Key | VNish API key, sent as the `X-API-Key` header | — |
| Miner Name | Friendly name for the device; auto-detected from the miner, editable during setup or later in Options | auto-detected |
| Scan Interval | Polling frequency, in seconds | `15` |

The API key is generated from the miner's VNish web interface
(**Settings → API access**).

## Exposed Entities

| Entity key | Type | Unit | Description |
|---|---|---|---|
| `hashrate_instant` | sensor | TH/s | Instant (real-time) hashrate |
| `hashrate_average` | sensor | TH/s | Average hashrate |
| `hashrate_nominal` | sensor | TH/s | Nominal (rated) hashrate |
| `power_consumption` | sensor | W | Power consumption |
| `chip_temp_max` | sensor | °C | Maximum chip temperature |
| `pcb_temp_max` | sensor | °C | Maximum PCB temperature |
| `fan_speed_max` | sensor | % | Maximum fan speed / duty cycle |
| `efficiency` | sensor | J/TH | Power efficiency |
| `miner_status` | sensor | — | Current miner status |
| `preset` | select | — | Active overclock/autotune preset |
| `mining` | switch | — | Pause / resume mining |
| `restart_mining` | button | — | Software restart of the VNish mining process |
| `reboot_hardware` | button | — | Hardware reboot of the ASIC miner |

## Automation Examples

### Off-peak / peak time-of-use preset switching

Switch to a higher-power preset during off-peak hours and drop to a lower,
more efficient preset during peak hours to optimize electricity cost.

```yaml
automation:
  - alias: "VNish - Off-peak preset (high power)"
    description: "Switch the miner to the high-performance preset during off-peak hours"
    trigger:
      - platform: time
        at: "22:00:00"
    action:
      - service: select.select_option
        target:
          entity_id: select.a1_s19kpro_overclock_preset
        data:
          option: "2600 watt ~ 100 TH"

  - alias: "VNish - Peak preset (eco)"
    description: "Switch the miner to a lower-power eco preset during peak hours"
    trigger:
      - platform: time
        at: "06:00:00"
    action:
      - service: select.select_option
        target:
          entity_id: select.a1_s19kpro_overclock_preset
        data:
          option: "2050 watt ~ 80 TH"
```

### Overheat safety

Automatically pause mining if the chip temperature exceeds a critical
threshold, and resume once the miner has cooled down.

```yaml
automation:
  - alias: "VNish - Overheat safety (pause)"
    description: "Pause mining if chip temperature is critical"
    trigger:
      - platform: numeric_state
        entity_id: sensor.a1_s19kpro_max_chip_temperature
        above: 90
        for:
          minutes: 1
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.a1_s19kpro_mining
      - service: notify.mobile_app
        data:
          title: "⚠️ ASIC miner overheating"
          message: "Mining paused: chip temperature above 90°C."

  - alias: "VNish - Resume after cooldown"
    description: "Resume mining once temperature is back to normal"
    trigger:
      - platform: numeric_state
        entity_id: sensor.a1_s19kpro_max_chip_temperature
        below: 80
        for:
          minutes: 5
    condition:
      - condition: state
        entity_id: switch.a1_s19kpro_mining
        state: "off"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.a1_s19kpro_mining
```

### Error watchdog auto-restart

Automatically restart the mining process if the miner status remains in an
error state for too long.

```yaml
automation:
  - alias: "VNish - Restart on error"
    description: "Restart the mining process if the miner status stays abnormal"
    trigger:
      - platform: state
        entity_id: sensor.a1_s19kpro_miner_status
        to: "error"
        for:
          minutes: 3
    action:
      - service: button.press
        target:
          entity_id: button.a1_s19kpro_restart_mining
```

## Development & Testing

### Codebase structure

```
custom_components/vnish_miner/
├── __init__.py       # Config entry setup and platform forwarding
├── button.py          # Buttons (restart mining / reboot hardware)
├── config_flow.py     # Config flow + options flow
├── const.py            # Domain constants
├── coordinator.py     # DataUpdateCoordinator (summary/info/status/settings/presets)
├── entity.py           # Shared base entity
├── manifest.json
├── select.py            # Overclock preset select entity
├── sensor.py             # Metric sensors
├── strings.json / translations/
├── switch.py             # Pause/resume mining switch
└── vnish_client.py       # Async REST client (aiohttp)
```

### Running the test suite

Unit tests live in `tests/test_vnish.py`. They mock the `aiohttp` session and
cover:

- JSON response parsing for every endpoint (`summary`, `info`, `status`,
  `settings`, `autotune/presets`), including both the legacy flat payload
  and the VNish 1.3.x nested `miner` payload;
- hashrate unit conversion to TH/s;
- the payload sent when switching presets;
- calls to the action endpoints (`pause`, `resume`, `restart`, `reboot`);
- authentication (401/403) and connection error handling.

```bash
pip install aiohttp pytest
python3 -m pytest tests/test_vnish.py -v
```

## Disclaimer

This integration is not affiliated with VNish or Bitmain. Use it at your own
risk — remotely controlling overclock presets or rebooting an ASIC miner can
affect its hardware stability.
