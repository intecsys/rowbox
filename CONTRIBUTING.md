# Contributing to Rowbox

Thanks for helping! Rowbox is a non-commercial hobby project under GPL-3.0-or-later. By sending a
pull request you agree that your contribution is published under the same license.

## Architecture in one minute

```
S4 monitor --USB (CDC serial)--> rowbridge.py --Bluetooth FTMS--> fitness app
                                     |
                                     +--> /run/rowbridge/state.json  (1 Hz) --> rowdisplay.py, rowportal.py
                                     +--> /run/rowbridge/wave.json   (10 Hz, only while a screen needs it)
                                     +--> /var/lib/rowbridge/sessions.jsonl + tracks/  (logbook)
```

- **`rowbridge.py`** reads the S4 on `/dev/ttyACM0`: it polls the registers (distance, strokes,
  stroke rate, speed, watts, calories, time, heart rate) and parses the unsolicited messages
  (`Pxx` flywheel pulses every 25 ms, `SS`/`SE` stroke start/end). It publishes a Bluetooth FTMS
  rower (service 0x1826, Rower Data 0x2AD1) plus Device Information (0x180A) via BlueZ/D-Bus,
  keeps the logbook and writes the current state to `state.json` (atomic replace).
- **`rowstroke.py`** turns the pulses into strokes: drive/recovery time and a 30-point force curve
  (estimated from flywheel acceleration plus drag, no extra sensor).
- **`rowdisplay.py`** (Whisplay display, button, LED, sounds) and **`rowportal.py`** (web portal,
  Wi-Fi/hotspot, settings in `/etc/rowbox.json`) only *read* `state.json`. They never talk to the
  S4 directly. Small request files in `/run/rowbridge/` (e.g. `hotspot_request`, `key_request`)
  are the way back.
- **`rowgame.py`** journeys and trophies, **`rowexport.py`** FIT/TCX, **`rowtext.py`** translations.

Everything we know about the S4 itself (protocol, firmware, hardware) is in
[docs/s4-notes.md](docs/s4-notes.md).

## Develop without a rowing machine

```sh
python3 s4sim.py ./s4sim &              # simulated S4 (S4SIM_PULS=1 adds heart rate)
sudo python3 rowbridge.py ./s4sim       # bridge against the simulator
```

On the box, stop the service first (`sudo systemctl stop rowbridge`). For testing, the portal can
run on another port: `ROWBOX_PORT=8080 python3 rowportal.py`.

## Add a journey

Routes live in `ROUTES` in `rowgame.py`. Add one entry; the display name (`River: Start → End`)
and a trophy for finishing are created automatically, and the settings list it by continent.

```python
"danube": {"cont": "europe", "river": D("Donau", "Danube"), "km": 2415,
           "stops": [(0, "Ulm"), (390, D("Passau", "Passau")), (590, D("Wien", "Vienna")),
                     (650, "Bratislava"), (850, "Budapest"), (1220, "Belgrade"),
                     (2415, D("Schwarzes Meer", "Black Sea"))]},
```

`km` is the total length, `stops` are `(km from start, name)`; a name is a plain string or
`D(german, english)`. Continents: `europe`, `africa`, `asia`, `namerica`, `samerica`,
`australia`, `antarctica`, `ocean`. River kilometres may be rounded.

## Texts and languages

UI texts are written in German inside `L("…")`; the English translation goes into the `EN` dict in
`rowtext.py`. Run `python3 rowtext.py` before committing: it lists every text without a
translation.

## Pull requests

- Keep them small and focused; describe what you tested (real S4 or simulator, which app).
- Python 3 standard library plus the Debian packages installed by `setup.sh`; no pip installs.
- New files get the header `# SPDX-License-Identifier: GPL-3.0-or-later`.
- No personal data (Wi-Fi names, passwords, hostnames) in code, logs or screenshots.
- Bugs and ideas: please open an issue first, the bug template asks for the details we need.
