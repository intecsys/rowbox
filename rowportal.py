#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 The Rowbox authors
"""rowportal: Weboberflaeche und Hotspot der Rowbox.

- Laeuft auf Port 80. Im normalen WLAN erreichbar unter http://<hostname>.local
  (mit Passwort), im eigenen Hotspot ohne Passwort.
- Findet die Box 60 s lang kein bekanntes WLAN, startet sie einen eigenen
  Hotspot. Das Handy oeffnet die Einrichtungsseite dann von selbst.
- Seiten: Status, Live, WLAN, Logbuch, Protokolle, Einstellungen.
- Logbuch-Einheiten als FIT oder TCX herunterladen (fuer Strava, Garmin Connect usw.).
- Optional: sendet die Werte per MQTT, z. B. an Home Assistant.
"""
import base64, csv, gzip, hashlib, html, io, json, os, re, secrets, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # rowtext.py liegt daneben
from rowtext import L, LANGS, fmt_date, fmt_num, get_lang, set_lang
import rowgame

CONF = "/etc/rowbox.json"
STATE = "/run/rowbridge/state.json"
WAVE = "/run/rowbridge/wave.json"          # Radtempo 10 Hz, nur solange WAVE_ON frisch ist
WAVE_ON = "/run/rowbridge/wave_on"
PORTAL_STATE = "/run/rowbridge/portal.json"
LOGBOOK = "/var/lib/rowbridge/sessions.jsonl"
TRACKS = "/var/lib/rowbridge/tracks"
RAW_FLAG = "/var/lib/rowbridge/raw_on"
RAW_DIR = "/var/lib/rowbridge/raw"
RAW_NAME = re.compile(r"^s4-r(aw|oh)-[0-9_-]+\.log$")   # "roh": Dateien von vor 2.2
HOTSPOT_CON = "rowbox-hotspot"
HOTSPOT_NET = "10.42.0."
NO_WIFI_AFTER = 60       # Sekunden ohne WLAN, dann Hotspot
BOOT_GRACE = 120         # nach dem Start laenger warten: das WLAN des Zero braucht beim Booten Zeit
RETRY_EVERY = 120        # im Hotspot ohne verbundenes Handy so oft bekannte Netze probieren
HOST = __import__("socket").gethostname() + ".local"
HOTSPOT_REQ = "/run/rowbridge/hotspot_request"      # von der Taste am Display
LOGIN_TOKEN = "/run/rowbridge/login_token.json"   # fuer den QR-Code auf dem Display
ONBOARD = "/var/lib/rowbridge/onboarding.json"
PORTAL_SEEN = "/var/lib/rowbridge/portal_seen"
TOKEN_TTL = 600                                     # Sekunden, dann neuer Einmal-Link
PISUGAR_SOCK = "/tmp/pisugar-server.sock"

DEFAULTS = {"led": "warn", "timeout": 120, "summary": 600, "hotspot": True, "sound": True,
            "password": None, "ssid": None,
            "mqtt_host": "", "mqtt_port": 1883, "mqtt_user": "", "mqtt_password": "",
            "mqtt_topic": "rowbox", "anim": "pacer", "pacer_spm": 0, "views": None, "game": True,
            "display": True,          # False: ohne Whisplay eingerichtet (setup.sh --no-display)
            "weather": True,          # echtes Wetter an der virtuellen Position (nur wenn die Box online ist)
            "journey_show": None,     # None = alle Elemente der Reise-Ansicht auf dem Handy
            "boat_hull": "#f4f4f0", "boat_blade": "#ff5a1f", "boat_shirt": "#ff5a1f"}
# Elemente der Reise-Ansicht auf dem Handy, einzeln abschaltbar
JOURNEY_PARTS = [("scene", "3D-Ansicht"), ("live", "Live-Werte (Pace, Schläge, Watt)"), ("stage", "Nächste Etappe"),
                 ("list", "Orte auf der Strecke"), ("goal", "Ziel"), ("map", "Kleine Karte in der 3D-Ansicht"),
                 ("weather", "Wetter-Schild in der 3D-Ansicht")]
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
# Ansichten, die man mit der Display-Taste durchblaettern kann (Automatik ist immer dabei).
# Info bleibt immer an: dort steht das Passwort fuers Portal.
VIEW_CHOICES = [("status", "Status"), ("training", "Training"), ("force", "Kraftkurve"),
                ("pacer", "Taktgeber"), ("wave", "Live-Kurve"), ("summary", "Letzte Einheit"),
                ("history", "Verlauf"), ("journey", "Reise"), ("info", "Info")]
lock = threading.Lock()
SESSIONS = {}            # Anmelde-Token -> Ablaufzeit (nur im Speicher)
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_FILES = {"three.module.min.js": "text/javascript", "river3d.js": "text/javascript",
                "icon-180.png": "image/png", "icon-512.png": "image/png", "manifest.webmanifest": "application/manifest+json"}
STATIC_LONG = {"three.module.min.js", "icon-180.png", "icon-512.png"}   # aendert sich praktisch nie
STATIC_CACHE = {}
SESSION_DAYS = 30


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------- Konfiguration ----------

def load_conf():
    try:
        with open(CONF) as f:
            c = {**DEFAULTS, **json.load(f)}
    except (OSError, ValueError):
        c = dict(DEFAULTS)
    changed = False
    if not c["password"]:
        c["password"] = "".join(secrets.choice("abcdefghkmnpqrstuvwxyz23456789") for _ in range(10))
        changed = True
    if not c["ssid"]:
        mac = open("/sys/class/net/wlan0/address").read().strip() if os.path.exists(
            "/sys/class/net/wlan0/address") else "000000"
        c["ssid"] = "Rowbox-" + mac.replace(":", "")[-4:].upper()
        changed = True
    if changed:
        save_conf(c)
    return c


def save_conf(c):
    tmp = CONF + ".tmp"
    with open(tmp, "w") as f:
        json.dump(c, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONF)


def write_portal_state(**kw):
    try:
        os.makedirs(os.path.dirname(PORTAL_STATE), exist_ok=True)
        tmp = PORTAL_STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(kw, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, PORTAL_STATE)
    except OSError:
        pass


# ---------- Einmal-Anmeldelink ----------

def new_login_token():
    tok = {"token": secrets.token_urlsafe(16), "exp": time.time() + TOKEN_TTL}
    try:
        os.makedirs(os.path.dirname(LOGIN_TOKEN), exist_ok=True)
        with open(LOGIN_TOKEN + ".tmp", "w") as f:
            json.dump(tok, f)
        os.chmod(LOGIN_TOKEN + ".tmp", 0o600)
        os.replace(LOGIN_TOKEN + ".tmp", LOGIN_TOKEN)
    except OSError:
        pass
    return tok


def use_login_token(t):
    """True, wenn t der aktuelle, noch gueltige Einmal-Token ist. Danach ist er verbraucht."""
    try:
        with open(LOGIN_TOKEN) as f:
            cur = json.load(f)
    except (OSError, ValueError):
        return False
    ok = bool(t) and cur.get("exp", 0) > time.time() and secrets.compare_digest(t, cur.get("token", ""))
    if ok:
        new_login_token()
    return ok


def mark_portal_seen():
    try:
        os.makedirs(os.path.dirname(PORTAL_SEEN), exist_ok=True)
        open(PORTAL_SEEN, "a").close()
    except OSError:
        pass


# ---------- Uhrzeit (Pi hat keine eigene Uhr, der PiSugar 3 schon) ----------

def pisugar_cmd(cmd):
    import socket
    try:
        with socket.socket(socket.AF_UNIX) as so:
            so.settimeout(2)
            so.connect(PISUGAR_SOCK)
            so.sendall(cmd.encode() + b"\n")
            return so.recv(200).decode().strip()
    except OSError:
        return None


def ntp_synced():
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return out == "yes"
    except (OSError, subprocess.SubprocessError):
        return False


class Clock:
    """Ohne Netz: Uhrzeit aus dem PiSugar holen. Mit Netz: PiSugar nachstellen."""
    def __init__(self):
        self.from_rtc = False
        self.to_rtc_at = 0

    def tick(self):
        if ntp_synced():
            if time.time() - self.to_rtc_at > 3600:
                if pisugar_cmd("rtc_pi2rtc") is not None:
                    log("Uhrzeit in den PiSugar geschrieben")
                self.to_rtc_at = time.time()
        elif not self.from_rtc:
            rtc = pisugar_cmd("get rtc_time") or ""
            m = re.search(r"(\d{4})-\d\d-\d\d", rtc)
            if m and int(m.group(1)) >= 2025:       # nur eine plausibel gestellte Uhr uebernehmen
                if pisugar_cmd("rtc_rtc2pi") is not None:
                    log("Uhrzeit aus dem PiSugar uebernommen: " + rtc)
            else:
                log("PiSugar-Uhr noch nicht gestellt, warte auf Netzzeit")
            self.from_rtc = True


# ---------- NetworkManager ----------

def nm(*args, timeout=20):
    try:
        r = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "Zeitueberschreitung"


def split_t(line):
    """nmcli -t trennt mit ':', escaped als '\\:'."""
    return [p.replace("\x00", ":") for p in line.replace("\\:", "\x00").split(":")]


def wlan_connection():
    _, out, _ = nm("-t", "-f", "DEVICE,STATE,CONNECTION", "dev")
    for line in out.splitlines():
        dev, st, con = (split_t(line) + ["", "", ""])[:3]
        if dev == "wlan0":
            return st, con
    return "", ""


def saved_networks():
    _, out, _ = nm("-t", "-f", "NAME,TYPE,AUTOCONNECT-PRIORITY", "connection", "show")
    nets = []
    for line in out.splitlines():
        name, typ, prio = (split_t(line) + ["", "", ""])[:3]
        if typ == "802-11-wireless" and name != HOTSPOT_CON:
            _, ssid, _ = nm("-g", "802-11-wireless.ssid", "connection", "show", name)
            nets.append({"name": name, "ssid": ssid, "prio": prio})
    return nets


def scan(rescan=True):
    _, out, _ = nm("-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
                   "--rescan", "yes" if rescan else "no", timeout=30)
    best = {}
    for line in out.splitlines():
        ssid, sig, sec = (split_t(line) + ["", "", ""])[:3]
        if ssid and (ssid not in best or int(sig or 0) > best[ssid]["signal"]):
            best[ssid] = {"ssid": ssid, "signal": int(sig or 0), "security": sec}
    return sorted(best.values(), key=lambda n: -n["signal"])


class Wifi:
    """Hotspot als Rueckfall, wenn kein bekanntes WLAN erreichbar ist."""
    def __init__(self, conf):
        self.conf = conf
        self.mode = "start"            # client / hotspot / connecting / start
        self.lost_since = self.started = time.time()
        self.hotspot_since = 0
        self.networks = []
        self.msg, self.msg_args, self.failed = "", {}, False

    @property
    def message(self):
        """Letzte Meldung in der eingestellten Sprache."""
        return L(self.msg, **self.msg_args) if self.msg else ""

    def say(self, msg, failed=False, **args):
        self.msg, self.msg_args, self.failed = msg, args, failed

    def publish(self):
        write_portal_state(mode=self.mode, ssid=self.conf["ssid"], password=self.conf["password"],
                           url="http://10.42.0.1", message=self.message, failed=self.failed)

    def start_hotspot(self):
        log("Starte Hotspot " + self.conf["ssid"])
        self.networks = scan() or self.networks
        nm("connection", "delete", HOTSPOT_CON)
        code, _, err = nm("dev", "wifi", "hotspot", "ifname", "wlan0", "con-name", HOTSPOT_CON,
                          "ssid", self.conf["ssid"], "password", self.conf["password"])
        nm("connection", "modify", HOTSPOT_CON, "connection.autoconnect", "no")
        if code:
            log("Hotspot fehlgeschlagen: " + err)
            return
        self.mode, self.hotspot_since = "hotspot", time.time()
        self.publish()

    def stop_hotspot(self, retry=False):
        nm("connection", "down", HOTSPOT_CON)
        self.mode = "start"
        self.lost_since = time.time()
        if retry:                                  # bekannte Netze gezielt anstossen
            nm("device", "wifi", "rescan", timeout=15)
            nm("device", "connect", "wlan0", timeout=40)

    def clients(self):
        try:
            out = subprocess.run(["iw", "dev", "wlan0", "station", "dump"],
                                 capture_output=True, text=True, timeout=5).stdout
            return out.count("Station ")
        except (OSError, subprocess.SubprocessError):
            return 0

    def connect_saved(self, name, ssid):
        """Gespeichertes Netz auf Wunsch verbinden. Laeuft im Hintergrund."""
        with lock:
            self.mode = "connecting"
            self.say("Verbinde mit {ssid} ...", ssid=ssid)
            self.publish()
            time.sleep(2)                  # Antwortseite noch ausliefern
            nm("connection", "down", HOTSPOT_CON)
            code, _, err = nm("connection", "up", name, timeout=45)
            if code == 0:
                self.mode = "client"
                self.say("Verbunden mit {ssid}", ssid=ssid)
                log(f"Verbunden mit {ssid} ({name})")
            else:
                self.say("Verbindung zu {ssid} fehlgeschlagen. Passwort prüfen.", failed=True, ssid=ssid)
                log(f"Verbindung zu {ssid} ({name}) fehlgeschlagen: {err}")
                self.mode = "start"
                self.lost_since = 0        # sofort zurueck in den Hotspot
            self.publish()

    def connect(self, ssid, password):
        """Neues Netz speichern und verbinden. Laeuft im Hintergrund."""
        with lock:
            self.mode = "connecting"
            self.say("Verbinde mit {ssid} ...", ssid=ssid)
            self.publish()
            name = "wifi-" + re.sub(r"[^A-Za-z0-9_-]", "_", ssid)[:24]
            nm("connection", "delete", name)
            args = ["connection", "add", "type", "wifi", "con-name", name, "ssid", ssid,
                    "connection.autoconnect-priority", "10"]
            if password:
                args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
            code, _, err = nm(*args)
            if code:
                self.say("Speichern fehlgeschlagen: {err}", failed=True, err=err)
            else:
                time.sleep(2)              # Antwortseite noch ausliefern
                nm("connection", "down", HOTSPOT_CON)
                code, _, err = nm("connection", "up", name, timeout=45)
                if code == 0:
                    self.mode = "client"
                    self.say("Verbunden mit {ssid}", ssid=ssid)
                    log(f"Verbunden mit {ssid}")
                else:
                    self.say("Verbindung zu {ssid} fehlgeschlagen. Passwort prüfen.", failed=True, ssid=ssid)
                    log(f"Verbindung zu {ssid} fehlgeschlagen: {err}")
                    self.mode = "start"
                    self.lost_since = 0    # sofort zurueck in den Hotspot
            self.publish()

    def loop(self):
        clock = Clock()
        while True:
            try:
                clock.tick()
                tok = read_json(LOGIN_TOKEN, {})
                if tok.get("exp", 0) < time.time() + 60:
                    new_login_token()
            except Exception as ex:          # Nebensachen duerfen das WLAN nicht stoppen
                log(f"Uhr/Token: {ex}")
            with lock:
                st, con = wlan_connection()
                now = time.time()
                if self.mode == "connecting":
                    pass
                elif con == HOTSPOT_CON:
                    self.mode = "hotspot"
                    if (now - self.hotspot_since > RETRY_EVERY and self.clients() == 0
                            and saved_networks()):
                        log("Hotspot ohne Nutzer, probiere bekannte Netze")
                        self.stop_hotspot(retry=True)
                elif st == "connected":
                    if self.mode != "client":
                        log(f"WLAN verbunden: {con}")
                    self.mode, self.lost_since = "client", now
                else:
                    if self.mode == "client":
                        self.mode, self.lost_since = "start", now
                    wait = BOOT_GRACE if now - self.started < BOOT_GRACE + 30 else NO_WIFI_AFTER
                    if st.startswith("connecting") and now - self.lost_since < 3 * wait:
                        pass                   # NetworkManager verbindet gerade, nicht dazwischenfunken
                    elif self.conf.get("hotspot") and now - self.lost_since > wait:
                        self.start_hotspot()
                self.publish()
            # zehn Sekunden warten, aber auf die Taste am Display sofort reagieren
            for _ in range(10):
                if os.path.exists(HOTSPOT_REQ):
                    self.manual_request()
                    break
                time.sleep(1)

    def manual_request(self):
        """Taste am Display lang gehalten: Hotspot oeffnen oder schliessen."""
        try:
            with open(HOTSPOT_REQ) as f:
                want = f.read().strip()
            os.remove(HOTSPOT_REQ)
        except OSError:
            return
        with lock:
            if self.mode == "connecting":
                return
            if want == "retry" and self.mode == "hotspot":
                if self.clients() == 0 and saved_networks():
                    log("Hotspot-Anzeige weggedrueckt, probiere bekannte Netze")
                    self.stop_hotspot(retry=True)
            elif want == "stop" and self.mode == "hotspot":
                log("Hotspot per Taste geschlossen")
                self.stop_hotspot(retry=True)
            elif want == "start" and self.mode != "hotspot":
                log("Hotspot per Taste geoeffnet")
                self.start_hotspot()
            self.publish()


