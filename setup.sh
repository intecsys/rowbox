#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 The Rowbox authors
# Rowbox setup: bridge, display, web portal, PiSugar 3, Whisplay
# Usage in the cloned folder:   sudo bash setup.sh            (language from the system locale)
#                               sudo bash setup.sh --lang de  (Deutsch)  /  --lang en (English)
# Without Whisplay display:     sudo bash setup.sh --no-display     (bridge and web portal only)
# Without PiSugar 3:            sudo bash setup.sh --no-pisugar     (USB power bank or power supply)
set -u
cd "$(dirname "$0")"

LNG="${ROWBOX_LANG:-}"
DISPLAY_HW=1
PISUGAR=1
while [ $# -gt 0 ]; do
    case "$1" in
        --lang) LNG="${2:-}"; shift ;;
        --no-display) DISPLAY_HW=0 ;;
        --no-pisugar) PISUGAR=0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done
if [ -z "$LNG" ]; then
    case "${LC_ALL:-${LANG:-}}" in de*) LNG=de ;; *) LNG=en ;; esac
fi
[ "$LNG" = de ] || LNG=en
T() { if [ "$LNG" = de ]; then echo "$1"; else echo "$2"; fi; }   # T "Deutsch" "English"

USR="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
HOMEDIR="$(getent passwd "$USR" | cut -d: -f6)"
LOG="$HOMEDIR/rowbox-setup.log"
exec > >(tee -a "$LOG") 2>&1
step() { echo; echo "=== $(date +%H:%M:%S) $* ==="; }
fail=0
check() { if [ "$1" -eq 0 ]; then echo "OK: $2"; else echo "$(T FEHLER ERROR): $2"; fail=1; fi; }

[ "$(id -u)" -eq 0 ] || { T "Bitte mit sudo starten: sudo bash setup.sh" "Please run with sudo: sudo bash setup.sh"; exit 1; }

step "1/6 $(T Pakete Packages)"
apt-get update -qq
for pkg in python3-serial python3-dbus python3-gi git python3-pil python3-numpy \
           python3-spidev python3-libgpiod alsa-utils python3-qrcode dnsmasq-base iw python3-paho-mqtt; do
    apt-get install -y -qq "$pkg" >/dev/null; check $? "$(T Paket Package) $pkg"
done

step "2/6 $(T "Brücke, Anzeige, Portal" "Bridge, display, portal")"
for f in rowbridge.py s4sim.py rowdisplay.py rowportal.py; do
    install -m 755 -o "$USR" -g "$USR" "$f" "$HOMEDIR/$f"
done
for f in rowexport.py rowtext.py rowstroke.py rowgame.py; do
    install -m 644 -o "$USR" -g "$USR" "$f" "$HOMEDIR/$f"
