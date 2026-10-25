# iRobot Local

[![hacs](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)

Home Assistant integration for Wi-Fi iRobot Roomba vacuums that works **entirely on your local network** over MQTT/TLS (port 8883). No iRobot cloud at runtime, no polling (`local_push`).

Tested with a Roomba i7 on Home Assistant 2026.10. Other models talking the same local protocol (9xx, e5, i/s-series) should work but are untested — reports welcome.

[Polska wersja README](README.pl.md)

> Unofficial project, not affiliated with or endorsed by iRobot Corporation. "iRobot" and "Roomba" are trademarks of iRobot Corporation.

## Features

- **Vacuum** — start / resume, pause, stop, return to base, locate, `send_command`
- **Room cleaning** — name the Smart Map rooms, map them to Home Assistant areas and use the native `vacuum.clean_area` action
- **Sensors** — battery, mission phase, error code (with description), mission duration, cleaned area, total missions, Wi-Fi signal (disabled by default)
- **Binary sensors** — bin full, bin missing
- **Buttons** — empty bin into the Clean Base (`evac`, i7+ only), mapping run (`train`)
- **Diagnostics switch** — record every MQTT message from the robot to a file, for reverse engineering
- **Setup wizard** — gets BLID and password from the robot itself, falls back to your iRobot account, or takes them manually; DHCP discovery, reconfigure and re-authentication included

TLS quirks of older robot firmware with Python 3.12+/OpenSSL 3 are handled: the client tries `AES128-SHA256` first, then the OpenSSL defaults (plus TLS 1.3), with `SECLEVEL=0` and `OP_LEGACY_SERVER_CONNECT`.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/JaKosinski/ha-myirobot`, category **Integration**.
2. Install **iRobot Local** and restart Home Assistant.

Updates then show up in Settings → Updates.

### Manual

Copy the `custom_components/irobot_local` folder from this repository to `/config/custom_components/` so you end up with `/config/custom_components/irobot_local/manifest.json`, and restart Home Assistant.

## Setup

Settings → Devices & services → Add integration → **iRobot Local**, then pick how to get the robot's BLID and password:

- **From the robot (local, recommended)** — enter the IP, hold the HOME button for ~2 s until the robot beeps, then submit within 30 s. BLID comes over UDP 5678, the password over TLS 8883.
- **From the iRobot account (cloud)** — offered automatically when the local method fails. Your iRobot email and password are used once to read the robot password and are **not stored**. Accounts using Google/Apple sign-in have no iRobot password and will not work.
- **Manually** — BLID and robot password. The password looks like `:1:1234567890:...`; **the leading colon is part of it**.

Whichever you choose, only the IP, BLID and robot password are saved, and the robot is then controlled locally only.

Robots that Home Assistant sees on the network via DHCP (`iRobot-*` / `Roomba-*` hostnames) are offered automatically, and a configured robot's IP address is updated by itself when it changes. You can also change the IP or password later with **Reconfigure** on the integration, and if the robot rejects its password (e.g. after a factory reset) Home Assistant asks for the new one.

### Network

Home Assistant must reach the robot on TCP 8883 (and UDP 5678 for the local setup method). The robot does not need internet access, which works well with an isolated IoT VLAN. Some firmware restarts Wi-Fi periodically when it cannot reach the cloud.

### One connection at a time

The robot accepts **a single local MQTT connection**. Remove the built-in *iRobot Roomba and Braava* integration for the same robot, and expect entities to become unavailable while the iRobot app is open — the integration reconnects automatically (1–60 s back-off) once the app is closed.

## Room cleaning

The robot only knows numeric region ids of its Smart Map; room names live in the iRobot cloud, so you name them yourself.

1. In the iRobot app, start a clean of **all** the rooms you want in Home Assistant once. The robot keeps it as `last_command`.
2. Settings → Devices & services → iRobot Local → **Configure**. Map id and room ids are prefilled (`5 = Room 5`). Rename them:

   ```
   5 = Kitchen
   12 = Living room
   zid:3 = Under the table
   ```

   Bare ids are rooms, `zid:<id>` are custom zones. To tell which id is which room, clean them one at a time from the app and watch the vacuum's `last_command` attribute.
3. In the vacuum entity settings, assign the robot's rooms to Home Assistant areas.
4. Clean areas with the native action — the order of areas is the cleaning order:

```yaml
action: vacuum.clean_area
target:
  entity_id: vacuum.roomba
data:
  cleaning_area_id:
    - kitchen
    - living_room
```

According to user reports the i7 cleans selected rooms without internet access, as it keeps its navigation map on board. Without configured rooms you can still send the raw command:

```yaml
action: vacuum.send_command
target:
  entity_id: vacuum.roomba
data:
  command: start
  params:
    pmap_id: "<pmap_id>"
    user_pmapv_id: "<user_pmapv_id>"
    ordered: 1
    regions:
      - region_id: "1"
        type: rid
```

## Mapping run

The **Mapping run** button sends `train`: the robot explores the home without cleaning to build its Smart Map. Approving the map, splitting it into rooms and naming them still happens in the iRobot app (cloud); this cannot be done locally.

## Recording MQTT messages

The **Record MQTT messages** switch (diagnostic) writes every message from the robot to `/config/irobot_local/<BLID>_<time>.jsonl`, one JSON object per line. Attributes show the file path and message count. Recording stops by itself at 50 MB and does not survive a Home Assistant restart.

The file contains network details (SSID, IP, MAC) and the BLID — review it before sharing.

## Development

```bash
pip install -r requirements_test.txt
pytest
```

Client and setup tests run a fake robot (TLS 1.2 / AES128-SHA256 + MQTT 3.1.1, UDP discovery) on localhost; config flow tests run inside Home Assistant via `pytest-homeassistant-custom-component`.

### Releasing

Bump `version` in `custom_components/irobot_local/manifest.json`, commit, then tag and push:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Then create a GitHub release from the tag (Releases → Draft a new release); HACS picks it up as an update.

## Credits

Protocol details from [dorita980](https://github.com/koalazak/dorita980) and [roombapy](https://github.com/pschmitt/roombapy).

## License

[MIT](LICENSE)
