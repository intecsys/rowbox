# Changelog

## Unreleased
- Light (no battery: `setup.sh --no-pisugar`) and Ultralight (no display, no battery: `setup.sh --no-display --no-pisugar`) versions; without display, setup prints the portal password and the portal hides display settings (experimental)
- Phone as display: `/display` (or `/anzeige`) shows the box's screens full-screen on the phone, laid out for portrait and swipeable: training, force curve (last five strokes), pacer (vertical seat), live wave, journey (river drawn with stops and a boat at your position, progress bar), status (big verdict like "Ready to row" plus four status cards); linked from the Live page and in the menu
- Wi-Fi messages in the portal no longer refer to the display
- Hotspot card: printable label with hotspot name, password and Wi-Fi QR code (portal → Settings → Hotspot); `setup.sh` prints the QR code for boxes without display
- Phone display: swipe bar with arrows at the bottom of every screen
- Phone display: 3D view on the journey screen (three.js, bundled): single scull with the rower in the real stroke rhythm, banks, trees, towns with church, bridge and name sign at each stage; renders only while the journey screen is visible; the rowing motion follows the force curve of the last stroke (legs, then body; boat surges on the drive) on its own smooth stroke clock
- Phone display, 3D view: the Rhine follows its real course from Basel to Rotterdam (OpenStreetMap), with 74 real bridges by name, towns on their real bank, a small map with your position and the OSM credit; other routes keep the generated river. Routes are built with `tools/build_route.py` and ship as small files in `static/routes/` (Rhine: ~100 KB), so the box stays offline
- Phone display, 3D view: fixed the rower sometimes staying invisible after the first frame
- Phone display, 3D view: real terrain along the Rhine (AWS Terrain Tiles, ~30 m grid), so the Middle Rhine valley rises 150–200 m on both banks; 242 landmarks from OpenStreetMap as simple models with name signs (castles, ruins, cathedrals, churches, towers, windmills, rocks such as the Loreley; Pfalzgrafenstein sits in the river)
- Phone display, 3D view: new look: sky with colour gradient and sun by time of day, haze toward the horizon, water that reflects the sky with moving ripples, wake behind the boat, mixed forest (broadleaf and conifers in groups), vineyard and rock colours on steep slopes, fields in the flat
- Phone display, 3D view: real weather at your virtual position on the route (Open-Meteo, fetched by the box about every 15 minutes when online): cloudy sky, rain and snow, fog by visibility, wind on the water, temperature and weather in a small label; switch in Settings → Journey; nothing changes offline
- Phone display, 3D view: the rower only starts a new drive when the box detects a real catch; after the recovery he waits at the catch and goes to rest after a pause (before, the animation kept rowing a few strokes)
- Phone display, 3D view: real sun position for the place on the route and the current time (night, dawn, dusk, sun direction)
- Phone display: the journey list shortens itself on small screens instead of running into the destination
- Portal: status dots in the same colours as on the display (green ok, red problem, blue waiting), also in the header; S4 firmware on its own line so the status rows don't crowd on phones
- Phone display, 3D view: new single scull and rower: 8 m hull with pointed bow and stern, deck, cockpit, orange stripe, wing rigger, oars with grip, collar and hatchet blade; the rower has legs, arms and hands on the handles (two-bone joints), feet on the footstretcher, knees rise on the slide, layback at the finish
- README: compatibility tables for apps and rowing machines; corrected S4 firmware info (USB monitors run 2.01/2.03/2.1; check with advanced ×9, OK)

## 2.2
- The S4's model and firmware version are read on connect and shown in the portal status, on the display's Info screen and in the bridge log (helps with bug reports)
- Log tabs in the portal are translated (Bridge, Display, Portal, Wi-Fi); old links keep working
- Raw S4 recordings are now named `s4-raw-…`; older `s4-roh-…` files still show up
- Bug report template and CONTRIBUTING.md

## 2.1.1
- First public release under GPL-3.0-or-later
- English: "Strokes" label capitalised

