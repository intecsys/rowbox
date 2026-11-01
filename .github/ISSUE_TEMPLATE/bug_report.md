---
name: Bug report
about: Something on your Rowbox doesn't work as expected
title: "[Bug] "
labels: bug
---

<!-- Please remove passwords, Wi-Fi names and other personal data from logs and screenshots. -->

### What happened?

<!-- A short description. What did you expect instead? -->

### Steps to reproduce

1.
2.
3.

### Setup

| | |
|---|---|
| Rowbox version | <!-- shown in the portal header, e.g. 2.2 --> |
| Raspberry Pi model | <!-- e.g. Pi Zero 2 W --> |
| Raspberry Pi OS | <!-- output of: cat /etc/os-release | grep PRETTY ; uname -r --> |
| Power | <!-- PiSugar 3 / USB power bank / power supply --> |
| PiSugar 3 detected? | <!-- yes / no / not used. Battery level shown on the display? --> |
| Whisplay working? | <!-- Display shows the Rowbox screens? Sound works? Output of: aplay -l --> |
| S4 firmware | <!-- portal Status page or display Info screen, e.g. 2.10 --> |
| Fitness app | <!-- e.g. Rowing Workout, EXR, Kinomap; app version; iPhone/Android --> |

### Logs

Web portal → **Logs** → the relevant tab (bridge, display, portal or Wi-Fi). Paste the lines around the problem:

```
paste logs here
```

<!-- Or via SSH: journalctl -u rowbridge -u rowdisplay -u rowportal -n 200 --no-pager -->

### Anything else?

<!-- Photos of the display, what you already tried, etc. -->