done
# Dateien fuer das Webportal (three.js und die 3D-Flussfahrt auf dem Handy)
install -d -o "$USR" -g "$USR" "$HOMEDIR/static"
for f in static/*; do
    install -m 644 -o "$USR" -g "$USR" "$f" "$HOMEDIR/$f"
done
cat > /etc/systemd/system/rowportal.service <<UNIT
[Unit]
Description=Rowbox web portal and hotspot
After=NetworkManager.service

[Service]
ExecStart=/usr/bin/python3 -u $HOMEDIR/rowportal.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
# Captive Portal: im Hotspot alle Namen auf die Box aufloesen
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
echo "address=/#/10.42.0.1" > /etc/NetworkManager/dnsmasq-shared.d/rowbox-captive.conf
cat > /etc/systemd/system/rowdisplay.service <<EOF
[Unit]
Description=Rowbox display (Whisplay)
After=rowbridge.service pisugar-server.service

[Service]
Environment=WHISPLAY_RUNTIME=$HOMEDIR/Whisplay/runtime
ExecStart=/usr/bin/python3 -u $HOMEDIR/rowdisplay.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/rowbridge.service <<EOF
[Unit]
Description=Rowbox WaterRower S4 Bluetooth bridge
After=bluetooth.service
Wants=bluetooth.service

[Service]
ExecStartPre=-/usr/sbin/rfkill unblock bluetooth
ExecStart=/usr/bin/python3 -u $HOMEDIR/rowbridge.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
if [ $DISPLAY_HW -eq 1 ]; then
    systemctl enable rowbridge rowdisplay rowportal
    check $? "$(T "Brücke, Anzeige und Portal als Dienste eingerichtet" "Bridge, display and portal set up as services")"
else
    systemctl disable --now rowdisplay 2>/dev/null
    systemctl enable rowbridge rowportal
    check $? "$(T "Brücke und Portal als Dienste eingerichtet (ohne Display)" "Bridge and portal set up as services (no display)")"
fi
rfkill unblock bluetooth

step "3/6 $(T "Schnittstellen I2C und SPI" "I2C and SPI interfaces")"
raspi-config nonint do_i2c 0; check $? "$(T "I2C an" "I2C on")"
raspi-config nonint do_spi 0; check $? "$(T "SPI an" "SPI on")"

step "3b $(T "WLAN-Fix für gemischte WPA2/WPA3-Netze" "Wi-Fi fix for mixed WPA2/WPA3 networks") (raspberrypi/linux #7634)"
CMD=/boot/firmware/cmdline.txt
if grep -q "brcmfmac.feature_disable=" "$CMD"; then
    sed -i 's/brcmfmac\.feature_disable=[^ ]*/brcmfmac.feature_disable=0x2282000/' "$CMD"
else
    sed -i '1 s/$/ brcmfmac.feature_disable=0x2282000/' "$CMD"
fi
[ "$(wc -l < "$CMD")" -le 1 ] && grep -q "feature_disable=0x2282000" "$CMD"
check $? "$(T "WLAN-Fix in $CMD (wirkt nach Neustart)" "Wi-Fi fix in $CMD (active after reboot)")"

step "4/6 PiSugar 3"
if [ $PISUGAR -eq 1 ]; then
wget -q -O /tmp/pisugar-power-manager.sh https://cdn.pisugar.com/release/pisugar-power-manager.sh
bash /tmp/pisugar-power-manager.sh -c release --non-interactive --model 'PiSugar 3' </dev/null
check $? "$(T PiSugar-Werkzeug "PiSugar tools")"
# Taste am PiSugar 3 lang halten: Pi erst sauber herunterfahren, dann Strom aus
PSCONF=/etc/pisugar-server/config.json
python3 - "$PSCONF" <<'PY'
import json, sys
p = sys.argv[1]
c = json.load(open(p))
c["soft_poweroff"] = True
c["soft_poweroff_shell"] = "systemctl poweroff"
# Obere (frei belegbare) Taste: kurz = Statusanzeige, lang = Sprache wechseln
c["single_tap_enable"] = True
c["single_tap_shell"] = "echo status > /run/rowbridge/key_request"
c["long_tap_enable"] = True
c["long_tap_shell"] = "echo lang > /run/rowbridge/key_request"
c["double_tap_enable"] = False
c["double_tap_shell"] = ""
json.dump(c, open(p, "w"), indent=2)
PY
check $? "$(T "PiSugar-Tasten: sauber herunterfahren, Status, Sprache" "PiSugar buttons: clean shutdown, status, language")"
systemctl restart pisugar-server
else
    T "Übersprungen (--no-pisugar): Ausschalten dann über das Webportal." "Skipped (--no-pisugar): shut down via the web portal."
fi