## 2.1
- Nine more routes on every continent: Thames, Seine, Po, Nile, Yangtze, Mississippi, Amazon, Murray and the Onyx in Antarctica; grouped by continent in the settings
- Journey shown like a navigation screen (display and portal): next stage and destination with distance and estimated arrival
- Arrival while rowing from the current pace (clock time), otherwise from the average of the last four weeks including rest days (date); always with a buffer
- The running session already counts on the journey screen

## 2.0
- Journey: rowed metres move a boat along a real route (English Channel 34 km, Rhine 834 km, Atlantic 4,800 km); places reached are shown on the display after a session
- Trophies: 20 trophies for things like first session, 10 km in total, one hour in one go, early bird or a very steady session; shown on the display with a sound, listed in the portal
- New portal page and display screen "Journey"; route and on/off in the settings
- No streaks, deadlines or lost progress; existing sessions count towards the first journey

## 1.9.4
- Saved Wi-Fi networks can be connected directly from the portal
- Networks saved twice are marked, with their profile names, and a hint to remove the older one
- Wi-Fi LED in the portal header shows the box's own hotspot in blue

## 1.9.3
- Dismissing the hotspot screen with the display button makes the box try known Wi-Fi networks again (if no phone is connected to the hotspot)
- Hotspot without a connected phone retries known networks every 2 minutes (was 5)
- After boot the box waits 2 minutes for known Wi-Fi before opening the hotspot, and never while NetworkManager is still connecting
- Status screen: own hotspot shown as "Hotspot" instead of a Wi-Fi name, hint text no longer overlaps the screen label

## 1.9.2
- DE | EN switch in the portal header and on the sign-in page

## 1.9.1
- Orange buttons are solid orange (no light border in dark mode)

## 1.9
- New web portal design: hardware-style keys, status LEDs in the header, light and dark mode (follows the phone setting)

## 1.8
- Choose in the settings which screens the display button cycles through (automatic and Info are always included)

## 1.7.1
- Pauses inside a session no longer count as recovery time

## 1.7
- Pacer on the display: moves like the seat in your own rhythm (or a fixed stroke rate) and marks whether each stroke was in time
- Live wave on the display: paddle-wheel speed of the last six seconds
- Both as extra screens and as a strip in the training screen; choose pacer, wave or off in the settings

## 1.6
- Force curve from the S4's paddle-wheel pulses: live in the browser, on the display (new screen) and per session in the logbook
- Drive and recovery time and their ratio
- Pause countdown: the display stays on the training screen during a short break and shows when the session ends automatically
- Logbook distance now includes the first metres and the roll-out after the last stroke
- Simulator produces realistic sensor pulses

## 1.5
- Upper PiSugar 3 button as a shortcut: short press shows the status screen, long press switches the language

## 1.4
- German and English: display, web portal, setup script and exports
- Language choice as the first step of the setup assistant, changeable in the portal settings
- README screenshots in both languages

## 1.3
- Recording of raw S4 messages (pulse counts per 25 ms, stroke start/end, stroke timing registers) from the portal, for analysis

## 1.2
- Per-second data points for every session
- Export of each session as FIT (sport: indoor rowing) and TCX from the logbook

## 1.1
- Bluetooth Device Information service (manufacturer, model, version) for wider app compatibility
- Heart rate from the S4 (if it has a receiver) passed to the app and the logbook
- Live view in the browser (`/live`)
- Optional MQTT with Home Assistant discovery

## 1.0
- S4 (USB) → Bluetooth FTMS bridge: distance, stroke rate, strokes, pace, power, calories, time; reset from the app is passed to the S4
- Whisplay display: status, training, summary, history, info; LED and backlight only fully on/off (no flicker)
- Logbook on the box, download as CSV/JSON
- Web portal with login, Wi-Fi management, logs, settings, restart/shutdown
- Hotspot fallback with QR code and captive portal
- Setup assistant on the display (Wi-Fi, portal login by QR code, S4, app, test stroke)
- Clock from the PiSugar 3 RTC when offline; sounds on connect/disconnect/saved session
- Display button: hold 3 s opens or closes the hotspot; PiSugar 3 button: hold for a clean shutdown
- S4 simulator for testing without the rower
