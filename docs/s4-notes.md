# S4 monitor notes

What we know about the WaterRower S4 performance monitor, collected while building Rowbox.
Facts only, in our own words; drawings and manuals remain WaterRower's and are linked, not copied.
Items marked *unconfirmed* are educated guesses. Corrections and photos are welcome.

## USB interface

- Shows up as a USB CDC serial device (`/dev/ttyACM0` on Linux), USB ID `04d8:000a`,
  manufacturer string "Microchip Technology Inc." (Microchip's default ID for CDC serial demos).
- The baud rate is ignored by the virtual serial port; Rowbox opens it at 19200.
- `USB` starts the session. `IR{S,D,T}xxx` reads a memory location, answered with `ID…`.
- Unsolicited messages: `Pxx` flywheel pulses in the last 25 ms (hex, 7–13 while rowing),
  `SS`/`SE` stroke start/end, `PING` while idle.
- `IV?` returns `IV` + model + version high + version low, e.g. `IV40210` = S4, firmware 2.10.
- The protocol itself is documented by WaterRower ("Water Rower S4 S5 USB Protocol Iss 1 04").

## Firmware

- USB monitors ("S4 version 2", Mini-USB) run firmware **2.01**, **2.03** or the current **2.1**.
  WaterRower upgrades 2.01/2.03 on request via its service contact form.
  ([WaterRower service](https://www.waterrowerservice.com/?p=5014))
- Older 1.x firmware: WaterRower's archived firmware page (2011 and 2016, unchanged) lists 1.49 as
  current at the time; back then updates meant sending in the circuit board. This most likely refers
  to the earlier RS-232 monitors. ([archived page](https://web.archive.org/web/20160122212732/http://www.waterrower.biz/en/upgrades_firmware.htm))
- There are no public release notes and no update tool.
- Reading the version on the S4: switch on, press **advanced** nine times, then **OK**; the version
  appears bottom right. Rowbox shows it in the portal (Status) and on the display (Info).

## Hardware

- Generations: "S4 version 1" has an RS-232 serial port, "S4 version 2" a Mini-USB port on the
  back. WaterRower offered a board upgrade to USB (needed for the ComModule). Rowbox needs the USB
  version.
- Service parts (from WaterRower's monitor drawings):
  - **WRP-C004 S4 Monitor Board Assy**: front panel with LCD, keypad and circuit board as one unit
  - **WRP-C500 Housing Assy**: back shell with battery compartment and the tube the sensor cable runs through
  - **WRP-C510 Sensor Assy and Disk**: optical sensor under the top deck, reading a disk that sits on
    the clutch on top of the paddle shaft
  - **WRP-C025 Housing Screw Set**: four screws hold the board assembly on the back shell
- The sensor cable plugs into the board; WaterRower's instructions say the red wire faces the
  outer edge of the board (*unconfirmed*: likely the supply line).
- Display: monochrome segment LCD with fixed legends. Power: batteries only, no mains input.
- Microcontroller: a Microchip PIC with built-in USB (*unconfirmed*: probably a PIC18F such as
  PIC18F2550/4550; nobody seems to have published a board photo yet).
- Sources: [S4 monitor drawings (archived)](https://web.archive.org/web/20160122212732/http://www.waterrower.biz/service/monitor/monitor_drawings.htm),
  [S4 Monitor Board Upgrade (WaterRower AU)](https://www.waterrower.com.au/s4-monitor-upgrade-v-2),
  [WaterRower S4 product page](https://www.waterrower.com/wr_ae/waterrower-s4-monitor.html) ("optical sensor")