step "5/6 $(T "Whisplay-Treiber (baut ein Kernelmodul, dauert auf dem Zero eine Weile)" "Whisplay driver (builds a kernel module, takes a while on the Zero)")"
if [ $DISPLAY_HW -eq 1 ]; then
    if [ ! -d "$HOMEDIR/Whisplay" ]; then
        sudo -u "$USR" git clone -q --depth 1 https://github.com/PiSugar/Whisplay.git "$HOMEDIR/Whisplay"
    fi
    ( cd "$HOMEDIR/Whisplay" && echo y | bash script/install_raspberry_pi.sh )
    check $? "$(T Whisplay-Treiber "Whisplay driver")"
else
    T "Übersprungen (--no-display)." "Skipped (--no-display)."
fi

step "6/6 $(T Portal "Web portal")"
# Einstellungen anlegen (Passwort, Hotspot-Name) und festhalten, ob es ein Display gibt
python3 - "$DISPLAY_HW" "$LNG" "$HOMEDIR" <<'PY'
import json, os, sys
sys.path.insert(0, sys.argv[3])
import rowportal
c = rowportal.load_conf()
c["display"] = sys.argv[1] == "1"
rowportal.save_conf(c)
if not c["display"]:
    # ohne Display gibt es keinen Assistenten: Sprache setzen und Einrichtung als erledigt markieren
    os.makedirs("/var/lib/rowbridge", exist_ok=True)
    open("/var/lib/rowbridge/language", "w").write(sys.argv[2])
    with open(rowportal.ONBOARD, "w") as f:
        json.dump({"done": True, "ok": [], "skipped": []}, f)
print(c["password"], c["ssid"])
PY
check $? "$(T "Einstellungen in /etc/rowbox.json" "Settings in /etc/rowbox.json")"
read -r PW HSSID < <(python3 -c 'import json; c=json.load(open("/etc/rowbox.json")); print(c["password"], c["ssid"])')
if [ $DISPLAY_HW -eq 1 ]; then
    T "Webportal: http://$(hostname).local – das Passwort steht auf dem Display unter Info" \
      "Web portal: http://$(hostname).local – the password is shown on the display under Info"
else
    T "Webportal: http://$(hostname).local" "Web portal: http://$(hostname).local"
    T "Passwort (Portal und Hotspot): $PW" "Password (portal and hotspot): $PW"
    T "Hotspot, falls kein WLAN da ist: $HSSID" "Hotspot if no known Wi-Fi is around: $HSSID"
    T "Bitte notieren. Später nachsehen: sudo grep password /etc/rowbox.json" \
      "Please write it down. Look it up later: sudo grep password /etc/rowbox.json"
    T "QR-Code für den Hotspot (an einem neuen Ort ohne bekanntes WLAN damit verbinden):" \
      "QR code for the hotspot (join it at a new place without a known Wi-Fi):"
    python3 - "$HOMEDIR" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
import qrcode, rowportal
c = json.load(open("/etc/rowbox.json"))
q = qrcode.QRCode(border=2)
q.add_data(rowportal.wifi_qr_text(c["ssid"], c["password"]))
q.print_ascii(invert=True)
PY
    T "Zum Ausdrucken und Aufkleben: Webportal → Einstellungen → Hotspot → Karte zum Ausdrucken" \
      "To print and stick on the box: web portal → Settings → Hotspot → Printable card"
fi

step "$(T Fertig Done)"
if [ $fail -eq 0 ]; then
    T "Alles eingerichtet. Jetzt neu starten: sudo reboot" "All set. Now reboot: sudo reboot"
    if [ $DISPLAY_HW -eq 1 ]; then
        T "Nach dem Neustart fragt das Display nach der Sprache." "After the reboot the display asks for the language."
    else
        T "Nach dem Neustart: App mit „WaterRower S4“ verbinden, S4 einstecken, rudern." \
          "After the reboot: connect your app to “WaterRower S4”, plug in the S4, row."
    fi
else
    T "Mindestens ein Schritt ist fehlgeschlagen, siehe FEHLER-Zeilen oben." \
      "At least one step failed, see the ERROR lines above."
    echo "$(T Protokoll Log): $LOG"
fi
