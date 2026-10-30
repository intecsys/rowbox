# Changelog

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
