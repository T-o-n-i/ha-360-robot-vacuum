# 360 Robot Vacuum

Home Assistant integration for Qihoo 360 robot vacuums that are controlled with the **360Robot** app (cloud `q.smart.360.cn`). Built and tested with two **360 S6**; other robots from the same app probably work too.

You sign in with the same account as in the app. All robots on the account show up as devices, and their state comes in live from the 360 cloud.

## What you get

Per robot:

- **Vacuum**: start, pause, stop, return to dock, locate, fan speed (`quiet`, `auto`, `strong`). Start on a paused robot resumes the run.
- **Sensors**: battery, status (the raw mode, with the full status as attributes), error code, cleaned area, cleaning time, time of the last status update.
- **Diagnostic sensors**: operating hours of filter, main brush, side brush and sensors since they were last reset (asked for once an hour).
- **Volume** of the voice prompts as a slider (0–10).
- **Diagnostics download** with the device list and the last raw messages from each robot.

## Installation

The integration is not in the Marketplace catalog, so searching for it there finds nothing. Add this repository to the Marketplace yourself; after that it installs and updates like any other integration.

The quickest way is this button. It opens the repository in your Marketplace:

[![Open in the Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=T-o-n-i&repository=ha-360-robot-vacuum&category=integration)

Or by hand:

1. *Settings → Marketplace*, menu (⋮) in the top right → *Custom repositories*.
2. Repository `https://github.com/T-o-n-i/ha-360-robot-vacuum`, type *Integration*, *Add*.
3. Open *360 Robot Vacuum* and install it.

Restart Home Assistant afterwards.

<!-- ha-marketplace:exclude-start -->
### Manual

Copy `custom_components/robot360` to `/config/custom_components/robot360` and restart Home Assistant.
<!-- ha-marketplace:exclude-end -->

## Setup

1. *Settings → Devices & services → Add integration → 360 Robot Vacuum*.
2. Enter the login you use in the 360Robot app: e-mail address or mobile number with country code (e.g. `+4917…`), and the password.
3. 360 usually asks for a picture captcha. The dialog shows it; type the characters.

That is the only time you need the password and the captcha. The integration keeps the account login (like the app does) and uses it for every restart and every new cloud session. Only if 360 stops accepting it, Home Assistant asks you to sign in again.

## Sending other commands

`vacuum.send_command` sends any command the 360 cloud knows. `command` is the info type, `params` the data. Example, set the volume to 5:

```yaml
action: vacuum.send_command
target:
  entity_id: vacuum.living_room
data:
  command: "21024"
  params:
    cmd: setVolume
    value: 5
```

## Status values

What a 360 S6 (firmware 2.10.10) reports:

| Mode (`sensor.*_status`) | Vacuum state |
| --- | --- |
| `sweep` | cleaning |
| `backcharge` | returning |
| `charge`, `fullcharge` | docked |

Further mode names (`pause`, `idle`, `spotClean`, …) come from other projects and are not confirmed on an S6 yet. Unknown names are guessed from the word (`…clean…` is cleaning, `back…` is returning). The exact value is always in the status sensor. If you see a mode that ends up wrong, please open an issue with it.

## Limits

- Cloud only. Without internet, or if 360 changes its API, the integration stops working.
- No map, no room or zone cleaning.
- Consumables are reported as operating hours. The cloud does not say how many hours a part is meant to last.

## Testing without Home Assistant

`tools/probe.py` signs in, lists the robots, asks for status and consumables and writes the answers (without password and session keys) to `tools/probe_output.json`. Handy to check a new model before installing.

```bash
python3 -m venv .venv && .venv/bin/pip install aiohttp cryptography
.venv/bin/python tools/probe.py
```

## Credits

The login and the push channel are ported from [TA2k/ioBroker.botslab360](https://github.com/TA2k/ioBroker.botslab360), the command payloads come from [juetthei/ha-360-vacuum](https://github.com/juetthei/ha-360-vacuum) (both MIT, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)). The frame layout of the push channel was worked out with the help of [cavefire/cn360-proxy](https://github.com/cavefire/cn360-proxy).

Not affiliated with or endorsed by Qihoo 360.