# ---------- Daten fuer die Seiten ----------

def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def sessions():
    try:
        with open(LOGBOOK) as f:
            return [json.loads(x) for x in f.read().splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def battery():
    import socket
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(1)
            s.connect("/tmp/pisugar-server.sock")
            s.sendall(b"get battery\n")
            return round(float(s.recv(100).decode().split(":", 1)[1]))
    except (OSError, ValueError, IndexError):
        return None


def mmss(sec):
    sec = int(sec or 0)
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def num(n):
    return fmt_num(n)


def e(x):
    return html.escape(str(x))


CSS = """
:root{--chassis:#e4e4df;--panel:#f4f4f1;--ink:#000;--mute:#7d7d77;--rule:#cfcfc8;--key:#fbfbf9;
--signal:#ff5a1f;--wait:#468cff;--good:#2fb463;--bad:#e5463c;--sans:"Helvetica Neue",Helvetica,Arial,"Nimbus Sans","Liberation Sans",sans-serif}
@media (prefers-color-scheme:dark){:root{--chassis:#000;--panel:#161615;--ink:#f2f2ee;--mute:#8d8d86;
--rule:#2c2c2a;--key:#202020}}
*{box-sizing:border-box}
body{margin:0;background:var(--chassis);color:var(--ink);font:16px/1.45 var(--sans);
-webkit-font-smoothing:antialiased}
header{max-width:760px;margin:auto;padding:18px 16px 6px}
.bar{display:flex;align-items:center;justify-content:space-between;gap:10px 14px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:12px}
.lang{display:flex;border:1.5px solid var(--ink);border-radius:6px;overflow:hidden}
.lang a{padding:3px 8px;font-size:12px;font-weight:700;text-decoration:none;color:var(--ink)}
.lang a.on{background:var(--ink);color:var(--chassis)}
.mark{font-size:22px;font-weight:700;letter-spacing:-.02em}
.mark small{font-size:11px;font-weight:500;color:var(--mute);margin-left:6px;letter-spacing:0}
.leds{display:flex;gap:12px;font-size:11px;color:var(--mute)}
.leds span{display:flex;align-items:center;gap:5px}
.led{width:8px;height:8px;border-radius:50%;background:var(--rule);display:inline-block}
.led.on{background:var(--good)}.led.wait{background:var(--wait)}.led.off{background:var(--bad)}
nav{display:grid;grid-template-columns:repeat(auto-fill,minmax(92px,1fr));gap:6px;margin-top:16px}
nav a{display:flex;align-items:flex-end;height:46px;padding:6px 9px;border:1.5px solid var(--ink);
border-radius:7px;background:var(--key);color:var(--ink);text-decoration:none;font-size:13px;font-weight:500;
box-shadow:0 2px 0 var(--ink)}
nav a:active{transform:translateY(2px);box-shadow:none}
nav a.on{background:var(--ink);color:var(--chassis);transform:translateY(2px);box-shadow:none}
nav a.out{border-style:dashed;box-shadow:none;color:var(--mute);border-color:var(--mute)}
main{max-width:760px;margin:auto;padding:12px 16px 40px}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:16px;margin-bottom:12px}
h2{font-size:15px;margin:0 0 12px;font-weight:700;letter-spacing:-.01em}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
td,th{padding:8px 2px;text-align:left;border-top:1px solid var(--rule)}
tr:first-child td,tr:first-child th{border-top:0}
th{color:var(--mute);font-weight:500;font-size:13px}.r{text-align:right;white-space:nowrap}
@media (max-width:480px){td,th{font-size:14px;padding:7px 2px}}
.ok{color:var(--ink)}.bad{color:var(--bad)}.dim{color:var(--mute)}
/* Statuspunkte wie auf dem Display: gruen = ok, rot = Problem, blau = wartet */
td.ok::before,td.bad::before,td.dim::before{content:"";display:inline-block;width:9px;height:9px;
border-radius:50%;margin-right:8px;vertical-align:0;background:var(--good)}
td.bad::before{background:var(--bad)}td.dim::before{background:var(--wait)}
a{color:var(--ink);text-decoration-color:var(--signal);text-decoration-thickness:2px;text-underline-offset:3px}
label{font-size:13px}
input,select,button{font:inherit;font-size:15px;padding:10px 12px;border-radius:6px;border:1.5px solid var(--rule);
background:var(--key);color:var(--ink);width:100%;margin:5px 0 12px}
input:focus,select:focus,button:focus-visible,nav a:focus-visible{outline:2px solid var(--signal);outline-offset:2px}
button{background:var(--signal);color:#000;border:1.5px solid var(--signal);font-weight:700;cursor:pointer;
box-shadow:0 2px 0 #b8400f}
button:active{transform:translateY(2px);box-shadow:none}
button.sec{background:var(--key);color:var(--ink);border-color:var(--ink);box-shadow:0 2px 0 var(--ink)}
button.danger{background:var(--ink);color:var(--chassis);border-color:var(--ink);box-shadow:0 2px 0 var(--mute)}
pre{white-space:pre-wrap;font-size:12px;color:var(--mute);margin:0}
.msg{padding:12px 14px;border-radius:8px;border:1.5px solid var(--ink);background:var(--panel);margin-bottom:12px}
.msg.err{border-color:var(--signal)}
.row{display:flex;gap:8px}.row>*{flex:1}
/* Haekchen und Farbwahl als Tasten im Stil der Box */
.checks label{display:flex;gap:12px;align-items:center;margin:10px 0;font-size:15px;cursor:pointer}
input[type=checkbox]{-webkit-appearance:none;appearance:none;flex:none;width:22px;height:22px;margin:0;padding:0;
border:1.5px solid var(--ink);border-radius:5px;background:var(--key);box-shadow:0 2px 0 var(--ink);
display:inline-grid;place-content:center;cursor:pointer}
input[type=checkbox]:checked{background:var(--ink)}
input[type=checkbox]:checked::after{content:"";width:10px;height:5px;border:2.5px solid var(--chassis);
border-top:0;border-right:0;transform:translate(0,-1px) rotate(-45deg)}
input[type=checkbox]:disabled{opacity:.45;cursor:default}
input[type=color]{-webkit-appearance:none;appearance:none;height:48px;padding:5px;border:1.5px solid var(--ink);
border-radius:7px;background:var(--key);box-shadow:0 2px 0 var(--ink);cursor:pointer}
input[type=color]::-webkit-color-swatch-wrapper{padding:0}
input[type=color]::-webkit-color-swatch{border:0;border-radius:4px}input[type=color]::-moz-color-swatch{border:0;border-radius:4px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}nav a,button{transform:none!important}}
"""


def lang_switch(cur, href):
    """Umschalter DE | EN. href(code) liefert den Link fuer die jeweilige Sprache."""
    return '<span class="lang">' + "".join(
        f'<a href="{e(href(k))}" class="{"on" if k == cur else ""}" lang="{k}">{k.upper()}</a>'
        for k in LANGS) + "</span>"


def login_page(msg="", nxt="/", lang=None):
    """Anmeldeseite mit eigenem Sprachumschalter; die Wahl gilt nach erfolgreicher Anmeldung fuer die Box."""
    lang = lang if lang in LANGS else get_lang()
    m = f'<div class="msg err">{e(msg)}</div>' if msg else ""
    sw = lang_switch(lang, lambda k: f"/login?l={k}&next={quote(nxt)}")
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Rowbox – {L("Anmelden", lang)}</title>
<style>{CSS}</style></head><body><header><div class="bar"><span class="mark">rowbox</span>{sw}</div></header><main>{m}
<div class="card"><h2>{L("Anmelden", lang)}</h2><form method="post" action="/login">
<input type="hidden" name="next" value="{e(nxt)}"><input type="hidden" name="l" value="{lang}">
<label class="dim">{L("Passwort", lang)}</label>
<input name="password" type="password" autocomplete="current-password" autofocus>
<button>{L("Anmelden", lang)}</button></form>
<p class="dim">{pw_hint(lang)}</p></div></main></body></html>"""


def pw_hint(lang=None):
    """Wo das Passwort steht: auf dem Display oder, ohne Display, in der Einstellungsdatei."""
    if load_conf().get("display", True):
        return L("Das Passwort steht auf dem Display der Box unter „Info“.", lang)
    return L("Das Passwort hat setup.sh am Ende angezeigt. Nachsehen per SSH: {cmd}", lang,
             cmd="sudo grep password /etc/rowbox.json")


def page(title, active, body, msg=""):
    tabs = [("/", L("Status")), ("/live", L("Live")), ("/anzeige", L("Anzeige")), ("/logbuch", L("Logbuch"))]
    if read_json(CONF, {}).get("game", True):
        tabs.append(("/reise", L("Reise")))
    tabs += [("/wlan", L("WLAN")), ("/protokoll", L("Protokoll")), ("/einstellungen", L("Einstellungen"))]
    nav = "".join(f'<a href="{u}" class="{"on" if u == active else ""}">{t}</a>' for u, t in tabs)
    nav += f'<a href="/logout" class="out">{L("Abmelden")}</a>'
    m = f'<div class="msg">{e(msg)}</div>' if msg else ""
    st = read_json(STATE, {})
    fresh = time.time() - st.get("ts", 0) < 5
    wl, wcon = wlan_connection()
    led = lambda name, state: f'<span><i class="led {state}"></i>{name}</span>'
    leds = (led(L("Brücke").lower(), "on" if fresh else "off")
            + led("s4", "on" if fresh and st.get("s4") else "off")
            + led("app", "on" if fresh and st.get("app") else "wait")
            + led(L("WLAN").lower(), "wait" if wcon == HOTSPOT_CON else "on" if wl == "connected" else "off"))
    ver = e(st.get("version", ""))
    return f"""<!doctype html><html lang="{get_lang()}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Rowbox – {e(title)}</title>
<style>{CSS}</style></head><body><header><div class="bar"><span class="brand"><span class="mark">rowbox<small>{ver}</small></span>
{lang_switch(get_lang(), lambda k: f"/sprache?l={k}&next={quote(active or '/')}")}</span>
<span class="leds">{leds}</span></div><nav>{nav}</nav></header>
<main>{m}{body}</main></body></html>"""


def status_page(wifi):
    s = read_json(STATE, {})
    fresh = time.time() - s.get("ts", 0) < 5
    st, con = wlan_connection()
    b = battery()
    if con:
        _, ssid, _ = nm("-g", "802-11-wireless.ssid", "connection", "show", con)
        con = ssid or con
    def line(k, ok, v):
        cls = "ok" if ok is True else ("dim" if ok is None else "bad")
        return f'<tr><td>{k}</td><td class="r {cls}">{e(v)}</td></tr>'
    rows = (line(L("Brücke"), fresh, L("läuft") if fresh else L("aus"))
            + line("S4", bool(s.get("s4")), L("verbunden") if s.get("s4") else L("nicht verbunden"))
            + (f'<tr><td>{L("S4-Firmware")}</td><td class="r">{e(s["s4_fw"].split(" ")[-1])}</td></tr>'
               if s.get("s4") and s.get("s4_fw") else "")
            + line("App", True if s.get("app") else None, L("verbunden") if s.get("app") else L("wartet"))
            + line(L("WLAN"), st == "connected", con if con else L("keins"))
            + line(L("Akku"), b is not None and b > 15, f"{b} %" if b is not None else L("unbekannt")))
    live = ""
    if s.get("active"):
        live = (f'<div class="card"><h2>{L("Training läuft")}</h2><table>'
                f'<tr><td>{L("Distanz")}</td><td class="r">{num(s.get("dist", 0))} m</td></tr>'
                f'<tr><td>{L("Zeit")}</td><td class="r">{mmss(s.get("elapsed"))}</td></tr>'
                f'<tr><td>{L("Schlagzahl")}</td><td class="r">{s.get("spm", 0)}/min</td></tr>'
                f'<tr><td>{L("Leistung")}</td><td class="r">{s.get("watts", 0)} W</td></tr></table>'
                f'<p><a href="/live">{L("Große Anzeige öffnen")}</a></p></div>')
    js = lambda t: json.dumps(t).replace('"', "&quot;")
    actions = f"""<div class="card"><h2>{L("Gerät")}</h2><form method="post" action="/aktion" class="row">
<button class="sec" name="do" value="restart" onclick="return confirm({js(L("Box neu starten?"))})">{L("Neu starten")}</button>
<button class="danger" name="do" value="poweroff" onclick="return confirm({js(L("Box ausschalten? Wieder an: Taste am Akku, oder Strom neu einstecken."))})">{L("Ausschalten")}</button></form></div>"""
    return page(L("Status"), "/", f'<div class="card"><h2>{L("Zustand")}</h2><table>{rows}</table></div>'
                f"{live}{actions}", wifi.message if wifi.mode != "client" else "")


LIVE_JS = """
const f=(id,v)=>document.getElementById(id).textContent=v;
const mmss=s=>{s=Math.round(s||0);const h=Math.floor(s/3600),m=Math.floor(s/60)%60,x=String(s%60).padStart(2,'0');
 return h?h+':'+String(m).padStart(2,'0')+':'+x:m+':'+x};
async function tick(){
 try{const r=await fetch('/live.json',{cache:'no-store'});
  if(r.status!==200){location.href='/login?next=/live';return}
  const s=await r.json();
  f('dist',(s.dist||0).toLocaleString(T.locale));f('time',mmss(s.elapsed));
  f('pace',s.pace?mmss(s.pace):'–');f('spm',s.spm||'–');f('watts',s.watts||'–');
  f('cal',s.cal||0);f('bpm',s.bpm||'–');
  document.getElementById('hrbox').style.display=s.bpm?'':'none';
  const pause=!s.active&&s.pause_left!=null;
  f('state',!s.fresh?T.off:!s.s4?T.nos4:s.active?T.rowing:pause?T.pauseleft.replace('{t}',mmss(s.pause_left)):T.pause);
  document.getElementById('state').className=s.active?'ok':'dim';
  if(s.curve&&s.stroke_n!==lastN){lastN=s.stroke_n;hist.push(s.curve);if(hist.length>6)hist.shift();draw()}
  f('drive',s.drive?s.drive.toFixed(2).replace('.',T.dec):'–');
  f('recover',s.recover?s.recover.toFixed(2).replace('.',T.dec):'–');
  f('ratio',s.drive&&s.recover?'1 : '+(s.recover/s.drive).toFixed(1).replace('.',T.dec):'–');
 }catch(e){f('state',T.nobox)}
}
let lastN=null;const hist=[];
const pts=c=>c.map((v,i)=>(i*300/(c.length-1)).toFixed(1)+','+(110-v).toFixed(1)).join(' ');
function draw(){
 const g=document.getElementById('curves');
 g.innerHTML=hist.map((c,i)=>'<polyline points="'+pts(c)+'" fill="none" stroke-linejoin="round" stroke-width="'+
  (i==hist.length-1?3:1.5)+'" stroke="'+(i==hist.length-1?'#ff5a1f':'#7d7d77')+'" opacity="'+
  (i==hist.length-1?1:0.25+0.1*i)+'"/>').join('');
}
tick();setInterval(tick,1000);
"""

LIVE_CSS = """
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.tile{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:12px 14px}
.tile .k{color:var(--mute);font-size:13px}
.tile .v{font-size:clamp(44px,12vw,92px);font-weight:300;letter-spacing:-.04em;line-height:1;
font-variant-numeric:tabular-nums}
.tile .u{color:var(--mute);font-size:16px;margin-left:6px}
.big{grid-column:1/-1}.big .v{font-size:clamp(64px,20vw,140px)}
#state{font-size:15px;font-weight:600;margin:4px 0 10px}#state.ok{color:var(--good)}#state.dim{color:var(--wait)}
.curve svg{width:100%;height:auto;display:block;color:var(--ink)}
.nums{display:flex;gap:10px;margin-top:10px;border-top:1px solid var(--rule);padding-top:10px}.nums div{flex:1}
.nums b{display:block;font-size:28px;font-weight:300;font-variant-numeric:tabular-nums}
"""


def live_page():
    tile = lambda i, k, u, big=False, extra="": (
        f'<div class="tile{" big" if big else ""}"{extra}><div class="k">{k}</div>'
        f'<div><span class="v" id="{i}">–</span><span class="u">{u}</span></div></div>')
    texts = {"locale": "de-DE" if get_lang() == "de" else "en-GB", "off": L("Brücke aus"),
             "nos4": L("S4 nicht verbunden"), "rowing": L("rudert"), "pause": L("Pause"),
             "nobox": L("keine Verbindung zur Box"),
             "pauseleft": L("Pause – automatisches Ende in {t}", t="{t}"),
             "dec": "," if get_lang() == "de" else "."}
    body = (f'<a class="card" href="/anzeige" style="display:block;text-decoration:none;color:inherit">'
            f'<h2>{L("Handy als Display")}</h2><p class="dim" style="margin:0">'
            f'{L("Alle Ansichten wie auf dem Display, zum Wischen. Ideal für die Ultralight-Version ohne Display.")}</p></a>'
            f'<p id="state" class="dim">…</p><div class="grid">'
            + tile("dist", L("Distanz"), "m", True) + tile("time", L("Zeit"), "")
            + tile("pace", L("Pace"), "/500") + tile("spm", L("Schlagzahl"), "/min")
            + tile("watts", L("Leistung"), "W") + tile("cal", L("Energie"), "kcal")
            + tile("bpm", L("Puls"), "/min", extra=' id="hrbox" style="display:none"')
            + f'<div class="tile big curve"><div class="k">{L("Kraftkurve (letzter Zug orange)")}</div>'
            + '<svg viewBox="0 -4 300 118"><line x1="0" y1="110" x2="300" y2="110" stroke="currentColor" opacity=".25"/>'
              '<g id="curves"></g></svg><div class="nums">'
            + f'<div><span class="dim">{L("Zug")}</span><b id="drive">–</b></div>'
            + f'<div><span class="dim">{L("Rollphase")}</span><b id="recover">–</b></div>'
            + f'<div><span class="dim">{L("Verhältnis")}</span><b id="ratio">–</b></div></div></div>'
            + f'</div><script>const T={json.dumps(texts)};{LIVE_JS}</script>')
    return page(L("Live"), "/live", body).replace("</style>", LIVE_CSS + "</style>", 1)


def live_json():
    s = read_json(STATE, {})
    s["fresh"] = time.time() - s.get("ts", 0) < 5
    s.pop("last", None)
    return json.dumps(s)


# ---------- Handy als Display ----------

SCREEN_CSS = """
:root{--bg:#000;--fg:#f2f2ee;--dim:#8a8f98;--sig:#ff5a1f;--ok:#3ccf6e;--card:#20242c;--line:#3c424c}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-tap-highlight-color:transparent}
.top{position:fixed;top:0;left:0;right:0;display:flex;justify-content:space-between;align-items:center;
padding:10px 16px;z-index:2;font-size:14px;color:var(--dim)}
.top a{color:var(--dim);text-decoration:none}
.tr{display:flex;align-items:center;gap:14px}
.fsb{background:none;border:0;color:var(--dim);padding:2px;display:flex;cursor:pointer}.fsb svg{width:20px;height:20px;margin:0}
.tip{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:5;width:min(90vw,420px);background:var(--card);
border-radius:18px;padding:18px 20px;display:flex;flex-direction:column;gap:8px;text-align:left;box-shadow:0 10px 40px rgba(0,0,0,.6)}
.tip[hidden]{display:none}.tip b{font-size:18px}.tip span{color:var(--dim);line-height:1.4}
.tip button{align-self:flex-end;background:var(--sig);color:#000;border:0;border-radius:10px;padding:8px 18px;font-weight:700}
.dots{display:flex;gap:7px}.dots i{width:7px;height:7px;border-radius:50%;background:var(--line)}
.dots i.on{background:var(--fg)}
.deck{display:flex;height:100svh;overflow-x:auto;scroll-snap-type:x mandatory;scrollbar-width:none}
.deck::-webkit-scrollbar{display:none}
.slide{flex:0 0 100%;height:100%;scroll-snap-align:center;display:flex;flex-direction:column;
align-items:center;justify-content:center;padding:52px 20px calc(60px + env(safe-area-inset-bottom))}
.sc{width:100%;max-width:440px;text-align:center;font-variant-numeric:tabular-nums}
.t{color:var(--dim);font-weight:700;font-size:clamp(20px,6vw,28px)}
.sub{color:var(--dim);font-size:clamp(13px,3.6vw,16px);margin-top:2px}
.huge{font-weight:800;font-size:clamp(72px,26vw,140px);line-height:1;margin-top:18px}
.big{font-weight:800;font-size:clamp(36px,12vw,64px);line-height:1.05}
.row3{display:flex;justify-content:space-around;margin-top:26px}
.row3 b{display:block;font-size:clamp(26px,8vw,44px)}.row3 span,.lab{color:var(--dim);font-size:clamp(13px,3.6vw,16px)}
.warn{color:var(--sig)}
svg,canvas{width:100%;display:block;margin-top:18px}
.navc{background:var(--card);border-radius:16px;padding:16px 18px;text-align:left;margin-top:14px}
.navc .big{font-size:clamp(30px,10vw,52px)}
/* Reise: drei Live-Werte in einer Karte, so gross wie die Etappenkarte */
.live3{display:grid;grid-template-columns:repeat(3,1fr);padding:14px 4px;text-align:center}
.live3>div+div{border-left:1px solid var(--line)}
.live3 b{display:block;font-weight:800;font-size:clamp(26px,min(8.5vw,5.2vh),46px);line-height:1.1;font-variant-numeric:tabular-nums}
.live3 span{color:var(--dim);font-size:clamp(12px,3.3vw,15px)}
#journey .navc{margin-top:10px}#journey .goal{margin-top:8px}#journey .goal .r2{font-size:clamp(18px,5.5vw,26px)}
#journey .goal .lab{font-weight:400}
.r2{display:flex;justify-content:space-between;align-items:baseline;font-weight:800;font-size:clamp(22px,7vw,34px)}
.goal{text-align:left;margin-top:16px;padding:0 4px}
.goal b{font-size:clamp(22px,7vw,34px)}
.st{list-style:none;padding:0;margin:26px 0 0;text-align:left}
.st li{display:flex;align-items:center;gap:14px;padding:11px 0;font-size:clamp(20px,6.5vw,30px);font-weight:700}
.st li span{margin-left:auto;color:var(--dim);font-weight:400;font-size:clamp(14px,4.4vw,20px)}
.st i{width:16px;height:16px;border-radius:50%;background:var(--line);flex:none}
.st i.ok{background:var(--ok)}.st i.bad{background:#e5484d}.st i.wait{background:#3d5afe}
.sc.full{height:100%;display:flex;flex-direction:column;max-width:520px}
.grow{flex:1;min-height:0;position:relative;margin-top:12px}
.grow>svg,.grow>canvas{position:absolute;inset:0;width:100%;height:100%;margin:0}
.over{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--dim);
padding:24px;font-size:clamp(15px,2.2vh,20px);line-height:1.4}
.bar3{display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid var(--line);padding:14px 0 4px}
.bar3>div+div{border-left:1px solid var(--line)}
.bar3 b{display:block;font-size:clamp(28px,min(10vw,6vh),58px);font-weight:800}
.bar3 span{color:var(--dim);font-size:clamp(12px,1.9vh,17px)}
.foot{color:var(--dim);font-size:clamp(14px,2.2vh,19px);padding:12px 0 4px}
.route{position:relative;z-index:0;list-style:none;margin:0;padding:0 0 0 6px;height:100%;display:flex;flex-direction:column;justify-content:space-evenly;text-align:left;overflow:hidden}
.route li{position:relative;padding-left:30px;display:flex;justify-content:space-between;align-items:baseline;
font-size:clamp(17px,2.8vh,26px);font-weight:700}
.route li::before{content:"";position:absolute;left:0;top:50%;width:14px;height:14px;margin-top:-7px;border-radius:50%;background:var(--line)}
.route li::after{content:"";position:absolute;left:6px;top:50%;height:calc(100% + 40px);width:2px;background:var(--line);z-index:-1}
.route li:last-child::after{display:none}
.route li span{color:var(--dim);font-weight:400;font-size:.8em}
.route li.past{color:var(--dim)}.route li.past::before{background:var(--sig)}
.route li.here{color:var(--sig)}.route li.here::before{background:var(--fg);box-shadow:0 0 0 4px var(--sig)}
.verdict{padding:6px 0 18px}
.verdict .big{font-size:clamp(34px,min(11vw,6.5vh),64px);line-height:1.05}
.verdict .ok{color:var(--ok)}.verdict .bad{color:#ff6b6b}.verdict .wait{color:#7d8cff}
.verdict .sub{margin-top:8px}
.cards{flex:1;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:12px;min-height:0}
.cards>div{background:var(--card);border-radius:20px;padding:16px;display:flex;flex-direction:column;
justify-content:space-between;text-align:left;border:2px solid transparent;min-height:0}
.cards>div.ok{border-color:rgba(60,207,110,.55)}.cards>div.bad{border-color:rgba(255,107,107,.6)}
.cards>div.wait{border-color:rgba(125,140,255,.6)}
.cards i{width:clamp(22px,4.5vh,40px);height:clamp(22px,4.5vh,40px);border-radius:50%;background:var(--line)}
.cards .ok i{background:var(--ok);box-shadow:0 0 22px rgba(60,207,110,.55)}
.cards .bad i{background:#ff6b6b;box-shadow:0 0 22px rgba(255,107,107,.5)}
.cards .wait i{background:#7d8cff;box-shadow:0 0 22px rgba(125,140,255,.5)}
.cards b{font-size:clamp(26px,min(9vw,5.2vh),48px);font-weight:900;letter-spacing:-.01em}
.cards span{color:var(--dim);font-size:clamp(14px,2.1vh,19px);line-height:1.25}
#journey .grow{background:radial-gradient(ellipse at 20% 50%,rgba(47,111,218,.16),transparent 70%);border-radius:18px}
.jhead{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.jhead .big{font-size:clamp(30px,min(10vw,6vh),56px);text-align:left}
.jhead span{color:var(--dim);font-size:clamp(13px,2vh,17px);white-space:nowrap}
.prog{height:8px;border-radius:4px;background:var(--line);overflow:hidden;margin:8px 0 2px}
.prog i{display:block;height:100%;background:linear-gradient(90deg,#2f6fda,var(--sig))}
#jr-svg{position:absolute;left:0;top:0;width:64px;height:100%;overflow:visible}
.scene{height:30vh;margin:10px -20px 0;position:relative;overflow:hidden;background:#0d1a2b}
.scene>canvas:first-child{width:100%!important;height:100%!important;display:block;margin:0}
.scene:empty{display:none}

#journey .grow{padding:6px 0}
#journey .route{padding-left:0}#journey .route li{padding-left:70px}
#journey .route li::before,#journey .route li::after{display:none}
.hero{flex:1;display:flex;flex-direction:column;justify-content:center;min-height:0}
.hero .huge{font-size:clamp(84px,min(32vw,20vh),210px);margin:0}
.hero .sub{font-size:clamp(15px,2.4vh,22px)}
.tiles{display:grid;grid-template-columns:1fr 1fr;flex:1.15;min-height:0;border-top:1px solid var(--line)}
.tiles>div{display:flex;flex-direction:column;justify-content:center;border-bottom:1px solid var(--line)}
.tiles>div:nth-child(odd){border-right:1px solid var(--line)}
.tiles b{font-size:clamp(38px,min(14vw,8.5vh),84px);font-weight:800;line-height:1.05}
.tiles span{color:var(--dim);font-size:clamp(13px,2vh,18px);margin-top:4px}
.pausebox{flex:1.15;display:flex;flex-direction:column;justify-content:center;border-top:1px solid var(--line)}
.pausebox .big{font-size:clamp(56px,min(20vw,12vh),120px)}
.swipe{position:fixed;bottom:0;left:0;right:0;display:flex;align-items:center;justify-content:center;gap:10px;
padding:10px 12px calc(10px + env(safe-area-inset-bottom));color:var(--dim);font-size:clamp(13px,1.9vh,16px);
background:linear-gradient(transparent,#000 35%);z-index:2}
.swipe button{background:none;border:0;color:var(--fg);padding:6px 12px;cursor:pointer;display:flex}
.swipe button:disabled{opacity:.2}
.swipe svg{width:22px;height:22px;margin:0}
.live3 .land{display:none}

/* ---------- Querformat: eigene Layouts statt der gestauchten Hochkant-Ansicht ---------- */
@media (orientation:landscape){
.slide{padding:44px max(64px,env(safe-area-inset-right)) 16px max(64px,env(safe-area-inset-left))}
.sc,.sc.full{max-width:1400px}
/* Blaettern: zwei runde Pfeile an den Raendern statt der Leiste unten */
.swipe{top:50%;bottom:auto;transform:translateY(-50%);justify-content:space-between;background:none;padding:0 6px;pointer-events:none}
.swipe span{display:none}
.swipe button{pointer-events:auto;background:rgba(20,24,30,.55);border-radius:50%;width:44px;height:44px;padding:0;align-items:center;justify-content:center}
/* Training: Strecke links gross, vier Werte rechts */
#training .sc{display:grid;grid-template-columns:1.1fr 1fr;grid-template-rows:auto 1fr;column-gap:3vw}
#training .t{grid-column:1/-1}
#training .hero .huge{font-size:clamp(72px,min(15vw,34vh),260px)}
#training .tiles,#training .pausebox{border-top:0;border-left:1px solid var(--line)}
#training .tiles b{font-size:clamp(34px,min(6.5vw,13vh),110px)}
/* Kraftkurve: Kurve breit, Werte rechts untereinander */
#force .sc{display:grid;grid-template-columns:1fr minmax(160px,22vw);grid-template-rows:auto 1fr;column-gap:3vw}
#force .t{grid-column:1/-1}#force .grow{margin-top:8px}
#force .bar3{grid-template-columns:1fr;border-top:0;border-left:1px solid var(--line);padding:0 0 0 2vw;align-content:center}
#force .bar3>div+div{border-left:0;border-top:1px solid var(--line)}#force .bar3>div{padding:2vh 0}
#force .bar3 b{font-size:clamp(28px,min(5vw,9vh),72px)}
/* Taktgeber: Sitz links, Text rechts */
#pacer .sc{display:grid;grid-template-columns:minmax(120px,34vw) 1fr;grid-template-rows:auto 1fr;column-gap:4vw}
#pacer .t{grid-column:1/-1}#pacer .foot{align-self:center;text-align:left;font-size:clamp(18px,3.4vh,32px)}
/* Status: Urteil links, Karten rechts */
#status .sc{display:grid;grid-template-columns:1fr 1.25fr;grid-template-rows:auto 1fr;column-gap:3vw}
#status .t{grid-column:1/-1}#status .verdict{align-self:center;text-align:left}
#status .verdict .big{font-size:clamp(34px,min(6vw,12vh),96px)}
/* Reise als Cockpit: 3D-Fluss ueber den ganzen Bildschirm, Werte als Glas-Karten darueber */
#journey{padding:0}
#journey .sc.full{position:relative;max-width:none;height:100%;--lb:clamp(66px,17vh,120px);--m:max(12px,env(safe-area-inset-left))}
#journey .scene{position:absolute;inset:0;height:auto;margin:0}
#journey .jtop,#journey .navc,#journey .goal{position:absolute;z-index:1;
background:rgba(12,18,28,.62);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}
#journey .jtop{top:40px;left:50%;transform:translateX(-50%);width:min(44vw,600px);border-radius:14px;padding:8px 16px 10px}
#journey .jhead .big{font-size:clamp(20px,min(3.4vw,6vh),40px)}
#journey .prog{margin:6px 0 0;height:6px}
#journey .live3{left:50%;transform:translateX(-50%);bottom:var(--m);width:min(calc(100% - 2*var(--m)),1100px);
height:var(--lb);margin:0;grid-template-columns:repeat(5,1fr);align-items:center;padding:6px}
#journey .live3 .land{display:block}
#journey .live3 b{font-size:clamp(22px,min(4vw,8.5vh),60px)}
#journey .live3 span{font-size:clamp(11px,min(1.6vw,2.6vh),17px)}
#journey #jr-stage{right:calc(var(--m) + 52px);bottom:calc(var(--lb) + var(--m) + 10px + clamp(44px,9vh,64px));width:min(34vw,400px);
margin:0;padding:10px 16px;border-radius:14px 14px 0 0}
#journey #jr-stage .big{font-size:clamp(22px,min(3.6vw,7vh),48px)}
#journey #jr-stage .r2{font-size:clamp(16px,min(2.4vw,4.6vh),30px)}
#journey .goal{right:calc(var(--m) + 52px);bottom:calc(var(--lb) + var(--m) + 10px);width:min(34vw,400px);height:clamp(44px,9vh,64px);
margin:0;padding:6px 16px;border-radius:0 0 14px 14px;border-top:1px solid var(--line);display:flex;flex-direction:column;justify-content:center}
#journey .goal .r2{font-size:clamp(14px,min(2vw,4vh),24px)}#journey .goal b{font-size:inherit}
#journey #jr-geta{display:none}
#journey .grow{display:none!important}
#journey .r3-wx{top:44px!important;left:var(--m)!important}
#journey .r3-map{top:44px!important;right:var(--m)!important;width:clamp(84px,24vh,190px)!important;height:clamp(84px,24vh,190px)!important}
#journey .r3-credit{left:var(--m)!important;bottom:calc(var(--lb) + var(--m) + 6px)!important}
.top{text-shadow:0 1px 3px rgba(0,0,0,.6)}
}
/* Flaches Querformat (Handy mit Browserleisten): kompaktes Cockpit, Szene bleibt frei */
@media (orientation:landscape) and (max-height:520px){
.top{padding:6px 14px}
#journey .sc.full{--lb:clamp(52px,19vh,72px);--m:max(8px,env(safe-area-inset-left))}
#journey .jtop{top:30px;width:min(34vw,380px);padding:5px 12px 7px;border-radius:10px}
#journey .jhead .big{font-size:clamp(16px,5vh,22px)}#journey .jhead span{font-size:12px}#journey .prog{height:4px;margin-top:4px}
#journey .live3 b{font-size:clamp(18px,8vh,30px)}#journey .live3 span{font-size:11px}
#journey #jr-stage{width:min(30vw,300px);padding:6px 12px;bottom:calc(var(--lb) + var(--m) + 6px + 30px)}
#journey #jr-stage .lab{display:none}
#journey #jr-stage .big{font-size:clamp(16px,6vh,26px)}#journey #jr-stage .r2{font-size:clamp(13px,4.4vh,18px)}
#journey .goal{width:min(30vw,300px);height:30px;padding:2px 12px;bottom:calc(var(--lb) + var(--m) + 6px)}
#journey .goal .r2{font-size:13px}
#journey .r3-wx{top:30px!important}
#journey .r3-map{top:30px!important;width:clamp(60px,22vh,90px)!important;height:clamp(60px,22vh,90px)!important}
.swipe button{width:36px;height:36px}
}
"""

SCREEN_JS = r"""
const $=id=>document.getElementById(id);
const mmss=s=>{s=Math.round(s||0);const h=Math.floor(s/3600),m=Math.floor(s/60)%60,x=String(s%60).padStart(2,'0');
 return h?h+':'+String(m).padStart(2,'0')+':'+x:m+':'+x};
const num=(v,d)=>v==null?'–':Number(v).toFixed(d).replace('.',T.dec);
const deck=$('deck'),slides=[...deck.children],dots=$('dots');
slides.forEach(()=>dots.appendChild(document.createElement('i')));
let cur=0;
function current(){return Math.round(deck.scrollLeft/deck.clientWidth)}
let mark=function(){cur=current();[...dots.children].forEach((d,i)=>d.className=i==cur?'on':'');
 try{localStorage.setItem('rowbox-screen',slides[cur].id)}catch(e){}};
deck.addEventListener('scroll',()=>{clearTimeout(mark.t);mark.t=setTimeout(mark,80)});
document.addEventListener('keydown',e=>{const d=e.key=='ArrowRight'?1:e.key=='ArrowLeft'?-1:0;
 if(d)deck.scrollTo({left:(current()+d)*deck.clientWidth,behavior:'smooth'})});
try{const id=localStorage.getItem('rowbox-screen');const i=slides.findIndex(x=>x.id==id);
 if(i>0)requestAnimationFrame(()=>{deck.scrollLeft=i*deck.clientWidth;mark()})}catch(e){}
const go=d=>deck.scrollTo({left:(current()+d)*deck.clientWidth,behavior:'smooth'});
$('prev').onclick=()=>go(-1);$('next').onclick=()=>go(1);
const arrows=()=>{$('prev').disabled=cur==0;$('next').disabled=cur==slides.length-1};
const _mark=mark;mark=function(){_mark();arrows();if(typeof scene3d=='function')scene3d()};
mark();
// --- 1 Hz: Trainingswerte, Kraftkurve, Status ---
let S={},lastN=null;const hist=[];
function fit(cv){const r=cv.getBoundingClientRect(),d=window.devicePixelRatio||1,w=Math.round(r.width*d),h=Math.round(r.height*d);
 if(w&&h&&(cv.width!=w||cv.height!=h)){cv.width=w;cv.height=h}return d}
async function live(){
 try{const r=await fetch('/live.json',{cache:'no-store'});
  if(r.status==401){location.href='/login?next=/anzeige';return}
  S=await r.json()}catch(e){S={}}
 const pause=!S.active&&S.pause_left!=null;
 $('tr-t').textContent=S.active||!S.session?T.training:T.pause;
 const dt=(S.dist||0).toLocaleString(T.locale),de=$('tr-dist');de.textContent=dt;
 de.style.fontSize=dt.length>4?'min('+(128/dt.length).toFixed(1)+'vw,'+(80/dt.length).toFixed(1)+'vh,210px)':'';
 const tm=mmss(S.elapsed);$('tr-time').textContent=tm;$('tr-time').style.fontSize=tm.length>5?'min('+(70/tm.length).toFixed(1)+'vw,'+(42/tm.length).toFixed(1)+'vh,84px)':'';$('tr-spm').textContent=S.spm||0;$('tr-w').textContent=S.watts||0;
 $('tr-pace').textContent=S.pace?mmss(S.pace):'–';
 if($('jl-pace')){$('jl-pace').textContent=S.active&&S.pace?mmss(S.pace):'–';$('jl-spm').textContent=S.active?S.spm||0:0;$('jl-w').textContent=S.active?S.watts||0:0;
  $('jl-dist').textContent=(S.dist||0).toLocaleString(T.locale);$('jl-time').textContent=mmss(S.elapsed)}
 $('tr-pause').style.display=pause?'':'none';$('tr-row').style.display=pause?'none':'';
 if(pause)$('tr-left').textContent=mmss(S.pause_left);
 if(S.curve&&S.stroke_n!==lastN){lastN=S.stroke_n;hist.push(S.curve);if(hist.length>5)hist.shift();
  $('fc-g').innerHTML=hist.map((c,i)=>{const top=i==hist.length-1;return '<polyline fill="none" stroke-linejoin="round" vector-effect="non-scaling-stroke" points="'+
   c.map((v,k)=>(k*300/(c.length-1)).toFixed(1)+','+(118-v*1.15).toFixed(1)).join(' ')+'" stroke="'+(top?'#ff5a1f':'#8a8f98')+
   '" stroke-width="'+(top?5:2)+'" opacity="'+(top?1:0.15+0.08*i)+'"/>'}).join('');
  $('fc-none').style.display='none'}
 $('fc-d').textContent=num(S.drive,2);$('fc-r').textContent=num(S.recover,2);
 $('fc-q').textContent=S.drive&&S.recover?'1:'+num(S.recover/S.drive,1):'–';
 const st=(id,cls,txt)=>{$(id).className=cls;$(id+'t').textContent=txt};
 const fresh=!!S.fresh;
 st('s-br',fresh?'ok':'bad',fresh?T.runs:T.off);
 st('s-s4',fresh&&S.s4?'ok':'bad',fresh&&S.s4?T.conn+(S.s4_fw?' · '+T.fw+' '+S.s4_fw.split(' ').pop():''):T.cable);
 st('s-app',fresh&&S.app?'ok':'wait',fresh&&S.app?T.conn:T.waits);
 const v=!fresh?['bad',T.v_off,T.h_off]:!S.s4?['bad',T.v_s4,T.h_s4]:!S.app?['wait',T.v_app,T.h_app]:['ok',T.v_ok,''];
 $('s-v').className='big '+v[0];$('s-v').textContent=v[1];$('s-vh').textContent=v[2];
}
// --- alle 10 s: Reise und Akku ---
async function slow(){
 try{const r=await fetch('/anzeige.json',{cache:'no-store'});if(r.status!=200)return;const j=await r.json();
  $('s-bat').className=j.battery==null?'':j.battery>15?'ok':'bad';
  $('s-batt').textContent=j.battery==null?'–':j.battery+' %';
  const g=j.journey;if(!g){$('journey').remove();return}
  $('jr-river').textContent=g.river;$('jr-next').textContent=g.done?g.goal:g.next;
  $('jr-nkm').textContent=g.done?T.done:g.next_left;$('jr-neta').textContent=g.done?'':g.eta_next;
  $('jr-goal').textContent=g.goal;$('jr-gkm').textContent=g.left+' km';$('jr-geta').textContent=g.eta_goal=='–'?'':g.eta_goal;
  const kmf=d=>d.toLocaleString(T.locale,{maximumFractionDigits:d<10?1:0});
  const past=g.stops.filter(s=>s[0]<=g.km),ahead=g.stops.filter(s=>s[0]>g.km);
  const esc=t=>String(t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  // mit 3D-Ansicht knapp: die naechste Etappe steht schon in der Karte, also nur die zwei danach
  let li=R3?[]:past.slice(-1).map(s=>'<li class="past">'+esc(s[1])+'<span>'+kmf(g.km-s[0])+' km</span></li>');
  if(!g.done)li.push('<li class="here">'+esc(T.here)+'<span></span></li>');
  li=li.concat(ahead.slice(R3?1:0,R3?3:5).map(s=>'<li>'+esc(s[1])+'<span>'+kmf(s[0]-g.km)+' km</span></li>'));
  $('jr-route').innerHTML=li.join('');
  // Elemente nach den Einstellungen ein- und ausblenden (Portal → Einstellungen → Reise)
  const show=g.show||[];JSHOW=show;
  for(const[k,id]of[['live','jr-live'],['stage','jr-stage'],['goal','jr-goalbox']]){const el=$(id);if(el)el.style.display=show.includes(k)?'':'none'}
  const sc=$('jr-3d');if(sc)sc.style.display=show.includes('scene')?'':'none';
  $('jr-grow').style.display=show.includes('list')?'':'none';
  if(show.includes('list'))fitRoute();if(typeof scene3d=='function')scene3d();
  $('jr-bar').style.width=Math.min(100,g.km/g.total*100).toFixed(1)+'%';
  $('jr-prog').textContent=kmf(g.km)+' / '+kmf(g.total)+' km';
  G=g;river()
 }catch(e){}
}
// Liste so kuerzen, dass sie nicht ueber das Ziel laeuft (kleine Bildschirme, Browserleisten)
function fitRoute(){const ul=$('jr-route');if(!ul)return;const gr=ul.parentNode;gr.style.visibility='';
 while(ul.children.length&&ul.scrollHeight>ul.clientHeight+2)ul.lastElementChild.remove();
 if(!ul.children.length||ul.clientHeight<40)gr.style.visibility='hidden'}   // zu eng: Liste ganz weglassen
// Fluss als geschwungene Linie, Orte als Bojen, das Boot bei "Du bist hier"
let G=null;
function river(){
 const svg=$('jr-svg'),ul=$('jr-route');if(!svg||!G)return;
 const h=ul.clientHeight,top=ul.getBoundingClientRect().top,X=y=>30+9*Math.sin(y/38);
 let d='';for(let y=0;y<=h;y+=4)d+=(d?'L':'M')+X(y).toFixed(1)+','+y;
 const items=[...ul.children].map(li=>{const r=li.getBoundingClientRect();return{y:r.top-top+r.height/2,cls:li.className}});
 const here=items.find(i=>i.cls=='here');
 let out='<path d="'+d+'" fill="none" stroke="#2f6fda" stroke-width="16" stroke-linecap="round" opacity=".35"/>'+
  '<path d="'+d+'" fill="none" stroke="#5b9bff" stroke-width="3" stroke-dasharray="2 9" stroke-linecap="round" opacity=".7"/>';
 if(here){let p='';for(let y=0;y<=here.y;y+=4)p+=(p?'L':'M')+X(y).toFixed(1)+','+y;
  out+='<path d="'+p+'" fill="none" stroke="#ff5a1f" stroke-width="5" stroke-linecap="round"/>'}
 for(const it of items){const x=X(it.y);
  if(it.cls=='here')out+='<g transform="translate('+x.toFixed(1)+','+it.y.toFixed(1)+')"><circle r="17" fill="#ff5a1f" opacity=".25"/>'+
   '<path d="M0,-19 C3.2,-10 3.2,10 0,19 C-3.2,10 -3.2,-10 0,-19 Z" fill="#f2f2ee"/>'+
   '<path d="M-2.5,-1 L-19,-6 M2.5,-1 L19,-6" stroke="#f2f2ee" stroke-width="1.8" stroke-linecap="round"/>'+
   '<ellipse cx="-20" cy="-6.4" rx="3.6" ry="1.9" fill="#ff5a1f" transform="rotate(17 -20 -6.4)"/>'+
   '<ellipse cx="20" cy="-6.4" rx="3.6" ry="1.9" fill="#ff5a1f" transform="rotate(-17 20 -6.4)"/>'+
   '<circle r="2.8" cy="2" fill="#ff5a1f"/></g>';
  else out+='<circle cx="'+x.toFixed(1)+'" cy="'+it.y.toFixed(1)+'" r="7" fill="'+(it.cls=='past'?'#ff5a1f':'#000')+
   '" stroke="'+(it.cls=='past'?'#ff5a1f':'#f2f2ee')+'" stroke-width="2.5"/>'}
 svg.innerHTML=out}
addEventListener('resize',river);
window.rowboxState=()=>({S,W,G});
// Bildschirm beim Rudern anlassen: Wake Lock, wo der Browser es erlaubt (nur HTTPS), sonst ein
// stummes, unsichtbares Mini-Video in Schleife (so bleibt auch Safari auf dem iPhone/iPad wach).
const NOSLEEP='data:video/mp4;base64,AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAMrbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAB9AAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAlV0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAB9AAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAfQAAAAAAABAAAAAAHNbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAABAAAAAgABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABeG1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAThzdGJsAAAAuHN0c2QAAAAAAAAAAQAAAKhhdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFUxhdmM2MC4zMS4xMDIgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAALmF2Y0MBQsAK/+EAFmdCwArZHsBEAAADAAQAAAMACDxImSABAAVoy4PEyAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAAApQAAAKUAAAABhzdHRzAAAAAAAAAAEAAAACAABAAAAAABRzdHNzAAAAAAAAAAEAAAABAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAACAAAAAQAAABxzdHN6AAAAAAAAAAAAAAACAAACiQAAAAsAAAAUc3RjbwAAAAAAAAABAAADWwAAAGJ1ZHRhAAAAWm1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAG1kaXJhcHBsAAAAAAAAAAAAAAAALWlsc3QAAAAlqXRvbwAAAB1kYXRhAAAAAQAAAABMYXZmNjAuMTYuMTAwAAAACGZyZWUAAAKcbWRhdAAAAnIGBf//btxF6b3m2Ui3lizYINkj7u94MjY0IC0gY29yZSAxNjQgcjMxMDggMzFlMTlmOSAtIEguMjY0L01QRUctNCBBVkMgY29kZWMgLSBDb3B5bGVmdCAyMDAzLTIwMjMgLSBodHRwOi8vd3d3LnZpZGVvbGFuLm9yZy94MjY0Lmh0bWwgLSBvcHRpb25zOiBjYWJhYz0wIHJlZj0zIGRlYmxvY2s9MTotMzotMyBhbmFseXNlPTB4MToweDExMSBtZT1oZXggc3VibWU9NyBwc3k9MSBwc3lfcmQ9Mi4wMDowLjcwIG1peGVkX3JlZj0xIG1lX3JhbmdlPTE2IGNocm9tYV9tZT0xIHRyZWxsaXM9MSA4eDhkY3Q9MCBjcW09MCBkZWFkem9uZT0yMSwxMSBmYXN0X3Bza2lwPTEgY2hyb21hX3FwX29mZnNldD0tNCB0aHJlYWRzPTEgbG9va2FoZWFkX3RocmVhZHM9MSBzbGljZWRfdGhyZWFkcz0wIG5yPTAgZGVjaW1hdGU9MSBpbnRlcmxhY2VkPTAgYmx1cmF5X2NvbXBhdD0wIGNvbnN0cmFpbmVkX2ludHJhPTAgYmZyYW1lcz0wIHdlaWdodHA9MCBrZXlpbnQ9MjUwIGtleWludF9taW49MSBzY2VuZWN1dD00MCBpbnRyYV9yZWZyZXNoPTAgcmNfbG9va2FoZWFkPTQwIHJjPWNyZiBtYnRyZWU9MSBjcmY9MjMuMCBxY29tcD0wLjYwIHFwbWluPTAgcXBtYXg9NjkgcXBzdGVwPTQgaXBfcmF0aW89MS40MCBhcT0xOjEuMjAAgAAAAA9liIQF85///w9FAAFXn4AAAAAHQZo4C+c6gA==';
let wakeLock=null;const nsv=document.createElement('video');
nsv.muted=true;nsv.loop=true;nsv.playsInline=true;nsv.setAttribute('playsinline','');nsv.src=NOSLEEP;
nsv.style.cssText='position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;pointer-events:none';
document.body.appendChild(nsv);
async function awake(){if(document.hidden)return;
 try{if('wakeLock' in navigator&&window.isSecureContext){wakeLock=await navigator.wakeLock.request('screen');return}}catch(e){}
 nsv.play().catch(()=>{})}
document.addEventListener('visibilitychange',awake);
for(const ev of['touchend','click'])addEventListener(ev,awake,{once:true});
awake();
// Vollbild: echte Fullscreen-API (iPad, Android, Desktop); auf dem iPhone geht das nur als Web-App vom Home-Bildschirm
(function(){const b=$('fs'),de=document.documentElement,standalone=navigator.standalone||matchMedia('(display-mode: fullscreen), (display-mode: standalone)').matches;
 const can=de.requestFullscreen||de.webkitRequestFullscreen,iphone=/iPhone|iPod/.test(navigator.userAgent);
 if(standalone||(!can&&!iphone))return;b.hidden=false;
 b.onclick=()=>{if(can){const fe=document.fullscreenElement||document.webkitFullscreenElement;
   if(fe)(document.exitFullscreen||document.webkitExitFullscreen).call(document);else{const r=can.call(de);if(r&&r.catch)r.catch(()=>{})}}
  else $('fstip').hidden=false};
 $('fsok').onclick=()=>{$('fstip').hidden=true}})();
var R3=null,R3try=false,JSHOW=null;
function scene3d(){
 const on=slides[cur]&&slides[cur].id=='journey'&&!document.hidden&&(!JSHOW||JSHOW.includes('scene'));
 if(on&&!R3try){R3try=true;import('/static/river3d.js').then(m=>{R3=m.start($('jr-3d'),window.rowboxState);
   if(!R3)$('jr-3d').remove();else{R3.run(true);if(G)slow()}}).catch(()=>{const e=$('jr-3d');if(e)e.remove()})}
 if(R3)R3.run(on);
}
document.addEventListener('visibilitychange',scene3d);
// --- 10 Hz nur fuer Taktgeber und Live-Kurve ---
let W={};
const P={phase:0,period:null,t:performance.now()/1000,last:0,started:false,warm:0,marks:[]};
function pacer(){
 const now=performance.now()/1000,dt=now-P.t;P.t=now;
 const target=T.spm?60/T.spm:(W.period||60/22);
 P.period=P.period==null?target:P.period+(target-P.period)*Math.min(1,dt*0.5);
 P.phase=(P.phase+dt/P.period)%1;
 const age=Date.now()/1000-(W.ts||0);
 for(const ago of [...(W.catch_ago||[])].sort((a,b)=>b-a)){
  const c=now-ago-age;if(c<=P.last+Math.max(1.2,0.6*P.period))continue;
  const anchor=((now-c)/P.period)%1;
  if(!P.started||c-P.last>10||P.warm<2){if(c-P.last>10)P.warm=0;P.phase=anchor;P.started=true;P.warm++}
  else{const err=((P.phase-anchor+0.5)%1+1)%1-0.5;P.marks.push(Math.abs(err)<0.12);if(P.marks.length>8)P.marks.shift();
   if(!T.spm)P.phase=Math.abs(err)>0.3?anchor:((P.phase-0.3*err)%1+1)%1}
  P.last=c}
 const cv=$('pc'),d=fit(cv),x=cv.getContext('2d'),w=cv.width,h=cv.height;x.clearRect(0,0,w,h);
 // Rollsitz senkrecht: unten = Auslage (Zuganfang), oben = Endzug
 const cx=w/2,y0=h*0.80,y1=h*0.08,r=Math.min(w*0.09,h*0.05);
 x.strokeStyle='#3c424c';x.lineWidth=8*d;x.lineCap='round';x.beginPath();x.moveTo(cx,y0);x.lineTo(cx,y1);x.stroke();
 x.lineWidth=4*d;[y0,y1].forEach(yy=>{x.beginPath();x.moveTo(cx-r*1.4,yy);x.lineTo(cx+r*1.4,yy);x.stroke()});
 const D=1/3,p=P.phase,seat=p<D?0.5-0.5*Math.cos(p/D*Math.PI):1-(0.5-0.5*Math.cos((p-D)/(1-D)*Math.PI));
 x.fillStyle=p<D?'#3ccf6e':'#3d5afe';x.beginPath();x.arc(cx,y0+seat*(y1-y0),r,0,7);x.fill();
 const mr=Math.min(w/40,h*0.018),gap=mr*3.2;
 P.marks.forEach((ok,i)=>{x.fillStyle=ok?'#3ccf6e':'#ff5a1f';x.beginPath();
  x.arc(cx+(i-(P.marks.length-1)/2)*gap,h*0.93,mr,0,7);x.fill()});
 const spm=P.period?Math.round(60/P.period):0;
 $('pc-txt').textContent=(T.spm?T.fixed:T.own).replace('{spm}',spm);
 $('pc-start').style.display=P.started?'none':'';
}
function wave(){
 const cv=$('wv'),d=fit(cv),x=cv.getContext('2d'),w=cv.width,h=cv.height;x.clearRect(0,0,w,h);
 const raw=W.w||[],sm=raw.map((_,i)=>{const a=raw.slice(Math.max(0,i-3),i+1);return a.reduce((p,q)=>p+q,0)/a.length});
 const sc=Math.max(600,Math.max(0,...sm)*1.1),age=Date.now()/1000-(W.ts||0),step=w/240;
 x.strokeStyle='#2d4632';x.lineWidth=2*d;
 for(const ago of W.catch_ago||[]){const px=w-(ago+age)/0.025*step;if(px>=0){x.beginPath();x.moveTo(px,8);x.lineTo(px,h-6);x.stroke()}}
 x.strokeStyle='#32373e';x.beginPath();x.moveTo(0,h-6);x.lineTo(w,h-6);x.stroke();
 if(sm.length>1){x.strokeStyle='#e0662b';x.lineWidth=4*d;x.lineJoin='round';x.beginPath();
  sm.forEach((v,i)=>{const px=w-(sm.length-i)*step,py=h-6-v/sc*(h-16);i?x.lineTo(px,py):x.moveTo(px,py)});x.stroke()}
}
async function fast(){
 const id=slides[cur].id;
 if(id=='pacer'||id=='wave'||(id=='journey'&&R3)){try{W=await (await fetch('/wave.json',{cache:'no-store'})).json()}catch(e){}}
 if(id=='pacer')pacer();if(id=='wave')wave();
}
live();slow();setInterval(live,1000);setInterval(slow,10000);setInterval(fast,100);
"""


def screen_page():
    conf = read_json(CONF, {})
    lang = get_lang()
    game = conf.get("game", True)
    texts = {"locale": "de-DE" if lang == "de" else "en-GB", "dec": "," if lang == "de" else ".",
             "training": L("Training"), "pause": L("Pause"), "runs": L("läuft"), "off": L("aus"),
             "conn": L("verbunden"), "cable": L("Kabel?"), "waits": L("wartet"), "fw": L("Firmware"),
             "done": L("Ziel erreicht"), "here": L("Du bist hier"),
             "v_ok": L("Bereit zum Rudern"), "v_off": L("Brücke aus"), "v_s4": L("S4 nicht verbunden"),
             "v_app": L("App nicht verbunden"), "h_off": L("Brücke startet oder ist gestoppt. Bitte kurz warten."),
             "h_s4": L("S4 einschalten, Kabel in den inneren USB-Anschluss"),
             "h_app": L("In der App mit „WaterRower S4“ verbinden"), "spm": int(conf.get("pacer_spm") or 0),
             "own": L("Takt {spm}/min · dein Rhythmus", spm="{spm}"), "fixed": L("Takt {spm}/min · fest", spm="{spm}")}
    journey = f"""<section class="slide" id="journey"><div class="sc full">
<div class="jtop"><div class="jhead"><div class="big" id="jr-river">{L("Reise")}</div><span id="jr-prog"></span></div>
<div class="prog"><i id="jr-bar" style="width:0"></i></div></div>
<div class="scene" id="jr-3d"></div>
<div class="navc live3" id="jr-live"><div class="land"><b id="jl-dist">0</b><span>{L("Meter")}</span></div>
<div><b id="jl-pace">–</b><span>/500 m</span></div>
<div><b id="jl-spm">0</b><span>{L("Schläge/min")}</span></div><div><b id="jl-w">0</b><span>{L("Watt")}</span></div>
<div class="land"><b id="jl-time">0:00</b><span>{L("Zeit")}</span></div></div>
<div class="navc" id="jr-stage"><div class="lab">{L("Nächste Etappe")}</div><div class="big" id="jr-next">…</div>
<div class="r2"><span><span id="jr-nkm"></span> km</span><span class="warn" id="jr-neta"></span></div></div>
<div class="grow" id="jr-grow"><svg id="jr-svg"></svg><ul class="route" id="jr-route"></ul></div>
<div class="goal" id="jr-goalbox"><div class="r2"><span><span class="lab">{L("Ziel")}</span> <b id="jr-goal"></b></span><span id="jr-gkm"></span></div>
<div class="lab" id="jr-geta"></div></div></div></section>""" if game else ""
    body = f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#000"><meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Rowbox"><link rel="apple-touch-icon" href="/static/icon-180.png">
<link rel="manifest" href="/static/manifest.webmanifest">
<title>Rowbox – {L("Anzeige")}</title><style>{SCREEN_CSS}</style></head><body>
<div class="top"><a href="/live">‹ rowbox</a><div class="tr"><button id="fs" class="fsb" aria-label="{L("Vollbild")}" hidden>
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg></button>
<div class="dots" id="dots"></div></div></div>
<div class="tip" id="fstip" hidden><b>{L("Vollbild auf dem iPhone")}</b><span>{L("In Safari auf Teilen tippen und „Zum Home-Bildschirm“ wählen. Rowbox startet dann ohne Leisten, wie eine App.")}</span><button id="fsok">OK</button></div>
<main class="deck" id="deck">
<section class="slide" id="training"><div class="sc full"><div class="t" id="tr-t">{L("Training")}</div>
<div class="hero"><div class="huge" id="tr-dist">0</div><div class="sub">{L("Meter")}</div></div>
<div class="tiles" id="tr-row"><div><b id="tr-time">0:00</b><span>{L("Zeit")}</span></div>
<div><b id="tr-pace">–</b><span>/500 m</span></div>
<div><b id="tr-spm">0</b><span>{L("Schläge/min")}</span></div><div><b id="tr-w">0</b><span>{L("Watt")}</span></div></div>
<div class="pausebox" id="tr-pause" style="display:none"><div class="lab">{L("Automatisches Ende in")}</div>
<div class="big warn" id="tr-left"></div></div></div></section>
<section class="slide" id="force"><div class="sc full"><div class="t">{L("Kraftkurve")}</div>
<div class="grow"><svg viewBox="0 -6 300 126" preserveAspectRatio="none">
<g id="fc-g"></g></svg>
<div class="over" id="fc-none">{L("Noch keine Kraftkurve. Sie erscheint nach dem ersten Zug.")}</div></div>
<div class="bar3"><div><b id="fc-d">–</b><span>{L("Zug (s)")}</span></div><div><b id="fc-r">–</b><span>{L("Rollphase (s)")}</span></div>
<div><b id="fc-q">–</b><span>{L("Verhältnis")}</span></div></div></div></section>
<section class="slide" id="pacer"><div class="sc full"><div class="t">{L("Taktgeber")}</div>
<div class="grow"><canvas id="pc"></canvas><div class="over" id="pc-start" style="align-items:flex-start">{L("Startet mit dem ersten Zug")}</div></div>
<div class="foot" id="pc-txt"></div></div></section>
<section class="slide" id="wave"><div class="sc full"><div class="t">{L("Live-Kurve")}</div>
<div class="grow"><canvas id="wv"></canvas></div></div></section>
{journey}
<section class="slide" id="status"><div class="sc full"><div class="t">Rowbox</div>
<div class="verdict"><div class="big" id="s-v">…</div><div class="sub" id="s-vh"></div></div>
<div class="cards"><div id="s-br"><i></i><b>{L("Brücke")}</b><span id="s-brt"></span></div>
<div id="s-s4"><i></i><b>S4</b><span id="s-s4t"></span></div>
<div id="s-app"><i></i><b>App</b><span id="s-appt"></span></div>
<div id="s-bat"><i></i><b>{L("Akku")}</b><span id="s-batt"></span></div></div></div></section>
</main><nav class="swipe"><button id="prev" aria-label="{L("Zurück")}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg></button>
<span style="white-space:nowrap">{L("Wischen für mehr Ansichten")}</span>
<button id="next" aria-label="{L("Weiter")}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg></button></nav>
<script>const T={json.dumps(texts)};{SCREEN_JS}</script></body></html>"""
    return body


# ---------- Wetter an der virtuellen Position ----------
# Die Reise kennt fuer Strecken mit echtem Verlauf die Lage alle 2 km (static/routes/<id>.json, "geo").
# Ist die Box online, holt sie dort hoechstens alle 15 Minuten das aktuelle Wetter (Open-Meteo,
# frei fuer nicht-kommerzielle Nutzung, ohne Anmeldung). Im Hotspot ohne Internet bleibt es leer.
WEATHER_URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat:.2f}&longitude={lon:.2f}"
               "&current=temperature_2m,weather_code,cloud_cover,wind_speed_10m,precipitation,visibility,is_day"
               "&wind_speed_unit=ms")
WEATHER_TTL = 900
WEATHER = {"key": None, "ts": 0.0, "data": None, "busy": False, "failed": 0.0}
ROUTE_GEO = {}


def route_latlon(rid, km):
    path = os.path.join(STATIC_DIR, "routes", f"{rid}.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if ROUTE_GEO.get(rid, (None,))[0] != mtime:
        R = read_json(path, {})
        ROUTE_GEO[rid] = (mtime, R.get("start", 0), R.get("geo"))
    _, start, geo = ROUTE_GEO[rid]
    if not geo or not geo.get("ll"):
        return None
    ll, n = geo["ll"], len(geo["ll"]) // 2
    i = max(0, min(n - 1, round((km * 1000 - start) / geo["step"])))
    return ll[2 * i], ll[2 * i + 1]


def weather_text(code):
    """WMO-Wettercode in Worte (kurz, fuer die Anzeige)."""
    for codes, txt in (((0,), "klar"), ((1,), "heiter"), ((2,), "wolkig"), ((3,), "bedeckt"),
                       ((45, 48), "Nebel"), ((51, 53, 55, 56, 57), "Nieselregen"),
                       ((61, 80), "leichter Regen"), ((63, 65, 66, 67, 81, 82), "Regen"),
                       ((71, 73, 75, 77, 85, 86), "Schnee"), ((95, 96, 99), "Gewitter")):
        if code in codes:
            return L(txt)
    return ""


def fetch_weather(key, lat, lon):
    import urllib.request
    try:
        req = urllib.request.Request(WEATHER_URL.format(lat=lat, lon=lon),
                                     headers={"User-Agent": "Rowbox (+https://github.com/intecsys/rowbox)"})
        with urllib.request.urlopen(req, timeout=8) as r:
            c = json.load(r)["current"]
        data = {"t": c.get("temperature_2m"), "code": c.get("weather_code"), "cloud": c.get("cloud_cover"),
                "wind": c.get("wind_speed_10m"), "rain": c.get("precipitation"), "vis": c.get("visibility"),
                "day": c.get("is_day")}
        with lock:
            WEATHER.update(key=key, ts=time.time(), data=data)
    except (OSError, ValueError, KeyError):
        with lock:                                  # offline: erst in 5 Minuten wieder versuchen
            WEATHER["failed"] = time.time()
    finally:
        WEATHER["busy"] = False


def weather_for(rid, km):
    pos = route_latlon(rid, km)
    if not pos:
        return None
    key = (round(pos[0], 1), round(pos[1], 1))
    now = time.time()
    with lock:
        due = WEATHER["key"] != key or now - WEATHER["ts"] > WEATHER_TTL
        if due and not WEATHER["busy"] and now - WEATHER["failed"] > 300:
            WEATHER["busy"] = True
            threading.Thread(target=fetch_weather, args=(key, *pos), daemon=True).start()
        # bis zum naechsten Abruf das letzte Wetter zeigen, wenn es nicht aelter als eine Stunde ist
        data = WEATHER["data"] if now - WEATHER["ts"] < 3600 else None
    if not data:
        return None
    label = " · ".join(x for x in (f"{data['t']:.0f} °C" if data.get("t") is not None else "",
                                     weather_text(data.get("code"))) if x)
    return dict(data, label=label)


def screen_json():
    """Langsame Werte fuer die Handy-Anzeige: Reise und Akku."""
    conf = read_json(CONF, {})
    out = {"battery": battery(), "journey": None}
    if conf.get("game", True):
        lang = get_lang()
        live = read_json(STATE, {})
        fresh = time.time() - live.get("ts", 0) < 5
        speed = 1800 / live["pace"] if fresh and live.get("active") and live.get("pace") else None
        st = rowgame.status(sessions(), rowgame.load_json(rowgame.GAME, {}), lang,
                            extra_km=(live.get("session_m") or 0) / 1000 if fresh else 0, speed_kmh=speed)
        eta = lambda v: "–" if v in (None, "–") else (L("an {t}", t=v) if ":" in v else L("ca. {t}", t=v))
        dec = lambda x: (f"{x:,.1f}" if x < 10 else f"{x:,.0f}").translate(
            str.maketrans(",.", ".,") if lang == "de" else {})
        out["journey"] = {"route": st["route"], "river": st["river"], "goal": st["goal"], "next": st["next"], "done": st["done"],
                          "next_left": dec(st["next_left"]), "left": dec(st["left"]),
                          "eta_next": eta(st["eta_next"]), "eta_goal": eta(st["eta_goal"]),
                          "km": st["km"], "total": st["total"], "stops": [[k, n] for k, n in st["stops"]]}
        if conf.get("weather", True):
            out["journey"]["weather"] = weather_for(st["route"], st["km"])
        out["journey"]["show"] = conf.get("journey_show") or [k for k, _ in JOURNEY_PARTS]
        out["journey"]["look"] = {k[5:]: conf.get(k) or DEFAULTS[k] for k in ("boat_hull", "boat_blade", "boat_shirt")}
    return json.dumps(out)


def wave_json():
    """Radtempo fuer Taktgeber und Live-Kurve; haelt die Bruecke beim Schreiben (wie das Display)."""
    try:
        with open(WAVE_ON, "w"):
            pass
    except OSError:
        pass
    return json.dumps(read_json(WAVE, {}))


def wifi_qr_text(ssid, password):
    esc = lambda t: "".join("\\" + c if c in '\\;,:"' else c for c in t)
    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"


def qr_svg(text):
    """QR-Code als SVG (python3-qrcode), ohne Bilddatei."""
    try:
        import qrcode
    except ImportError:
        return ""
    q = qrcode.QRCode(border=2)
    q.add_data(text)
    q.make(fit=True)
    m = q.get_matrix()
    n = len(m)
    path = "".join(f"M{x},{y}h1v1h-1z" for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return (f'<svg viewBox="0 0 {n} {n}" shape-rendering="crispEdges" style="width:100%;max-width:260px">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')


def hotspot_card_page():
    """Karte zum Ausdrucken: Hotspot-Name, Passwort, QR-Code. Fuer Boxen ohne Display."""
    conf = read_json(CONF, {})
    ssid, pw = conf.get("ssid", ""), conf.get("password", "")
    return f"""<!doctype html><html lang="{get_lang()}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Rowbox – {L("Hotspot")}</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;color:#000;background:#fff}}
.card{{border:2px dashed #999;border-radius:14px;padding:22px;max-width:320px;text-align:center}}
h1{{font-size:22px;margin:0 0 4px}}p{{margin:6px 0;font-size:14px}}code{{font-size:17px;font-weight:700}}
.no-print{{margin-top:18px}}@media print{{.no-print{{display:none}}body{{margin:0}}}}</style></head><body>
<div class="card"><h1>rowbox</h1><p>{L("WLAN einrichten: QR-Code scannen oder mit diesem Netz verbinden")}</p>
{qr_svg(wifi_qr_text(ssid, pw))}
<p>{L("Netz")}: <code>{e(ssid)}</code></p><p>{L("Passwort")}: <code>{e(pw)}</code></p>
<p>{L("Die Einrichtungsseite öffnet sich von selbst, sonst {url} aufrufen.", url="<code>http://10.42.0.1</code>")}</p></div>
<p class="no-print"><button onclick="print()">{L("Drucken")}</button> <a href="/einstellungen">{L("Zurück")}</a></p>
<p class="no-print" style="max-width:420px;color:#555;font-size:13px">{L("Der Hotspot öffnet sich, wenn die Box kein bekanntes WLAN findet. Ändert sich Name oder Passwort, die Karte neu drucken.")}</p>
</body></html>"""


def wlan_page(wifi, msg=""):
    saved = saved_networks()
    st, con = wlan_connection()
    dup = {n["ssid"] for n in saved if sum(m["ssid"] == n["ssid"] for m in saved) > 1}
    small = 'style="width:auto;margin:0;padding:5px 11px;font-size:14px"'

    def saved_row(n):
        here = n["name"] == con
        state = f' <span class="ok">● {L("verbunden")}</span>' if here else ""
        sub = f'<br><span class="dim" style="font-size:12px">{e(n["name"])}</span>' if n["ssid"] in dup else ""
        connect = ("" if here else
                   f'<button name="do" value="connect" {small}>{L("Verbinden")}</button>')
        return (f'<tr><td>{e(n["ssid"])}{state}{sub}</td><td class="r">'
                f'<form method="post" action="/wlan/gespeichert" style="margin:0;display:flex;gap:6px;justify-content:flex-end">'
                f'<input type="hidden" name="name" value="{e(n["name"])}">{connect}'
                f'<button class="sec" name="do" value="delete" {small}>{L("Entfernen")}</button></form></td></tr>')

    rows = "".join(saved_row(n) for n in saved) or f'<tr><td class="dim">{L("Keine gespeicherten Netze")}</td></tr>'
    dup_note = (f'<p class="dim" style="font-size:13px;margin:10px 0 0">'
                f'{L("Ein Netz ist doppelt gespeichert. Ein altes Profil mit falschen Einstellungen kann das automatische Verbinden stören; im Zweifel das ältere entfernen.")}</p>'
                if dup else "")
    nets = wifi.networks if wifi.mode == "hotspot" else scan(rescan=False)
    opts = "".join(f'<option value="{e(n["ssid"])}">{e(n["ssid"])} ({n["signal"]} %)</option>'
                   for n in nets)
    body = f"""<div class="card"><h2>{L("Gespeicherte Netze")}</h2><table>{rows}</table>{dup_note}</div>
<div class="card"><h2>{L("Netz hinzufügen")}</h2><form method="post" action="/wlan/verbinden">
<label class="dim">{L("Gefundene Netze")}</label><select name="ssid_list"><option value="">{L("– bitte wählen –")}</option>{opts}</select>
<label class="dim">{L("oder Name von Hand")}</label><input name="ssid_manual" placeholder="{L("z. B. MeinWLAN")}">
<label class="dim">{L("Passwort")}</label><input name="password" type="password" autocomplete="off">
<button>{L("Speichern und verbinden")}</button></form>
<p class="dim">{L("Die Box trennt dabei die aktuelle Verbindung. Nach etwa einer Minute ist sie im neuen Netz erreichbar; klappt es nicht, öffnet sie wieder ihren Hotspot.")}</p></div>"""
    return page(L("WLAN"), "/wlan", body, msg or (wifi.message if wifi.mode != "client" else ""))


def logbook_page():
    rows = sessions()[::-1]
    tr = "".join(
        f'<tr><td>{session_link(r)}</td>'
        f'<td class="r">{num(r["dist"])} m</td><td class="r">{mmss(r["dur"])}</td>'
        f'<td class="r">{mmss(r.get("pace", 0))}</td><td class="r">{r.get("watts", 0)}</td>'
        f'<td class="r">{export_links(r)}</td></tr>'
        for r in rows) or f'<tr><td class="dim" colspan="6">{L("Noch keine Einheit")}</td></tr>'
    total = sum(r["dist"] for r in rows)
    what = L("Einheit") if len(rows) == 1 else L("Einheiten")
    body = f"""<div class="card"><h2>{L("{n} {what}, {m} m gesamt", n=len(rows), what=what, m=num(total))}</h2>
<table><tr><th>{L("Start")}</th><th class="r">{L("Distanz")}</th><th class="r">{L("Zeit")}</th><th class="r">/500 m</th>
<th class="r">Ø W</th><th class="r">{L("Export")}</th></tr>{tr}</table>
<p class="dim">{L("FIT passt für Strava, Garmin Connect, Intervals.icu und die meisten anderen; TCX nur, wo FIT nicht geht. Einheiten vor Version 1.2 haben keine Messpunkte und deshalb keinen Export.")}</p></div>
<div class="card"><h2>{L("Herunterladen")}</h2><div class="row">
<a href="/logbuch.csv"><button class="sec">CSV</button></a>
<a href="/logbuch.jsonl"><button class="sec">JSON</button></a></div></div>"""
    return page(L("Logbuch"), "/logbuch", body)


def session_link(r):
    t = r.get("track")
    if t and r.get("curve"):
        return f'<a href="/einheit/{e(t)}">{e(fmt_date(r["start"]))}</a>'
    return e(fmt_date(r["start"]))


def curve_svg(main, faint=(), w=300, h=110, faint_width=1, faint_opacity=0.2):
    """Kraftkurven als SVG: viele blasse, eine kraeftige."""
    pts = lambda c: " ".join(f"{i * w / (len(c) - 1):.1f},{h - v:.1f}" for i, v in enumerate(c))
    lines = "".join(f'<polyline points="{pts(c)}" fill="none" stroke="#7d7d77" stroke-width="{faint_width}" '
                    f'opacity="{faint_opacity}"/>' for c in faint if len(c) > 1)
    if main and len(main) > 1:
        lines += (f'<polyline points="{pts(main)}" fill="none" stroke="#ff5a1f" stroke-width="3" '
                  f'stroke-linejoin="round"/>')
    return (f'<svg viewBox="0 -4 {w} {h + 8}" style="width:100%;height:auto;display:block">'
            f'<line x1="0" y1="{h}" x2="{w}" y2="{h}" stroke="currentColor" opacity=".25"/>{lines}</svg>')


def load_strokes(tid):
    try:
        with open(os.path.join(TRACKS, f"{tid}.strokes.jsonl")) as f:
            return [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        return []


def dec(x, digits=2):
    out = f"{x:.{digits}f}"
    return out.replace(".", ",") if get_lang() == "de" else out


def session_page(tid):
    rec = next((r for r in sessions() if r.get("track") == tid), None)
    if not rec:
        return None
    sts = load_strokes(tid)
    curves = [x["curve"] for x in sts]
    drive, recover = rec.get("drive"), rec.get("recover")
    # Verlauf ueber die Einheit: erstes und letztes Drittel getrennt
    third = len(curves) // 3
    parts = ""
    if third >= 5:
        import rowstroke
        first, last = rowstroke.average(curves[:third]), rowstroke.average(curves[-third:])
        parts = (f'<div class="card"><h2>{L("Anfang und Ende der Einheit")}</h2>'
                 f'{curve_svg(last, [first], faint_width=2.5, faint_opacity=0.9)}'
                 f'<p class="dim">{L("Grau: erstes Drittel der Schläge, orange: letztes Drittel.")}</p></div>')
    stats = [(L("Distanz"), f"{num(rec['dist'])} m"), (L("Zeit"), mmss(rec["dur"])),
             ("/500 m", mmss(rec.get("pace", 0))), (L("Leistung"), f"{rec.get('watts', 0)} W"),
             (L("Schlagzahl"), f"{dec(rec.get('spm', 0), 1)}/min"), (L("Schläge"), str(rec.get("strokes", 0)))]
    if drive:
        stats.append((L("Zug"), f"{dec(drive)} s"))
    if recover:
        stats.append((L("Rollphase"), f"{dec(recover)} s"))
    if drive and recover:
        stats.append((L("Verhältnis"), f"1 : {dec(recover / drive, 1)}"))
    rows = "".join(f'<tr><td>{k}</td><td class="r">{e(v)}</td></tr>' for k, v in stats)
    body = (f'<div class="card"><h2>{e(fmt_date(rec["start"]))}</h2><table>{rows}</table></div>'
            f'<div class="card"><h2>{L("Kraftkurve")}</h2>{curve_svg(rec.get("curve"), curves)}'
            f'<p class="dim">{L("Orange: Durchschnitt, grau: jeder einzelne Zug ({n}). Die Kurve zeigt die Form des Zugs, die Spitze ist 100 %.", n=len(curves))}</p></div>'
            f'{parts}<p><a href="/logbuch" style="color:var(--mute)">← {L("Logbuch")}</a></p>')
    return page(L("Einheit"), "/logbuch", body)


def export_links(r):
    t = r.get("track")
    if not t or not os.path.exists(os.path.join(TRACKS, f"{t}.jsonl")):
        return '<span class="dim">–</span>'
    return (f'<a href="/export/{e(t)}.fit">FIT</a> · '
            f'<a href="/export/{e(t)}.tcx" style="color:var(--mute)">TCX</a>')


def export_file(name):
    """/export/<id>.fit oder .tcx -> (Dateiname, Daten, Typ) oder None."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # rowexport.py liegt daneben
    import rowexport
    tid, _, ext = name.partition(".")
    if ext not in ("fit", "tcx") or not tid.isdigit():
        return None
    rec = next((r for r in sessions() if r.get("track") == tid), None)
    pts = rowexport.load_track(TRACKS, tid) if rec else None
    if not pts:
        return None
    if ext == "fit":
        return rowexport.filename(rec, "fit"), rowexport.fit(rec, pts), "application/vnd.ant.fit"
    return rowexport.filename(rec, "tcx"), rowexport.tcx(rec, pts), "application/vnd.garmin.tcx+xml"


def medal_svg(size=44):
    return (f'<svg viewBox="0 0 44 56" width="{size * 44 // 56}" height="{size}" aria-hidden="true">'
            '<path d="M8 0h10l8 20H16z" fill="#ff5a1f"/><path d="M36 0H26l-8 20h10z" fill="#b8400f"/>'
            '<circle cx="22" cy="36" r="18" fill="#ff5a1f"/>'
            '<circle cx="22" cy="36" r="13" fill="none" stroke="#000" stroke-width="2.5"/></svg>')


def journey_page():
    lang = get_lang()
    game = rowgame.load_json(rowgame.GAME, {})
    sess = sessions()
    live = read_json(STATE, {})
    fresh = time.time() - live.get("ts", 0) < 5
    speed = 1800 / live["pace"] if fresh and live.get("active") and live.get("pace") else None
    st = rowgame.status(sess, game, lang, extra_km=(live.get("session_m") or 0) / 1000 if fresh else 0,
                        speed_kmh=speed)
    pct = min(100, st["km"] / st["total"] * 100)
    kmtxt = lambda x: (f"{x:,.1f}" if x < 100 else f"{x:,.0f}").replace(",", "\u2009").replace(".", "," if lang == "de" else ".").replace("\u2009", "." if lang == "de" else ",")
    stops = []
    shown_here = False
    for k, n in st["stops"]:
        passed = k <= st["km"]
        if not passed and not shown_here and not st["done"]:
            stops.append(f'<li class="here"><i></i><span>{L("Du bist hier")}</span><span class="r">{kmtxt(st["km"])} km</span></li>')
            shown_here = True
        stops.append(f'<li class="{"done" if passed else ""}"><i></i><span>{e(n)}</span><span class="r">{fmt_num(k)} km</span></li>')
    def eta(v):
        if v in (None, "–"):
            return "–"
        return L("an {t}", t=v) if ":" in v else L("ca. {t}", t=v)

    if st["done"]:
        nav = (f'<div class="nav"><div class="nx"><span>{e(st["river"])}</span><b>{e(st["goal"])}</b>'
               f'<div class="r2"><span class="ok">{L("Ziel erreicht")}</span></div></div></div>')
    else:
        nav = (f'<div class="nav"><div class="nx"><span>{L("Nächste Etappe")}</span><b>{e(st["next"])}</b>'
               f'<div class="r2"><span>{kmtxt(st["next_left"])} km</span><span class="eta">{eta(st["eta_next"])}</span></div></div>'
               f'<div class="gl"><span>{L("Ziel")} · {e(st["river"])}</span><b>{e(st["goal"])}</b>'
               f'<div class="r2"><span>{kmtxt(st["left"])} km</span><span>{eta(st["eta_goal"])}</span></div></div>'
               f'<p class="dim" style="font-size:12px;margin:10px 2px 0">'
               + (L("Ankunft beim Rudern nach deinem aktuellen Tempo, sonst nach deinem Schnitt der letzten vier Wochen ({km} km pro Tag), jeweils mit Puffer.",
                    km=kmtxt(st["per_day"])) if st["per_day"] else
                  L("Eine Ankunftszeit gibt es, sobald ein paar Einheiten im Logbuch stehen."))
               + '</p></div>')
    head = (nav + f'<div class="card"><h2>{e(st["name"])}</h2>'
            f'<div class="jkm"><b>{kmtxt(st["km"])}</b> {L("km von {total}", total=fmt_num(st["total"]))}</div>'
            f'<div class="bar2"><span style="width:{pct:.1f}%"></span></div>'
            + (f'<p class="ok" style="font-weight:700">{L("Ziel erreicht")}</p>' if st["done"] else
               f'<p class="dim">{L("noch {km} km bis {name}", km=kmtxt(st["next_left"]), name=e(st["next"]))}</p>')
            + f'<ul class="route">{"".join(stops)}</ul>'
            f'<p class="dim" style="font-size:13px">{L("Jeder geruderte Meter zählt. Es gibt kein Datum und keine Frist.")}</p></div>')
    got = sorted(rowgame.earned(sess, TRACKS, game).items(), key=lambda x: x[1])
    items = "".join(
        f'<div class="tro">{medal_svg()}<div><b>{e(rowgame.trophy_text(t, lang)[0])}</b>'
        f'<span class="dim">{e(rowgame.trophy_text(t, lang)[1])}</span>'
        f'<span class="dim" style="font-size:12px">{e(fmt_date(time.strftime("%Y-%m-%dT%H:%M", time.localtime(w)), with_time=False)) if w else ""}</span></div></div>'
        for t, w in got if t in rowgame.TROPHIES)
    more = len(got) < len(rowgame.TROPHIES)
    tro = (f'<div class="card"><h2>{L("Trophäen")}</h2>'
           + (items or f'<p class="dim">{L("Die erste Trophäe gibt es nach der ersten Einheit.")}</p>')
           + (f'<p class="dim" style="font-size:13px;margin-top:12px">{L("Weitere Trophäen gibt es unterwegs zu entdecken.")}</p>' if more and items else "")
           + "</div>")
    css = """<style>
.jkm{font-variant-numeric:tabular-nums;color:var(--mute)}.jkm b{font-size:56px;font-weight:300;letter-spacing:-.03em;color:var(--ink)}
.bar2{height:10px;border-radius:5px;background:var(--rule);overflow:hidden;margin:10px 0 6px}
.nav{background:#000;color:#f2f2ee;border:1px solid var(--rule);border-radius:10px;padding:14px;margin-bottom:12px}
.nav .nx,.nav .gl{display:flex;flex-direction:column;gap:2px}
.nav .nx{background:var(--signal);color:#000;border-radius:7px;padding:12px 14px}
.nav .gl{padding:14px 14px 4px}
.nav span{font-size:13px;opacity:.8}.nav b{font-size:30px;font-weight:700;letter-spacing:-.02em;line-height:1.15}
.nav .gl b{font-size:22px}
.nav .r2{display:flex;justify-content:space-between;font-variant-numeric:tabular-nums;font-size:18px;margin-top:4px}
.nav .r2 span{font-size:18px;opacity:1}.nav .eta{font-weight:700}
.nav .dim{color:#f2f2ee;opacity:.6}
.bar2 span{display:block;height:100%;background:var(--signal)}
.route{list-style:none;margin:14px 0 6px;padding:0;border-left:2px solid var(--rule);margin-left:5px}
.route li{display:flex;justify-content:space-between;gap:10px;padding:5px 0 5px 16px;position:relative;color:var(--mute)}
.route li i{position:absolute;left:-7px;top:11px;width:12px;height:12px;border-radius:50%;background:var(--panel);border:2px solid var(--rule)}
.route li.done{color:var(--ink)}.route li.done i{background:var(--ink);border-color:var(--ink)}
.route li.here{color:var(--signal);font-weight:700}.route li.here i{background:var(--signal);border-color:var(--signal)}
.tro{display:flex;gap:12px;align-items:center;padding:10px 0;border-top:1px solid var(--rule)}
.tro:first-of-type{border-top:0}.tro div{display:flex;flex-direction:column}
</style>"""
    return page(L("Reise"), "/reise", css + head + tro)


def logs_page(unit):
    units = {"bridge": ("rowbridge", L("Brücke")), "display": ("rowdisplay", L("Anzeige")),
             "portal": ("rowportal", L("Portal")), "wifi": ("NetworkManager", L("WLAN"))}
    old = {"bruecke": "bridge", "anzeige": "display", "wlan": "wifi"}     # alte Links weiter gueltig
    unit = old.get(unit, unit)
    u = units.get(unit, units["bridge"])[0]
    out = subprocess.run(["journalctl", "-u", u, "-n", "200", "--no-pager", "-o", "short"],
                         capture_output=True, text=True, timeout=10).stdout
    tabs = " ".join(f'<a href="/protokoll?u={k}" style="color:{"var(--ink)" if v == u else "var(--mute)"}">{e(lab)}</a>'
                    for k, (v, lab) in units.items())
    body = raw_card() + f'<div class="card"><h2>{tabs}</h2><pre>{e(out[-30000:])}</pre></div>'
    return page(L("Protokoll"), "/protokoll", body)


def raw_files():
    try:
        return sorted((n for n in os.listdir(RAW_DIR) if RAW_NAME.match(n)), reverse=True)
    except OSError:
        return []


def raw_card():
    on = os.path.exists(RAW_FLAG)
    rows = "".join(
        f'<tr><td><a href="/rohdaten/{e(n)}">{e(n[7:-4])}</a></td>'
        f'<td class="r">{os.path.getsize(os.path.join(RAW_DIR, n)) // 1024} KB</td>'
        f'<td class="r"><form method="post" action="/aktion" style="margin:0">'
        f'<input type="hidden" name="name" value="{e(n)}">'
        f'<button class="sec" name="do" value="raw_delete" style="width:auto;margin:0;padding:4px 10px">{L("Löschen")}</button>'
        f'</form></td></tr>' for n in raw_files())
    btn = (f'<button class="danger" name="do" value="raw_off">{L("Aufzeichnung beenden")}</button>' if on else
           f'<button class="sec" name="do" value="raw_on">{L("Aufzeichnung starten")}</button>')
    running = f'<b class="ok">{L("Läuft gerade.")}</b>' if on else ""
    return f"""<div class="card"><h2>{L("Rohdaten vom S4 (Test)")}</h2>
<p class="dim">{L("Zeichnet alle Meldungen des S4 auf, auch Sensorimpulse und Zuganfang/-ende. Nur zum Auswerten gedacht, am normalen Betrieb ändert sich nichts.")}
{running}</p>
<form method="post" action="/aktion">{btn}</form>
{f'<table>{rows}</table>' if rows else ''}</div>"""


def route_opts():
    cur = rowgame.load_json(rowgame.GAME, {}).get("route", rowgame.DEFAULT_ROUTE)
    lang = get_lang()
    out = []
    for cid, cname in rowgame.CONTINENTS:
        opts = "".join(f'<option value="{k}" {"selected" if k == cur else ""}>'
                       f'{e(rowgame.txt(r["name"], lang))} ({fmt_num(r["km"])} km)</option>'
                       for k, r in sorted(rowgame.ROUTES.items(), key=lambda x: x[1]["km"]) if r["cont"] == cid)
        if opts:
            out.append(f'<optgroup label="{e(rowgame.txt(cname, lang))}">{opts}</optgroup>')
    return "".join(out)


def journey_checks(conf):
    on = conf.get("journey_show") or [k for k, _ in JOURNEY_PARTS]
    return "".join(f'<label><input type="checkbox" name="j_{k}" value="1" {"checked" if k in on else ""}>{L(name)}</label>'
                   for k, name in JOURNEY_PARTS)


def color_field(key, label, conf):
    val = conf.get(key) or DEFAULTS[key]
    return f'<div><label class="dim">{label}</label><input type="color" name="{key}" value="{e(val)}"></div>'



def view_checks(conf):
    on = conf.get("views") or [k for k, _ in VIEW_CHOICES]
    rows = []
    for k, name in VIEW_CHOICES:
        fixed = k == "info"
        rows.append(f'<label><input type="checkbox" name="v_{k}" value="1" '
                    f'{"checked" if k in on or fixed else ""} {"disabled" if fixed else ""}>{L(name)}</label>')
    return '<div class="checks">' + "".join(rows) + "</div>"


def settings_page(conf, msg=""):
    sel = lambda k, v: "selected" if str(conf.get(k)) == str(v) else ""
    lang = get_lang()
    lang_opts = "".join(f'<option value="{k}" {"selected" if k == lang else ""}>{v}</option>'
                        for k, v in LANGS.items())
    disp = conf.get("display", True)
    body = f"""<form method="post" action="/einstellungen"><div class="card"><h2>{L("Anzeige") if disp else L("Sprache")}</h2>
<label class="dim">Sprache · Language</label><select name="lang">{lang_opts}</select>
""" + (f"""<label class="dim">LED</label><select name="led">
<option value="warn" {sel("led", "warn")}>{L("nur bei Problemen")}</option>
<option value="on" {sel("led", "on")}>{L("immer Zustandsfarbe")}</option>
<option value="off" {sel("led", "off")}>{L("aus")}</option></select>
<label class="dim">{L("Display aus nach (Sekunden)")}</label>
<input name="timeout" type="number" min="15" max="3600" value="{conf["timeout"]}">
<label class="dim">{L("Zusammenfassung zeigen (Sekunden)")}</label>
<input name="summary" type="number" min="0" max="7200" value="{conf["summary"]}">
<label class="dim">{L("Animation in der Trainingsansicht")}</label><select name="anim">
<option value="pacer" {sel("anim", "pacer")}>{L("Taktgeber")}</option>
<option value="wave" {sel("anim", "wave")}>{L("Live-Kurve")}</option>
<option value="off" {sel("anim", "off")}>{L("aus")}</option></select>
<label class="dim">{L("Takt des Taktgebers (Schläge/min, 0 = eigener Rhythmus)")}</label>
<input name="pacer_spm" type="number" min="0" max="40" value="{conf.get("pacer_spm", 0)}">
<label class="dim">{L("Töne")}</label><select name="sound">
<option value="1" {sel("sound", True)}>{L("an")}</option><option value="0" {sel("sound", False)}>{L("aus")}</option></select>""" if disp else "") + f"""</div>
<div class="card"><h2>{L("Reise und Trophäen")}</h2>
<label class="dim">{L("Anzeigen")}</label><select name="game">
<option value="1" {sel("game", True)}>{L("an")}</option><option value="0" {sel("game", False)}>{L("aus")}</option></select>
<label class="dim">{L("Strecke (ein Wechsel beginnt eine neue Reise bei 0 km)")}</label><select name="route">{route_opts()}</select>
<label class="dim">{L("Echtes Wetter in der 3D-Ansicht (holt die Box bei Open-Meteo, wenn sie online ist)")}</label><select name="weather">
<option value="1" {sel("weather", True)}>{L("an")}</option><option value="0" {sel("weather", False)}>{L("aus")}</option></select>
</div>
<div class="card"><h2>{L("Reise-Ansicht auf dem Handy")}</h2>
<p class="dim">{L("Welche Elemente die Reise-Ansicht zeigt.")}</p>
<div class="checks">{journey_checks(conf)}</div></div>
<div class="card"><h2>{L("Farben im 3D-Boot")}</h2>
<div class="row">{color_field("boat_hull", L("Boot"), conf)}{color_field("boat_blade", L("Blätter"), conf)}{color_field("boat_shirt", L("Shirt"), conf)}</div></div>
""" + (f"""<div class="card"><h2>{L("Ansichten auf dem Display")}</h2>
<p class="dim">{L("Welche Ansichten die Display-Taste durchblättert. Die Automatik ist immer dabei, Info auch (dort steht das Passwort).")}</p>
{view_checks(conf)}</div>
""" if disp else "") + f"""<div class="card"><h2>{L("Hotspot")}</h2>
<label class="dim">{L("Eigenen Hotspot starten, wenn kein WLAN da ist")}</label><select name="hotspot">
<option value="1" {sel("hotspot", True)}>{L("ja")}</option><option value="0" {sel("hotspot", False)}>{L("nein")}</option></select>
<label class="dim">{L("Name")}</label><input name="ssid" value="{e(conf["ssid"])}" maxlength="32">
<label class="dim">{L("Passwort (auch für diese Seite, mind. 8 Zeichen)")}</label>
<input name="password" value="{e(conf["password"])}" minlength="8" maxlength="63">
<p class="dim"><a href="/hotspot-karte">{L("Karte zum Ausdrucken")}</a>: {L("QR-Code für den Hotspot, zum Aufkleben auf die Box. Praktisch ohne Display.")}</p></div>
<div class="card"><h2>{L("MQTT (optional)")}</h2>
<p class="dim">{L("Sendet die Werte an einen MQTT-Server, z. B. für Home Assistant. Leer lassen = aus.")}</p>
<div class="row"><div><label class="dim">{L("Server")}</label><input name="mqtt_host" value="{e(conf["mqtt_host"])}" placeholder="{L("z. B. homeassistant.local")}"></div>
<div style="flex:0 0 90px"><label class="dim">{L("Port")}</label><input name="mqtt_port" type="number" min="1" max="65535" value="{conf["mqtt_port"]}"></div></div>
<div class="row"><div><label class="dim">{L("Benutzer")}</label><input name="mqtt_user" value="{e(conf["mqtt_user"])}" autocomplete="off"></div>
<div><label class="dim">{L("Passwort")}</label><input name="mqtt_password" type="password" value="{e(conf["mqtt_password"])}" autocomplete="off"></div></div>
<label class="dim">{L("Topic")}</label><input name="mqtt_topic" value="{e(conf["mqtt_topic"])}" maxlength="60">
<p class="dim">{L("Zustand: {s}", s=e(mqtt_status()))}</p></div>
<button>{L("Speichern")}</button></form>
""" + (f"""<div class="card"><h2>{L("Einrichtung")}</h2><form method="post" action="/aktion">
<p class="dim">{L("Startet den Assistenten auf dem Display neu, zum Beispiel um übersprungene Schritte nachzuholen.")}</p>
<button class="sec" name="do" value="wizard">{L("Einrichtungsassistent neu starten")}</button></form></div>""" if disp else "")
    return page(L("Einstellungen"), "/einstellungen", body, msg)


# ---------- MQTT ----------

MQTT_STATUS = {"text": "aus", "args": {}}
HA_SENSORS = [("dist", "Distanz", "m", "distance"), ("elapsed", "Zeit", "s", "duration"),
              ("spm", "Schlagzahl", "1/min", None), ("watts", "Leistung", "W", "power"),
              ("pace", "Pace", "s", None), ("cal", "Energie", "kcal", None),
              ("bpm", "Puls", "bpm", None), ("battery", "Akku", "%", "battery")]   # Namen per L()


def mqtt_status():
    return L(MQTT_STATUS["text"], **MQTT_STATUS["args"])


def mqtt_set(text, **args):
    MQTT_STATUS.update(text=text, args=args)


def mqtt_loop(conf):
    """Schickt alle 2 s den Zustand und jede gespeicherte Einheit an den MQTT-Server.
    Meldet die Werte ausserdem bei Home Assistant an (MQTT Discovery)."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        mqtt_set("nicht verfügbar (Paket python3-paho-mqtt fehlt)")
        return
    cli, cur, last_session, t_batt, batt = None, None, None, 0, None
    while True:
        want = (conf["mqtt_host"], conf["mqtt_port"], conf["mqtt_user"], conf["mqtt_password"],
                conf["mqtt_topic"])
        if want != cur:                              # Einstellungen geaendert
            if cli:
                cli.loop_stop(); cli.disconnect(); cli = None
            cur = want
            host, port, user, pw, topic = want
            if not host:
                mqtt_set("aus")
            else:
                try:
                    try:
                        cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="rowbox-" + HOST)
                    except AttributeError:               # paho-mqtt 1.x
                        cli = mqtt.Client(client_id="rowbox-" + HOST)
                    if user:
                        cli.username_pw_set(user, pw)
                    cli.will_set(f"{topic}/online", "offline", retain=True)
                    cli.connect_async(host, int(port))
                    cli.loop_start()
                    mqtt_set("verbinde mit {host} …", host=host)
                    discovered = False
                except Exception as ex:
                    mqtt_set("Fehler: {err}", err=ex)
                    cli = None
        if cli:
            topic = cur[4]
            if cli.is_connected():
                mqtt_set("verbunden mit {host}", host=cur[0])
                if not discovered:
                    dev = {"identifiers": ["rowbox-" + HOST], "name": "Rowbox", "manufacturer": "Rowbox"}
                    for key, name, unit, cls in HA_SENSORS:
                        cfg = {"name": L(name), "unique_id": f"rowbox_{HOST.split('.')[0]}_{key}",
                               "state_topic": f"{topic}/state", "unit_of_measurement": unit,
                               "value_template": "{{ value_json.%s }}" % key,
                               "availability_topic": f"{topic}/online", "device": dev}
                        if cls:
                            cfg["device_class"] = cls
                        cli.publish(f"homeassistant/sensor/rowbox_{HOST.split('.')[0]}/{key}/config",
                                    json.dumps(cfg), retain=True)
                    cli.publish(f"homeassistant/binary_sensor/rowbox_{HOST.split('.')[0]}/active/config",
                                json.dumps({"name": L("Rudert"), "unique_id": f"rowbox_{HOST.split('.')[0]}_active",
                                            "state_topic": f"{topic}/state",
                                            "value_template": "{{ 'ON' if value_json.active else 'OFF' }}",
                                            "availability_topic": f"{topic}/online",
                                            "device": dev}), retain=True)
                    cli.publish(f"{topic}/online", "online", retain=True)
                    discovered = True
                if time.time() - t_batt > 60:
                    batt, t_batt = battery(), time.time()
                s = read_json(STATE, {})
                s.pop("last", None)
                s["battery"] = batt
                cli.publish(f"{topic}/state", json.dumps(s), retain=True)
                rows = sessions()
                if rows and rows[-1].get("ended") != last_session:
                    if last_session is not None:     # nur neue Einheiten, nicht beim Start
                        cli.publish(f"{topic}/session", json.dumps(rows[-1]))
                    last_session = rows[-1].get("ended")
                elif not rows:
                    last_session = 0
            else:
                mqtt_set("keine Verbindung zu {host}", host=cur[0])
                discovered = False
        time.sleep(2)


# ---------- Webserver ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "Rowbox"

    def log_message(self, fmt, *args):
        pass

    def local_hotspot(self):
        return self.client_address[0].startswith(HOTSPOT_NET)

    def token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "rowbox":
                return v
        return ""

    def logged_in(self):
        if self.local_hotspot():
            mark_portal_seen()
            return True
        exp = SESSIONS.get(self.token())
        return bool(exp and exp > time.time())

    def start_session(self, nxt):
        tok = secrets.token_urlsafe(24)
        SESSIONS[tok] = time.time() + SESSION_DAYS * 86400
        mark_portal_seen()
        self.send_response(303)
        self.send_header("Set-Cookie", f"rowbox={tok}; Path=/; HttpOnly; SameSite=Lax; "
                                       f"Max-Age={SESSION_DAYS * 86400}")
        self.send_header("Location", nxt)
        self.end_headers()

    def authorized(self):
        """Nicht angemeldet: auf die Anmeldeseite umlenken."""
        if self.logged_in():
            return True
        if urlparse(self.path).path.endswith(".json"):
            self.send("{}", "application/json", code=401)
        elif self.command == "GET":
            self.redirect("/login?next=" + self.path.replace("&", "%26"))
        else:
            self.redirect("/login")
        return False

    def send(self, body, ctype="text/html; charset=utf-8", code=200, extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def static(self, name):
        """Dateien aus static/ (three.js, 3D-Flussfahrt): lange cachen, gzip einmal im Speicher."""
        ctype = STATIC_FILES.get(name)
        if not ctype and re.fullmatch(r"routes/[a-z0-9_-]+\.json", name):
            ctype = "application/json"           # echte Flussverlaeufe aus OpenStreetMap
        path = os.path.join(STATIC_DIR, name)
        if not ctype or not os.path.isfile(path):
            return self.send("", "text/plain", code=404)
        mtime = os.path.getmtime(path)
        if STATIC_CACHE.get(name, (None,) * 4)[3] != mtime:     # nach einem Update neu einlesen
            raw = open(path, "rb").read()
            STATIC_CACHE[name] = (raw, gzip.compress(raw, 6), hashlib.sha1(raw).hexdigest()[:16], mtime)
        raw, gz, tag, _ = STATIC_CACHE[name]
        if self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            self.end_headers()
            return
        use_gz = "gzip" in (self.headers.get("Accept-Encoding") or "")
        data = gz if use_gz else raw
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # three.js und Icons lange cachen; 3D-Szene und Strecken nach jedem Update sofort neu (ETag, meist 304)
        self.send_header("Cache-Control", "public, max-age=86400" if name in STATIC_LONG else "no-cache")
        self.send_header("ETag", tag)
        if use_gz:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        # Captive Portal: fremde Hosts im Hotspot auf die Startseite umlenken
        if self.local_hotspot() and host not in ("10.42.0.1", HOST, ""):
            return self.redirect("http://10.42.0.1/wlan")
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/login":
            if q.get("t") and use_login_token(q["t"][0]):
                return self.start_session("/")
            nxt = q.get("next", ["/"])[0]
            return self.send(login_page(nxt=nxt if nxt.startswith("/") else "/", lang=q.get("l", [None])[0]))
        if u.path.startswith("/static/"):
            return self.static(u.path[len("/static/"):])
        if u.path == "/logout":
            SESSIONS.pop(self.token(), None)
            self.send_response(303)
            self.send_header("Set-Cookie", "rowbox=; Path=/; Max-Age=0")
            self.send_header("Location", "/login")
            self.end_headers()
            return
        if not self.authorized():
            return
        wifi, conf = self.server.wifi, self.server.conf
        if u.path == "/sprache":                  # Umschalter im Kopf: gilt fuer die ganze Box
            if q.get("l", [""])[0] in LANGS:
                try:
                    set_lang(q["l"][0])
                except OSError:
                    pass
            nxt = q.get("next", ["/"])[0]
            return self.redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        if u.path == "/":
            self.send(status_page(wifi))
        elif u.path == "/live":
            self.send(live_page())
        elif u.path == "/live.json":
            self.send(live_json(), "application/json")
        elif u.path in ("/anzeige", "/display"):
            self.send(screen_page())
        elif u.path == "/anzeige.json":
            self.send(screen_json(), "application/json")
        elif u.path == "/hotspot-karte":
            self.send(hotspot_card_page())
        elif u.path == "/wave.json":
            self.send(wave_json(), "application/json")
        elif u.path == "/wlan":
            self.send(wlan_page(wifi))
        elif u.path == "/logbuch":
            self.send(logbook_page())
        elif u.path == "/logbuch.jsonl":
            self.send("\n".join(json.dumps(r) for r in sessions()), "application/json",
                      extra={"Content-Disposition": 'attachment; filename="rowbox-logbuch.jsonl"'})
        elif u.path == "/logbuch.csv":
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            w.writerow([L(h) for h in ("Start", "Dauer_s", "Distanz_m", "Schlaege", "Schlagzahl", "Watt",
                                        "Watt_max", "Pace_500m_s", "Puls", "Puls_max")])
            for r in sessions():
                w.writerow([r["start"], r["dur"], r["dist"], r["strokes"], r["spm"], r["watts"],
                            r.get("watts_max", ""), r["pace"], r.get("hr", ""), r.get("hr_max", "")])
            self.send(buf.getvalue(), "text/csv; charset=utf-8",
                      extra={"Content-Disposition": 'attachment; filename="rowbox-logbuch.csv"'})
        elif u.path.startswith("/rohdaten/"):
            name = u.path[len("/rohdaten/"):]
            if not RAW_NAME.match(name) or not os.path.exists(os.path.join(RAW_DIR, name)):
                return self.send(page(L("Rohdaten"), "/protokoll", f"<p>{L('Datei nicht gefunden.')}</p>"), code=404)
            with open(os.path.join(RAW_DIR, name), "rb") as fh:
                data = fh.read()
            self.send(data, "text/plain; charset=utf-8",
                      extra={"Content-Disposition": f'attachment; filename="{name}"'})
        elif u.path.startswith("/einheit/"):
            out = session_page(u.path[len("/einheit/"):])
            if not out:
                return self.send(page(L("Nicht gefunden"), "", f"<p>{L('Seite nicht gefunden.')}</p>"), code=404)
            self.send(out)
        elif u.path.startswith("/export/"):
            out = export_file(u.path[len("/export/"):])
            if not out:
                return self.send(page(L("Export"), "/logbuch", f"<p>{L('Keine Messpunkte für diese Einheit.')}</p>"), code=404)
            fname, data, ctype = out
            self.send(data, ctype, extra={"Content-Disposition": f'attachment; filename="{fname}"'})
        elif u.path == "/reise":
            self.send(journey_page())
        elif u.path == "/protokoll":
            self.send(logs_page(q.get("u", ["bruecke"])[0]))
        elif u.path == "/einstellungen":
            self.send(settings_page(conf))
        else:
            self.send(page(L("Nicht gefunden"), "", f"<p>{L('Seite nicht gefunden.')}</p>"), code=404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(n, 10000)).decode(errors="ignore")
        f = {k: v[0] for k, v in parse_qs(raw).items()}
        if urlparse(self.path).path == "/login":
            nxt = f.get("next", "/")
            nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"
            if secrets.compare_digest(f.get("password", ""), self.server.conf["password"]):
                if f.get("l") in LANGS and f["l"] != get_lang():   # auf der Anmeldeseite gewaehlte Sprache uebernehmen
                    try:
                        set_lang(f["l"])
                    except OSError:
                        pass
                self.start_session(nxt)
            else:
                time.sleep(1)              # bremst Durchprobieren
                lang = f.get("l") if f.get("l") in LANGS else None
                self.send(login_page(L("Falsches Passwort. Bitte noch einmal.", lang), nxt, lang), code=403)
            return
        if not self.authorized():
            return
        wifi, conf = self.server.wifi, self.server.conf
        path = urlparse(self.path).path
        if path == "/wlan/verbinden":
            ssid = (f.get("ssid_manual") or f.get("ssid_list") or "").strip()
            pw = f.get("password", "")
            if not ssid or len(ssid) > 32:
                return self.send(wlan_page(wifi, L("Bitte ein Netz auswählen oder eingeben.")))
            if pw and not 8 <= len(pw) <= 63:
                return self.send(wlan_page(wifi, L("Das Passwort muss 8 bis 63 Zeichen haben.")))
            threading.Thread(target=wifi.connect, args=(ssid, pw), daemon=True).start()
            return self.send(page(L("WLAN"), "/wlan", f"""<div class="card"><h2>{L("Verbinde mit {ssid}", ssid=e(ssid))}</h2>
<p>{L("Die Box trennt jetzt die bisherige Verbindung und wählt sich neu ein. Das dauert etwa eine Minute. Klappt es, ist die Box danach unter <b>{url}</b> im neuen Netz erreichbar. Klappt es nicht, öffnet sie wieder ihren Hotspot.", url=f"http://{HOST}")}</p></div>"""))
        if path in ("/wlan/loeschen", "/wlan/gespeichert"):
            name = f.get("name", "")
            known = {n["name"]: n["ssid"] for n in saved_networks()}
            if name not in known:
                return self.redirect("/wlan")
            if f.get("do") == "connect":
                threading.Thread(target=wifi.connect_saved, args=(name, known[name]), daemon=True).start()
                return self.send(page(L("WLAN"), "/wlan", f"""<div class="card"><h2>{L("Verbinde mit {ssid}", ssid=e(known[name]))}</h2>
<p>{L("Die Box trennt jetzt die bisherige Verbindung und wählt sich neu ein. Das dauert etwa eine Minute. Klappt es, ist die Box danach unter <b>{url}</b> im neuen Netz erreichbar. Klappt es nicht, öffnet sie wieder ihren Hotspot.", url=f"http://{HOST}")}</p></div>"""))
            nm("connection", "delete", name)
            return self.redirect("/wlan")
        if path == "/einstellungen":
            try:
                new = {"led": f.get("led") if f.get("led") in ("warn", "on", "off") else "warn",
                       "timeout": max(15, min(3600, int(f.get("timeout", 120)))),
                       "summary": max(0, min(7200, int(f.get("summary", 600)))),
                       "hotspot": f.get("hotspot") == "1",
                       "sound": f.get("sound", "1") == "1",
                       "anim": f.get("anim") if f.get("anim") in ("pacer", "wave", "off") else "pacer",
                       "pacer_spm": max(0, min(40, int(f.get("pacer_spm") or 0))),
                       "views": [k for k, _ in VIEW_CHOICES if k == "info" or f.get("v_" + k)],
                       "game": f.get("game", "1") == "1",
                       "weather": f.get("weather", "1") == "1",
                       "journey_show": [k for k, _ in JOURNEY_PARTS if f.get("j_" + k)],
                       **{k: (f.get(k) if COLOR.match(f.get(k) or "") else DEFAULTS[k]).lower()
                          for k in ("boat_hull", "boat_blade", "boat_shirt")},
                       "ssid": (f.get("ssid") or conf["ssid"]).strip()[:32],
                       "password": f.get("password") or conf["password"],
                       "mqtt_host": (f.get("mqtt_host") or "").strip()[:100],
                       "mqtt_port": max(1, min(65535, int(f.get("mqtt_port") or 1883))),
                       "mqtt_user": (f.get("mqtt_user") or "").strip()[:100],
                       "mqtt_password": f.get("mqtt_password") or "",
                       "mqtt_topic": re.sub(r"[^A-Za-z0-9_/-]", "", f.get("mqtt_topic") or "rowbox")[:60] or "rowbox"}
            except ValueError:
                return self.send(settings_page(conf, L("Bitte nur Zahlen bei den Sekunden.")))
            if not conf.get("display", True):           # Felder fuers Display fehlen im Formular
                for k in ("led", "timeout", "summary", "sound", "anim", "pacer_spm", "views"):
                    new[k] = conf.get(k, DEFAULTS[k])
            if not 8 <= len(new["password"]) <= 63:
                return self.send(settings_page(conf, L("Das Passwort muss 8 bis 63 Zeichen haben.")))
            if f.get("lang") in LANGS:
                try:
                    set_lang(f["lang"])
                except OSError:
                    pass
            cur_route = rowgame.load_json(rowgame.GAME, {}).get("route", rowgame.DEFAULT_ROUTE)
            if f.get("route") in rowgame.ROUTES and f["route"] != cur_route:
                rowgame.set_route(f["route"])          # neue Reise ab jetzt
            conf.update(new)
            save_conf(conf)
            wifi.publish()
            return self.send(settings_page(conf, L("Gespeichert. Hotspot-Änderungen gelten beim nächsten Start des Hotspots.")))
        if path == "/aktion":
            act = f.get("do")
            if act == "wizard":
                for path_ in (ONBOARD, PORTAL_SEEN):
                    try:
                        os.remove(path_)
                    except OSError:
                        pass
                return self.send(settings_page(conf, L("Der Assistent erscheint in wenigen Sekunden auf dem Display.")))
            if act in ("raw_on", "raw_off", "raw_delete"):
                try:
                    if act == "raw_on":
                        os.makedirs(os.path.dirname(RAW_FLAG), exist_ok=True)
                        open(RAW_FLAG, "w").close()
                    elif act == "raw_off":
                        os.remove(RAW_FLAG)
                    elif RAW_NAME.match(f.get("name", "")):
                        os.remove(os.path.join(RAW_DIR, f["name"]))
                except OSError:
                    pass
                time.sleep(1.5)                # Bruecke bemerkt den Schalter jede Sekunde
                return self.redirect("/protokoll")
            if act in ("restart", "poweroff"):
                self.send(page(L("Gerät"), "/", f'<div class="card"><p>{L("Startet neu ...") if act == "restart" else L("Schaltet aus ...")}</p></div>'))
                threading.Timer(2, lambda: subprocess.run(
                    ["systemctl", "reboot" if act == "restart" else "poweroff"])).start()
                return
        self.redirect("/")


def main():
    conf = load_conf()
    wifi = Wifi(conf)
    threading.Thread(target=wifi.loop, daemon=True).start()
    threading.Thread(target=mqtt_loop, args=(conf,), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("ROWBOX_PORT", "80"))), Handler)
    srv.conf, srv.wifi = conf, wifi
    log(f"Portal laeuft auf Port {srv.server_address[1]}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
