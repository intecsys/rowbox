#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 The Rowbox authors
"""rowdisplay: Statusanzeige der Rudergeraet-Bruecke auf dem PiSugar Whisplay.

Liest den Zustand der Bruecke aus /run/rowbridge/state.json, den Akku vom
PiSugar-Server und das WLAN von NetworkManager. Zeigt das auf dem Display an,
steuert die LED und reagiert auf die Taste.

Beim ersten Start fuehrt ein Assistent durch WLAN, Webportal, S4, App und
einen Probeschlag. Waehrend des Assistenten ueberspringt ein kurzer Druck den
aktuellen Schritt.

Taste kurz:   naechste Ansicht bzw. Display wieder einschalten
Taste lang:   ab 1 s erscheint ein Hinweis, nach 3 s geht der Hotspot auf
              (oder wieder zu, wenn er schon an ist); loslassen bricht ab
Ausschalten:  untere Taste am PiSugar 3 (bei den Akku-LEDs) lang halten, oder im Webportal
Obere Taste am PiSugar 3 (optional, frei belegbare Taste):
              kurz = Display an mit Statusanzeige, lang = Sprache wechseln.
              Der PiSugar-Dienst schreibt dafuer "status" bzw. "lang" nach KEY_REQ.
"""
import json, os, socket, subprocess, sys, threading, time
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # rowtext.py liegt daneben
from rowtext import L, LANGS, chosen as lang_chosen, fmt_date, fmt_num, get_lang, set_lang
import rowgame

RUNTIME = os.environ.get("WHISPLAY_RUNTIME") or next(
    (p for p in __import__("glob").glob("/home/*/Whisplay/runtime")), "Whisplay/runtime")
HOST = socket.gethostname() + ".local"

STATE = "/run/rowbridge/state.json"
LOGBOOK = "/var/lib/rowbridge/sessions.jsonl"
CONF = "/etc/rowbox.json"                 # Einstellungen aus dem Webportal
PORTAL = "/run/rowbridge/portal.json"     # Hotspot-Zustand vom Webportal
LOGIN = "/run/rowbridge/login_token.json" # Einmal-Anmeldelink vom Webportal
ONBOARD = "/var/lib/rowbridge/onboarding.json"
PORTAL_SEEN = "/var/lib/rowbridge/portal_seen"
APP_URL = "https://apps.apple.com/app/id6470894043"   # Rowing Workout (Beispiel-App)
STEPS = ["lang", "wlan", "portal", "s4", "app", "row"]
LANG_WAIT = 8          # Sekunden ohne Tastendruck, dann gilt die angezeigte Sprache
SOUND_DIR = "/run/rowbridge/sounds"
SUMMARY_FOR = 600      # so lange nach einer Einheit zeigt die Automatik die Zusammenfassung
HOLD_WARN, HOLD_ACT = 1.0, 3.0   # Sekunden Taste halten: Hinweis / Hotspot umschalten
HOTSPOT_REQ = "/run/rowbridge/hotspot_request"   # Auftrag ans Webportal
KEY_REQ = "/run/rowbridge/key_request"           # von der oberen PiSugar-Taste
WAVE = "/run/rowbridge/wave.json"                # Radtempo der letzten 6 s (von der Bruecke)
WAVE_ON = "/run/rowbridge/wave_on"               # sagt der Bruecke, dass wir es brauchen
ANIM_FPS = 10
PISUGAR = "/tmp/pisugar-server.sock"
W, H = 240, 280
TIMEOUT = 120          # Sekunden ohne Ereignis, dann Display aus
LOW_BATT = 15          # Prozent, ab hier rote LED
# LED und Hintergrundlicht werden nur voll an oder aus geschaltet. Gedimmt
# (Software-PWM in Python) flackern beide, sobald das Programm rechnet.
# ROWBOX_LED: "warn" = nur bei Problemen (Standard), "on" = immer Zustandsfarbe, "off" = nie
LED_MODE = os.environ.get("ROWBOX_LED", "warn")
VIEWS = ["auto", "status", "training", "force", "pacer", "wave", "summary", "history", "journey", "info"]
LABEL = {"status": "Status", "training": "Training", "force": "Kraftkurve", "pacer": "Taktgeber",
         "wave": "Live-Kurve", "summary": "Letzte Einheit", "history": "Verlauf", "journey": "Reise",
         "info": "Info"}           # wird mit L() uebersetzt

C_BG = (0, 0, 0)
C_FG = (235, 238, 242)
C_DIM = (120, 128, 140)
C_OK = (60, 200, 110)
C_WAIT = (70, 140, 255)
C_BAD = (235, 70, 60)
C_WARN = (240, 170, 40)


def font(size, bold=False):
    names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
             "FreeSansBold.ttf" if bold else "FreeSans.ttf"]
    for d in ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/freefont"]:
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


F_SMALL, F_MED, F_BIG, F_HUGE = font(16), font(20, True), font(30, True), font(46, True)
F_TINY = font(14)


def to_rgb565(img):
    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    v = ((a[:, :, 0] & 0xF8) << 8) | ((a[:, :, 1] & 0xFC) << 3) | (a[:, :, 2] >> 3)
    return v.astype(">u2").tobytes()


def read_state():
    try:
        with open(STATE) as f:
            s = json.load(f)
        s["fresh"] = time.time() - s.get("ts", 0) < 5
        return s
    except (OSError, ValueError):
        return {"fresh": False}


def pisugar(cmd):
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(1)
            s.connect(PISUGAR)
            s.sendall(cmd.encode() + b"\n")
            return s.recv(200).decode().split(":", 1)[1].strip()
    except (OSError, IndexError):
        return None


def battery():
    pct, plug = pisugar("get battery"), pisugar("get battery_power_plugged")
    try:
        return round(float(pct)), plug == "true"
    except (TypeError, ValueError):
        return None, False


def wifi():
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS", "dev", "show", "wlan0"],
                             capture_output=True, text=True, timeout=3).stdout
        conn = ip = ""
        for line in out.splitlines():
            if line.startswith("GENERAL.CONNECTION:"):
                conn = line.split(":", 1)[1]
            elif line.startswith("IP4.ADDRESS") and not ip:
                ip = line.split(":", 1)[1].split("/")[0]
        if conn in ("", "--"):
            return None, None
        ssid = subprocess.run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", conn],
                              capture_output=True, text=True, timeout=3).stdout.strip()
        return ssid or conn, ip
    except (OSError, subprocess.SubprocessError):
        return None, None


def mmss(sec):
    sec = int(sec or 0)
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def row(d, y, label, ok, text, color=None):
    d.ellipse((16, y + 5, 30, y + 19), fill=color or (C_OK if ok else C_BAD))
    d.text((42, y), label, font=F_MED, fill=C_FG)
    d.text((224, y + 2), text, font=F_SMALL, fill=C_DIM, anchor="ra")


def header(d, title, batt, plug):
    d.text((W // 2, 26), title, font=F_MED, fill=C_DIM, anchor="mm")
    if batt is not None:
        col = C_BAD if batt <= LOW_BATT else C_DIM
        d.text((W // 2, 50), L("Akku {pct} %", pct=batt) + (L("  lädt") if plug else ""),
               font=F_SMALL, fill=col, anchor="mm")


def draw_status(s, batt, plug, ssid, hotspot=False):
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    header(d, "Rowbox", batt, plug)
    y = 74
    row(d, y, L("Brücke"), s["fresh"], L("läuft") if s["fresh"] else L("aus")); y += 36
    row(d, y, "S4", s.get("s4"), L("verbunden") if s.get("s4") else L("Kabel?")); y += 36
    row(d, y, "App", s.get("app"), L("verbunden") if s.get("app") else L("wartet"),
        None if s.get("app") else C_WAIT); y += 36
    if hotspot:                                    # eigener Hotspot ist kein WLAN-Anschluss
        row(d, y, L("WLAN"), False, L("Hotspot"), C_WAIT)
    else:
        name = ssid or L("keins")
        row(d, y, L("WLAN"), bool(ssid), name if len(name) <= 12 else name[:11] + "…")
    hint = status_hint(s, ssid)
    if hint:                                       # zwei Zeilen, mit Abstand zur Ansichtsbezeichnung
        for i, line in enumerate(wrap(d, hint, F_TINY, 210)[:2]):
            d.text((W // 2, 222 + i * 18), line, font=F_TINY, fill=C_WARN, anchor="mm")
    elif s.get("dist"):
        d.text((W // 2, 236), L("Letzte Einheit: {m}", m=f"{fmt_num(s['dist'])} m"), font=F_SMALL, fill=C_DIM, anchor="mm")
    return img


def status_hint(s, ssid):
    """Kurzer Hinweis, was zu tun ist. None, wenn alles in Ordnung ist."""
    if not s["fresh"]:
        return L("Brücke startet oder ist gestoppt. Bitte kurz warten.")
    if not s.get("s4"):
        return L("S4 einschalten, Kabel in den inneren USB-Anschluss")
    if not s.get("app"):
        return L("In der App mit „WaterRower S4“ verbinden")
    return None


TRAIN_STRIP = (186, 76)      # y und Hoehe des Animationsstreifens in der Trainingsansicht
ANIM_STRIP = (64, 192)       # in den eigenen Ansichten Taktgeber und Live-Kurve


def draw_training_compact(s, batt, plug):
    """Trainingsansicht mit Platz fuer den Taktgeber oder die Live-Kurve unten."""
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    header(d, L("Training"), batt, plug)
    d.text((W // 2, 92), fmt_num(s.get("dist", 0)), font=F_HUGE, fill=C_FG, anchor="mm")
    d.text((W // 2, 124), L("Meter"), font=F_TINY, fill=C_DIM, anchor="mm")
    for x, val, lab in ((48, mmss(s.get("elapsed")), L("Zeit")), (130, str(s.get("spm", 0)), "/min"),
                        (200, str(s.get("watts", 0)), L("Watt"))):
        d.text((x, 152), val, font=F_MED, fill=C_FG, anchor="mm")
        d.text((x, 172), lab, font=F_TINY, fill=C_DIM, anchor="mm")
    return img


def draw_anim_frame(title, batt, plug):
    img = Image.new("RGB", (W, H), C_BG)
    header(ImageDraw.Draw(img), title, batt, plug)
    return img


def wrap_phase(x):
    return (x + 0.5) % 1.0 - 0.5


class Pacer:
    """Taktgeber: ein Punkt laeuft wie der Rollsitz, schnell im Zug, langsam zurueck (1 : 2).
    Der Takt folgt dem eigenen Rhythmus (Schnitt der letzten Zuege) oder einer festen Schlagzahl."""
    DRIVE = 1 / 3

    def __init__(self):
        self.phase, self.period, self.t = 0.0, None, time.monotonic()
        self.last_catch, self.started, self.warm = 0.0, False, 0
        self.marks = deque(maxlen=8)           # True = im Takt, False = daneben

    def update(self, wave, fixed_spm):
        now = time.monotonic()
        dt, self.t = now - self.t, now
        target = 60 / fixed_spm if fixed_spm else (wave.get("period") or 60 / 22)
        self.period = target if self.period is None else self.period + (target - self.period) * min(1, dt * 0.5)
        self.phase = (self.phase + dt / self.period) % 1.0
        # Zuganfaenge aus der Bruecke: Zeitpunkte relativ zu jetzt
        age = time.time() - wave.get("ts", 0)
        for ago in sorted(wave.get("catch_ago", []), reverse=True):
            c = now - ago - age
            if c <= self.last_catch + max(1.2, 0.6 * self.period):   # schon gesehen, oder Doppelerkennung
                continue
            anchor = ((now - c) / self.period) % 1.0   # Phase, wenn dieser Zug genau im Takt waere
            if not self.started or c - self.last_catch > 10 or self.warm < 2:
                if c - self.last_catch > 10:
                    self.warm = 0
                self.phase, self.started = anchor, True   # die ersten Zuege geben den Takt vor
                self.warm += 1
            else:
                err = wrap_phase(self.phase - anchor)
                self.marks.append(abs(err) < 0.12)
                if not fixed_spm:
                    # eigener Rhythmus: sanft nachziehen, bei deutlichem Wechsel neu ansetzen
                    self.phase = anchor if abs(err) > 0.3 else (self.phase - 0.3 * err) % 1.0
            self.last_catch = c

    def seat(self):
        """Position 0 (vorne) .. 1 (hinten)."""
        p = self.phase
        if p < self.DRIVE:
            x = p / self.DRIVE
            return 0.5 - 0.5 * __import__("math").cos(x * 3.14159)
        x = (p - self.DRIVE) / (1 - self.DRIVE)
        return 1 - (0.5 - 0.5 * __import__("math").cos(x * 3.14159))


def draw_pacer(pacer, h, big, fixed_spm):
    img = Image.new("RGB", (W, h), C_BG)
    d = ImageDraw.Draw(img)
    y = int(h * (0.38 if big else 0.36))
    x0, x1 = 30, 210
    d.line((x0, y, x1, y), fill=(60, 66, 76), width=4)
    for x in (x0, x1):
        d.line((x, y - 8, x, y + 8), fill=(60, 66, 76), width=2)
    x = x0 + pacer.seat() * (x1 - x0)
    r = 13 if big else 10
    col = C_OK if pacer.phase < Pacer.DRIVE else C_WAIT
    d.ellipse((x - r, y - r, x + r, y + r), fill=col)
    my = int(h * 0.72) if big else h - 14
    n = len(pacer.marks)
    for i, ok in enumerate(pacer.marks):
        cx = W // 2 + (i - (n - 1) / 2) * 20
        d.ellipse((cx - 5, my - 5, cx + 5, my + 5), fill=C_OK if ok else C_WARN)
    if big:
        spm = round(60 / pacer.period) if pacer.period else 0
        txt = L("Takt {spm}/min · fest", spm=spm) if fixed_spm else L("Takt {spm}/min · dein Rhythmus", spm=spm)
        d.text((W // 2, h - 14), txt, font=F_TINY, fill=C_DIM, anchor="mm")
        if not pacer.started:
            d.text((W // 2, int(h * 0.55)), L("Startet mit dem ersten Zug"), font=F_TINY, fill=C_DIM, anchor="mm")
    return img


def draw_wave(wave, h):
    """Live-Kurve: Tempo des Paddelrads der letzten 6 Sekunden, laeuft von rechts nach links."""
    img = Image.new("RGB", (W, h), C_BG)
    d = ImageDraw.Draw(img)
    raw = wave.get("w") or []
    w = [sum(raw[max(0, i - 3):i + 1]) / len(raw[max(0, i - 3):i + 1]) for i in range(len(raw))]  # glaetten
    scale = max(600, max(w or [0]) * 1.1)
    d.line((0, h - 4, W, h - 4), fill=(50, 55, 62))
    age = time.time() - wave.get("ts", 0)
    for ago in wave.get("catch_ago", []):          # Zuganfaenge als duenne Linien
        x = W - 1 - (ago + age) / 0.025
        if 0 <= x < W:
            d.line((x, 4, x, h - 4), fill=(45, 70, 50), width=1)
    if len(w) > 1:
        off = W - len(w)
        pts = [(off + i, h - 4 - v / scale * (h - 10)) for i, v in enumerate(w)]
        d.line(pts, fill=(224, 102, 43), width=2)
    return img


def draw_training(s, batt, plug):
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    header(d, L("Training") if s.get("active") else L("Pause"), batt, plug)
    d.text((W // 2, 105), f"{s.get('dist', 0)}", font=F_HUGE, fill=C_FG, anchor="mm")
    d.text((W // 2, 140), L("Meter"), font=F_SMALL, fill=C_DIM, anchor="mm")
    d.text((W // 2, 180), mmss(s.get("elapsed")), font=F_BIG, fill=C_FG, anchor="mm")
    if not s.get("active") and s.get("pause_left") is not None:
        # Pause innerhalb der Einheit: zeigen, wann sie automatisch endet
        d.text((W // 2, 216), L("Automatisches Ende in"), font=F_TINY, fill=C_DIM, anchor="mm")
        d.text((W // 2, 244), mmss(s["pause_left"]), font=F_BIG, fill=C_WARN, anchor="mm")
        return img
    d.text((70, 222), f"{s.get('spm', 0)}", font=F_BIG, fill=C_FG, anchor="mm")
    d.text((70, 247), L("Schläge/min"), font=F_TINY, fill=C_DIM, anchor="mm")
    d.text((170, 222), f"{s.get('watts', 0)}", font=F_BIG, fill=C_FG, anchor="mm")
    d.text((170, 247), L("Watt"), font=F_TINY, fill=C_DIM, anchor="mm")
    return img


def draw_curve(d, curve, box, color, width=3):
    x0, y0, x1, y1 = box
    if not curve or len(curve) < 2:
        return
    pts = [(x0 + i * (x1 - x0) / (len(curve) - 1), y1 - v / 100 * (y1 - y0)) for i, v in enumerate(curve)]
    d.line(pts, fill=color, width=width, joint="curve")


def fmt_s(x):
    out = f"{x:.2f}"
    return out.replace(".", ",") if get_lang() == "de" else out


def draw_force(s, last, batt, plug):
    """Kraftkurve: live der letzte Zug, sonst der Durchschnitt der letzten Einheit."""
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    live = s.get("curve")
    curve = live or (last or {}).get("curve")
    header(d, L("Kraftkurve") if live else L("Letzte Einheit"), batt, plug)
    if not curve:
        for i, line in enumerate(wrap(d, L("Noch keine Kraftkurve. Sie erscheint nach dem ersten Zug."), F_SMALL, 200)):
            d.text((W // 2, 130 + i * 22), line, font=F_SMALL, fill=C_DIM, anchor="mm")
        return img
    box = (20, 72, 220, 182)
    d.line((box[0], box[3], box[2], box[3]), fill=(50, 55, 62), width=1)
    draw_curve(d, curve, box, (224, 102, 43))
    drive = s.get("drive") if live else (last or {}).get("drive")
    recover = s.get("recover") if live else (last or {}).get("recover")
    if drive:
        d.text((70, 204), fmt_s(drive), font=F_MED, fill=C_FG, anchor="mm")
        d.text((70, 225), L("Zug (s)"), font=F_TINY, fill=C_DIM, anchor="mm")
    if recover:
        d.text((170, 204), fmt_s(recover), font=F_MED, fill=C_FG, anchor="mm")
        d.text((170, 225), L("Rollphase (s)"), font=F_TINY, fill=C_DIM, anchor="mm")
    if drive and recover:
        ratio = f"1 : {recover / drive:.1f}"
        d.text((W // 2, 246), ratio.replace(".", ",") if get_lang() == "de" else ratio,
               font=F_TINY, fill=C_DIM, anchor="mm")
    return img


def fmt_m(m):
    return fmt_num(m) + " m"


def read_history(n=5):
    try:
        with open(LOGBOOK) as f:
            lines = f.read().splitlines()[-n:]
        return [json.loads(x) for x in reversed(lines)]
    except (OSError, ValueError):
        return []


def draw_summary(last, batt, plug, recent):
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    header(d, L("Geschafft") if recent else L("Letzte Einheit"), batt, plug)
    if not last:
        d.text((W // 2, 140), L("Noch keine Einheit"), font=F_MED, fill=C_DIM, anchor="mm")
        return img
    when = fmt_date(last.get("start", ""))
    d.text((W // 2, 76), when, font=F_TINY, fill=C_DIM, anchor="mm")
    d.text((W // 2, 112), fmt_m(last["dist"]), font=F_BIG, fill=C_FG, anchor="mm")
    d.text((W // 2, 146), mmss(last["dur"]), font=F_BIG, fill=C_FG, anchor="mm")
    cells = [(70, 186, mmss(last.get("pace", 0)), "/500 m"),
             (170, 186, str(last.get("watts", 0)), L("Ø Watt")),
             (70, 226, str(round(last.get("spm", 0))), L("Ø Schläge/min")),
             (170, 226, str(last.get("strokes", 0)), L("Schläge"))]
    for x, y, val, lab in cells:
        d.text((x, y), val, font=F_MED, fill=C_FG, anchor="mm")
        d.text((x, y + 20), lab, font=F_TINY, fill=C_DIM, anchor="mm")
    return img


def draw_history(rows, batt, plug):
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    header(d, L("Verlauf"), batt, plug)
    if not rows:
        d.text((W // 2, 140), L("Noch keine Einheit"), font=F_MED, fill=C_DIM, anchor="mm")
        return img
    y = 80
    for r in rows:
        d.text((22, y), fmt_date(r.get("start", ""), with_time=False), font=F_SMALL, fill=C_DIM)
        d.text((150, y), fmt_m(r["dist"]), font=F_SMALL, fill=C_FG, anchor="ra")
        d.text((220, y), mmss(r["dur"]), font=F_SMALL, fill=C_FG, anchor="ra")
        y += 34
    return img


def progress_bar(d, y, frac, x0=24, x1=216):
    d.rounded_rectangle((x0, y, x1, y + 10), radius=5, fill=(45, 50, 58))
    if frac > 0:
        d.rounded_rectangle((x0, y, x0 + max(10, (x1 - x0) * min(frac, 1)), y + 10), radius=5,
                            fill=(255, 90, 31))


def live_speed(s):
    """Aktuelles Tempo in km/h aus der Pace, nur beim Rudern."""
    pace = s.get("pace") or 0
    return 1800 / pace if s.get("active") and pace > 0 else None


def eta_text(value, mode):
    if value in (None, "–"):
        return "–"
    return L("an {t}", t=value) if mode == "time" else L("ca. {t}", t=value)


def draw_journey(batt, plug, s=None):
    """Reise als Navigation: naechste Etappe oben, Strecke in der Mitte, Endziel unten."""
    s = s or {}
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    game = rowgame.load_json(rowgame.GAME, {})
    st = rowgame.status(rowgame.load_sessions(LOGBOOK), game, get_lang(),
                        extra_km=(s.get("session_m") or 0) / 1000, speed_kmh=live_speed(s))
    live = bool(live_speed(s))
    d.text((W // 2, 16), st["river"], font=F_TINY, fill=C_DIM, anchor="mm")
    # Naechste Etappe
    d.rounded_rectangle((8, 30, 232, 128), radius=12, fill=(32, 36, 44))
    if st["done"]:
        d.text((W // 2, 66), st["goal"], font=F_BIG, fill=C_FG, anchor="mm")
        d.text((W // 2, 100), L("Ziel erreicht"), font=F_MED, fill=C_OK, anchor="mm")
    else:
        d.text((22, 46), L("Nächste Etappe"), font=F_TINY, fill=C_DIM, anchor="lm")
        name = st["next"]
        fnt = next((f for f in (F_BIG, F_MED, F_SMALL) if d.textlength(name, font=f) <= 200), F_TINY)
        d.text((22, 76), name, font=fnt, fill=C_FG, anchor="lm")
        mode_next = "time" if live and ":" in st["eta_next"] else "date"
        dist, when = f"{fmt_dec(st['next_left'])} km", eta_text(st["eta_next"], mode_next)
        rf = next((f for f in (F_MED, F_SMALL) if d.textlength(dist, font=f) + d.textlength(when, font=f) <= 186), F_TINY)
        d.text((22, 108), dist, font=rf, fill=C_FG, anchor="lm")
        d.text((218, 108), when, font=rf, fill=(255, 90, 31), anchor="rm")
    # Strecke als Linie mit Orten und Boot
    x0, x1, y = 20, 220, 152
    d.line((x0, y, x1, y), fill=(60, 66, 76), width=4)
    frac = st["km"] / st["total"]
    d.line((x0, y, x0 + (x1 - x0) * frac, y), fill=(255, 90, 31), width=4)
    for k, _ in st["stops"]:
        x = x0 + (x1 - x0) * k / st["total"]
        done = k <= st["km"]
        d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(255, 90, 31) if done else (60, 66, 76))
    bx = x0 + (x1 - x0) * frac
    d.polygon([(bx - 7, y - 16), (bx + 7, y - 16), (bx, y - 6)], fill=C_FG)   # Boot
    # Ziel
    if st["done"]:
        d.text((W // 2, 196), L("{km} km gerudert", km=fmt_num(st["total"])), font=F_SMALL, fill=C_FG, anchor="mm")
        for i, line in enumerate(wrap(d, L("Neue Strecke im Portal unter Einstellungen wählen."), F_TINY, 200)[:2]):
            d.text((W // 2, 224 + i * 18), line, font=F_TINY, fill=C_DIM, anchor="mm")
        return img
    d.text((22, 186), L("Ziel"), font=F_TINY, fill=C_DIM, anchor="lm")
    left = f"{fmt_dec(st['left'])} km"
    mode_goal = "time" if live and ":" in st["eta_goal"] else "date"
    room = 218 - d.textlength(left, font=F_SMALL) - 18 - 22
    gfont = next((f for f in (F_MED, F_SMALL) if d.textlength(st["goal"], font=f) <= room), None)
    if gfont:                                   # Ziel und Entfernung in einer Zeile
        d.text((22, 210), st["goal"], font=gfont, fill=C_FG, anchor="lm")
        d.text((218, 210), left, font=F_SMALL, fill=C_FG, anchor="rm")
        d.text((22, 238), eta_text(st["eta_goal"], mode_goal), font=F_SMALL, fill=C_DIM, anchor="lm")
    else:                                       # langer Name: Entfernung in die Zeile darunter
        d.text((22, 210), st["goal"], font=F_MED if d.textlength(st["goal"], font=F_MED) <= 196 else F_SMALL,
               fill=C_FG, anchor="lm")
        when = eta_text(st["eta_goal"], mode_goal)
        rf = F_SMALL if d.textlength(when, font=F_SMALL) + d.textlength(left, font=F_SMALL) <= 180 else F_TINY
        d.text((22, 238), when, font=rf, fill=C_DIM, anchor="lm")
        d.text((218, 238), left, font=rf, fill=C_FG, anchor="rm")
    return img


def fmt_dec(x):
    if x >= 1000:
        return fmt_num(x)
    out = f"{x:.1f}" if x < 10 else f"{x:.0f}"
    return out.replace(".", ",") if get_lang() == "de" else out


def draw_medal(d, cx, cy, r):
    d.polygon([(cx - 22, cy - r - 30), (cx - 4, cy - r - 30), (cx + 8, cy - r + 6), (cx - 10, cy - r + 6)],
              fill=(255, 90, 31))
    d.polygon([(cx + 22, cy - r - 30), (cx + 4, cy - r - 30), (cx - 8, cy - r + 6), (cx + 10, cy - r + 6)],
              fill=(200, 60, 15))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 90, 31))
    d.ellipse((cx - r + 7, cy - r + 7, cx + r - 7, cy + r - 7), outline=(0, 0, 0), width=3)


def draw_trophy(tid):
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 24), L("Neue Trophäe"), font=F_TINY, fill=C_DIM, anchor="mm")
    draw_medal(d, W // 2, 108, 40)
    name, desc = rowgame.trophy_text(tid, get_lang())
    d.text((W // 2, 176), name, font=F_MED, fill=C_FG, anchor="mm")
    for i, line in enumerate(wrap(d, desc, F_TINY, 200)[:3]):
        d.text((W // 2, 204 + i * 18), line, font=F_TINY, fill=C_DIM, anchor="mm")
    return img


def draw_stop(name, ev):
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    route = rowgame.ROUTES.get(ev.get("route"), rowgame.ROUTES[rowgame.DEFAULT_ROUTE])
    d.text((W // 2, 24), rowgame.txt(route["name"], get_lang()).split(":")[0], font=F_TINY, fill=C_DIM, anchor="mm")
    final = ev.get("done") and name == route["stops"][-1][1]
    d.text((W // 2, 96), rowgame.txt(name, get_lang()), font=F_BIG, fill=C_FG, anchor="mm")
    d.text((W // 2, 130), L("Ziel erreicht") if final else L("erreicht"), font=F_MED,
           fill=C_OK if final else C_DIM, anchor="mm")
    progress_bar(d, 168, ev.get("km", 0) / max(1, ev.get("total", 1)))
    d.text((W // 2, 198), L("{km} von {total} km", km=fmt_num(ev.get("km", 0)), total=fmt_num(ev.get("total", 0))),
           font=F_TINY, fill=C_DIM, anchor="mm")
    return img


def draw_hold(remaining, hotspot_on):
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 110), L("Hotspot schließen?") if hotspot_on else L("Hotspot öffnen?"),
           font=F_MED, fill=C_FG, anchor="mm")
    d.text((W // 2, 150), L("weiter halten: {n}", n=f"{max(remaining, 0):.0f}"), font=F_SMALL,
           fill=C_DIM, anchor="mm")
    d.text((W // 2, 180), L("loslassen = abbrechen"), font=F_TINY, fill=C_DIM, anchor="mm")
    return img


def wrap(d, text, fnt, width):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=fnt) <= width:
            cur = t
        else:
            lines.append(cur); cur = w
    return lines + [cur] if cur else lines


def draw_message(text):
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    lines = wrap(d, text, F_MED, 200)
    y = 140 - 14 * (len(lines) - 1)
    for line in lines:
        d.text((W // 2, y), line, font=F_MED, fill=C_FG, anchor="mm")
        y += 28
    return img


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def qr_image(text, size):
    try:
        import qrcode
    except ImportError:
        return None
    q = qrcode.QRCode(border=1, box_size=1)
    q.add_data(text)
    q.make(fit=True)
    img = q.make_image(fill_color="black", back_color="white").convert("RGB")
    k = max(1, size // img.width)              # ganzzahlig skalieren, sonst verzerren Module
    return img.resize((img.width * k, img.height * k), Image.NEAREST)


def draw_hotspot(p, footer=None):
    """Hotspot aktiv: QR-Code zum Verbinden, darunter Name und Passwort."""
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    if p.get("failed"):
        d.text((W // 2, 24), L("Verbindung fehlgeschlagen"), font=F_TINY, fill=C_BAD, anchor="mm")
    else:
        d.text((W // 2, 24), L("WLAN einrichten"), font=F_MED, fill=C_FG, anchor="mm")
    ssid, pw = p.get("ssid", ""), p.get("password", "")
    qr = qr_image(f"WIFI:T:WPA;S:{ssid};P:{pw};;", 140)
    if qr:
        img.paste(qr, ((W - qr.width) // 2, 44 + (140 - qr.height) // 2))
        y = 196
    else:
        y = 70
    d.text((W // 2, y), ssid, font=F_SMALL, fill=C_FG, anchor="mm")
    d.text((W // 2, y + 22), L("Passwort: {pw}", pw=pw), font=F_TINY, fill=C_DIM, anchor="mm")
    if footer:
        d.text((W // 2, y + 44), L("QR scannen, Seite öffnet sich"), font=F_TINY, fill=C_DIM, anchor="mm")
        d.text((W // 2, 268), footer, font=F_TINY, fill=C_DIM, anchor="mm")
    else:
        d.text((W // 2, y + 44), L("QR-Code scannen,"), font=F_TINY, fill=C_DIM, anchor="mm")
        d.text((W // 2, y + 62), L("Seite öffnet sich"), font=F_TINY, fill=C_DIM, anchor="mm")
    return img


def draw_info(batt, plug, ssid, ip):
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    header(d, L("Info"), batt, plug)
    lines = [(L("Webseite"), f"http://{HOST}"), (L("WLAN"), ssid or L("keins")), ("IP", ip or "-"),
             (L("Passwort"), read_json(CONF).get("password", "-"))]
    y = 85
    for k, v in lines:
        d.text((24, y), k, font=F_SMALL, fill=C_DIM)
        d.text((24, y + 20), str(v)[:20], font=F_MED, fill=C_FG)
        y += 48
    return img


# ---------- Toene ----------

def make_sounds():
    """Kurze, leise Toene als WAV-Dateien erzeugen."""
    import wave
    os.makedirs(SOUND_DIR, exist_ok=True)
    rate = 48000

    def tone(name, notes):
        parts = []
        for freq, dur in notes:
            t = np.arange(int(rate * dur)) / rate
            env = np.minimum(1, np.minimum(t / 0.01, (dur - t) / 0.04))   # weich ein/aus
            parts.append(0.25 * np.sin(2 * np.pi * freq * t) * env)
        data = (np.concatenate(parts) * 32767).astype("<i2")
        with wave.open(os.path.join(SOUND_DIR, name + ".wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(data.tobytes())

    tone("connect", [(660, 0.09), (880, 0.14)])
    tone("disconnect", [(660, 0.09), (440, 0.14)])
    tone("saved", [(523, 0.08), (659, 0.08), (784, 0.16)])
    tone("trophy", [(523, 0.07), (659, 0.07), (784, 0.07), (1047, 0.22)])


def play(name):
    path = os.path.join(SOUND_DIR, name + ".wav")
    if not os.path.exists(path):
        return
    for dev in ("whisplaysound", "default"):
        try:
            subprocess.Popen(["aplay", "-q", "-D", dev, path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue


# ---------- Einrichtungsassistent ----------

class Wizard:
    """Merkt sich, welche Schritte erledigt oder uebersprungen sind."""
    def __init__(self):
        self.data = read_json(ONBOARD) or {"done": False, "ok": [], "skipped": []}
        self.finished_at = None

    def save(self):
        try:
            os.makedirs(os.path.dirname(ONBOARD), exist_ok=True)
            with open(ONBOARD + ".tmp", "w") as f:
                json.dump(self.data, f)
            os.replace(ONBOARD + ".tmp", ONBOARD)
        except OSError:
            pass

    @property
    def active(self):
        return not self.data.get("done")

    def update(self, s, ssid, portal):
        """Erfuellte Bedingungen abhaken. Gibt den aktuellen Schritt zurueck."""
        if not self.active:
            return None
        met = {
            "lang": lang_chosen(),
            "wlan": bool(ssid) and portal.get("mode") not in ("hotspot", "connecting"),
            "portal": os.path.exists(PORTAL_SEEN),
            "s4": bool(s.get("s4")),
            "app": bool(s.get("app")),
            "row": bool(s.get("active")),
        }
        changed = False
        for k, v in met.items():
            if v and k not in self.data["ok"]:
                self.data["ok"].append(k)
                changed = True
        if "wlan" in self.data["skipped"] and "portal" not in self.data["skipped"] \
                and "portal" not in self.data["ok"]:
            self.data["skipped"].append("portal")     # ohne WLAN kein Portal
            changed = True
        step = self.current()
        if step is None:
            if self.finished_at is None:
                self.finished_at = time.time()
            elif time.time() - self.finished_at > 5:
                self.data["done"] = True
                changed = True
        if changed:
            self.save()
        return step

    def current(self):
        for k in STEPS:
            if k not in self.data["ok"] and k not in self.data["skipped"]:
                return k
        return None

    def skip(self):
        step = self.current()
        if step:
            self.data["skipped"].append(step)
            self.save()

    def marks(self):
        return [("ok" if k in self.data["ok"] else "skip" if k in self.data["skipped"] else "open")
                for k in STEPS]


def wizard_frame(step, marks, footer=True):
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    n = STEPS.index(step) + 1 if step in STEPS else len(STEPS)
    d.text((W // 2, 18), L("Einrichtung {n}/{total}", n=n, total=len(STEPS)) if step else L("Einrichtung"),
           font=F_TINY, fill=C_DIM, anchor="mm")
    x0 = W // 2 - (len(marks) - 1) * 11
    for i, m in enumerate(marks):                 # Fortschrittspunkte
        col = C_OK if m == "ok" else C_DIM if m == "skip" else (C_FG if STEPS[i] == step else (50, 55, 62))
        d.ellipse((x0 + i * 22 - 4, 34, x0 + i * 22 + 4, 42), fill=col)
    if footer and step:
        d.text((W // 2, 266), L("Taste: überspringen"), font=F_TINY, fill=C_DIM, anchor="mm")
    return img, d


def text_block(d, y, text, fnt=F_SMALL, col=C_DIM, width=210, gap=20):
    for line in wrap(d, text, fnt, width):
        d.text((W // 2, y), line, font=fnt, fill=col, anchor="mm")
        y += gap
    return y


def draw_language(choice, remaining, marks):
    """Erster Schritt: Sprache waehlen. Kurz druecken wechselt, nach ein paar Sekunden gilt sie."""
    img, d = wizard_frame("lang", marks, footer=False)
    d.text((W // 2, 70), "Sprache · Language", font=F_MED, fill=C_FG, anchor="mm")
    y = 112
    for code, name in LANGS.items():
        on = code == choice
        if on:
            d.rounded_rectangle((40, y - 20, 200, y + 20), radius=10, fill=(38, 43, 53))
        d.text((W // 2, y), name, font=F_MED, fill=C_FG if on else C_DIM, anchor="mm")
        y += 50
    d.text((W // 2, 226), L("Kurz drücken: andere Sprache", lang=choice), font=F_TINY, fill=C_DIM,
           anchor="mm")
    d.text((W // 2, 252), L("Weiter in {n} s", lang=choice, n=max(0, round(remaining))), font=F_TINY,
           fill=C_DIM, anchor="mm")
    return img


def draw_wizard(step, marks, s, portal, login_url, host_url, password):
    if step == "wlan" and portal.get("mode") == "hotspot":
        return draw_hotspot(portal, footer=L("Taste: ohne WLAN weiter"))
    if step == "wlan" and portal.get("mode") == "connecting":
        return draw_message(portal.get("message") or L("Verbinde ..."))
    img, d = wizard_frame(step, marks)
    if step == "wlan":
        d.text((W // 2, 84), L("WLAN"), font=F_BIG, fill=C_FG, anchor="mm")
        text_block(d, 122, L("Suche bekannte Netze. Sonst erscheint gleich ein QR-Code."))
        text_block(d, 232, L("Zum Rudern ist kein WLAN nötig."), F_TINY, C_DIM, 236)
    elif step == "portal":
        d.text((W // 2, 62), L("Webportal öffnen"), font=F_MED, fill=C_FG, anchor="mm")
        qr = qr_image(login_url, 130) if login_url else None
        if qr:
            img.paste(qr, ((W - qr.width) // 2, 78 + (130 - qr.height) // 2))
        text_block(d, 222, L("oder {url}", url=host_url), F_TINY, C_DIM)
        text_block(d, 242, L("Passwort: {pw}", pw=password), F_TINY, C_DIM)
    elif step == "s4":
        d.text((W // 2, 80), L("S4 anschließen"), font=F_MED, fill=C_FG, anchor="mm")
        y = text_block(d, 118, L("1. Monitor einschalten"))
        y = text_block(d, y + 6, L("2. Kabel in den inneren Micro-USB-Anschluss der Box (beschriftet „USB“)"))
        text_block(d, y + 10, L("Der Schritt hakt sich von selbst ab."), F_TINY)
    elif step == "app":
        d.text((W // 2, 60), L("App verbinden"), font=F_MED, fill=C_FG, anchor="mm")
        qr = qr_image(APP_URL, 100)
        if qr:
            img.paste(qr, ((W - qr.width) // 2, 76))
        text_block(d, 196, L("In der App „WaterRower S4“ wählen, nicht in den Bluetooth-Einstellungen."),
                   F_TINY, C_DIM, 226, 18)
    elif step == "row":
        d.text((W // 2, 80), L("Probeschlag"), font=F_MED, fill=C_FG, anchor="mm")
        text_block(d, 120, L("Zieh ein paar Schläge am Rudergerät."))
        d.text((W // 2, 190), f"{s.get('dist', 0)} m", font=F_BIG, fill=C_FG, anchor="mm")
    else:
        d.text((W // 2, 110), L("Alles bereit"), font=F_BIG, fill=C_OK, anchor="mm")
        text_block(d, 150, L("Viel Spaß beim Rudern."))
        skipped = [k for k, m in zip(STEPS, marks) if m == "skip"]
        if skipped:
            text_block(d, 200, L("Übersprungenes lässt sich im Webportal unter Einstellungen nachholen."),
                       F_TINY)
    return img


class Display:
    def __init__(self):
        sys.path.insert(0, RUNTIME)
        from whisplay import WhisplayBoard
        self.b = WhisplayBoard()
        self.b.set_backlight(100)
        self.view = 0
        self.awake = True
        self.last_event = time.time()
        self.pressed = threading.Event()      # kurzer Druck erkannt
        self.press_t = None                   # Zeitpunkt, seit dem die Taste gedrueckt ist
        self.b.on_button_press(self._down)
        self.b.on_button_release(self._up)
        self.last_frame = None
        self.key = None
        self.wizard = Wizard()
        self.lang_choice, self.lang_t = get_lang(), None   # Sprachwahl im Assistenten
        self.pacer = Pacer()
        self.wave_ping = 0.0
        self.ev_seen = read_json(rowgame.EVENT).get("ended")   # alte Ereignisse nicht wiederholen
        self.prev = {}                        # fuer Toene bei Zustandswechseln
        try:
            make_sounds()
        except Exception:                     # Toene sind verzichtbar
            pass

    def _down(self):
        self.press_t = time.time()

    def _up(self):
        t, self.press_t = self.press_t, None
        if t and time.time() - t < HOLD_WARN:
            self.pressed.set()
        self.last_frame = None                # Halten-Hinweis wieder ueberzeichnen

    def show(self, img):
        self.b.draw_image(0, 0, W, H, to_rgb565(img))

    def game_events(self):
        """Nach einer Einheit: neue Trophaeen und erreichte Orte je ein paar Sekunden zeigen."""
        ev = read_json(rowgame.EVENT)
        if not ev.get("ended") or ev["ended"] == self.ev_seen:
            return
        self.ev_seen = ev["ended"]
        if not getattr(self, "game_on", True):
            return
        screens = [draw_trophy(t) for t in ev.get("trophies", []) if t in rowgame.TROPHIES]
        screens += [draw_stop(n, ev) for n in ev.get("stops", [])]
        if not screens:
            return
        self.wake()
        if getattr(self, "sound", True):
            play("trophy")
        for img in screens:
            self.show(img)
            t = time.time()
            while time.time() - t < 6 and not self.pressed.is_set():
                time.sleep(0.1)
            self.pressed.clear()
        self.last_frame = None

    def show_region(self, img, y):
        self.b.draw_image(0, y, W, img.height, to_rgb565(img))

    def strip(self, kind, h, big):
        """Animationsstreifen zeichnen: Taktgeber oder Live-Kurve."""
        if time.time() - self.wave_ping > 2:   # Bruecke wissen lassen, dass wir Daten brauchen
            try:
                with open(WAVE_ON, "w"):
                    pass
            except OSError:
                pass
            self.wave_ping = time.time()
        wave = read_json(WAVE)
        if time.time() - wave.get("ts", 0) > 2:
            wave = {}
        if kind == "pacer":
            self.pacer.update(wave, getattr(self, "pacer_spm", 0))
            return draw_pacer(self.pacer, h, big, getattr(self, "pacer_spm", 0))
        return draw_wave(wave, h)

    def wake(self):
        self.last_event = time.time()
        if not self.awake:
            self.awake = True
            self.b.set_backlight(100)
            self.last_frame = None

    def sleep(self):
        if self.awake:
            self.awake = False
            self.b.set_backlight(0)

    def settings(self):
        c = read_json(CONF)
        self.led_mode = os.environ.get("ROWBOX_LED") or c.get("led", "warn")
        self.timeout = int(c.get("timeout", TIMEOUT))
        self.summary_for = int(c.get("summary", SUMMARY_FOR))
        self.sound = bool(c.get("sound", True))
        views = c.get("views")
        self.views_on = set(views) | {"auto", "info"} if views else set(VIEWS)
        self.game_on = bool(c.get("game", True))
        if not self.game_on:
            self.views_on.discard("journey")
        if VIEWS[self.view] not in self.views_on:
            self.view = 0                     # gewaehlte Ansicht wurde abgeschaltet
        self.anim_mode = c.get("anim", "pacer") if c.get("anim", "pacer") in ("pacer", "wave", "off") else "pacer"
        try:
            self.pacer_spm = max(0, min(40, int(c.get("pacer_spm", 0))))
        except (TypeError, ValueError):
            self.pacer_spm = 0
        if not os.path.exists(ONBOARD) and not self.wizard.active:
            self.wizard = Wizard()            # im Portal neu gestartet

    def sounds(self, s):
        """Toene bei Verbinden/Trennen von S4 und App und beim Speichern einer Einheit."""
        cur = {"s4": bool(s.get("s4")), "app": bool(s.get("app")),
               "last": (s.get("last") or {}).get("ended")}
        if self.prev and getattr(self, "sound", True):
            if (cur["s4"] and not self.prev["s4"]) or (cur["app"] and not self.prev["app"]):
                play("connect")
            elif self.prev["app"] and not cur["app"]:
                play("disconnect")
            elif cur["last"] and cur["last"] != self.prev["last"]:
                play("saved")
        self.prev = cur

    def led(self, s, batt):
        """Dauerlicht ohne Dimmen. Im Modus "warn" nur bei Akku oder Stoerung."""
        warn = None
        if batt is not None and batt <= LOW_BATT:
            warn = (255, 0, 0)
        elif not s["fresh"]:
            warn = (255, 255, 0)
        mode = getattr(self, "led_mode", LED_MODE)
        if mode == "off":
            rgb = (0, 0, 0)
        elif warn:
            rgb = warn
        elif mode == "on" and self.awake:
            rgb = (0, 255, 0) if s.get("app") else (0, 0, 255)
        else:
            rgb = (0, 0, 0)
        if rgb != getattr(self, "_rgb", None):
            self.b.set_rgb(*rgb)
            self._rgb = rgb

    def run(self):
        batt, plug, ssid, ip = None, False, None, None
        t_slow = 0
        while True:
            now = time.time()
            if now - t_slow > 10:
                batt, plug = battery()
                ssid, ip = wifi()
                self.settings()
                t_slow = now
            portal = read_json(PORTAL)
            s = read_state()

            # Obere PiSugar-Taste: Statusanzeige oder Sprache wechseln
            if os.path.exists(KEY_REQ):
                try:
                    with open(KEY_REQ) as f:
                        req = f.read().strip()
                    os.remove(KEY_REQ)
                except OSError:
                    req = ""
                if req == "status":
                    self.view = VIEWS.index("status")
                    self.last_frame = None
                    self.wake()
                elif req == "lang":
                    langs = list(LANGS)
                    new = langs[(langs.index(get_lang()) + 1) % len(langs)]
                    try:
                        set_lang(new)
                    except OSError:
                        pass
                    self.lang_choice = new
                    self.wake()
                    self.show(draw_message(LANGS[new]))
                    self.last_frame = None
                    time.sleep(1.5)
                    continue

            # Taste wird gehalten: Hinweis, nach HOLD_ACT Hotspot oeffnen bzw. schliessen
            if self.press_t:
                held = now - self.press_t
                hotspot_on = portal.get("mode") == "hotspot"
                if held >= HOLD_ACT:
                    self.press_t = None
                    try:
                        with open(HOTSPOT_REQ, "w") as f:
                            f.write("stop" if hotspot_on else "start")
                    except OSError:
                        pass
                    self.view = 0                 # Automatik zeigt dann den QR-Code
                    self.show(draw_message(L("Hotspot wird geschlossen") if hotspot_on
                                           else L("Hotspot wird geöffnet")))
                    self.last_frame = None
                    time.sleep(2)
                    continue
                if held >= HOLD_WARN:
                    self.wake()
                    self.show(draw_hold(HOLD_ACT - held, hotspot_on))
                    time.sleep(0.2)
                    continue

            if s["fresh"]:
                self.sounds(s)
                self.game_events()
            step = self.wizard.update(s, ssid, portal)
            wizard_on = self.wizard.active
            last = s.get("last") or None
            recent = bool(last) and now - last.get("ended", 0) < getattr(self, "summary_for", SUMMARY_FOR)
            key = (s.get("s4"), s.get("app"), s.get("active"), s["fresh"],
                   last.get("ended") if last else None, portal.get("mode"), portal.get("message"))
            if key != self.key or s.get("active"):
                self.key = key
                self.wake()
            if self.pressed.is_set():
                self.pressed.clear()
                if self.awake and portal.get("mode") == "hotspot":
                    try:                              # Hotspot weggedrueckt: trotzdem bekannte Netze probieren
                        with open(HOTSPOT_REQ, "w") as f:
                            f.write("retry")
                    except OSError:
                        pass
                if self.awake:
                    if wizard_on and self.wizard.current() == "lang":
                        langs = list(LANGS)               # Sprache wechseln, Wartezeit neu
                        self.lang_choice = langs[(langs.index(self.lang_choice) + 1) % len(langs)]
                        self.lang_t = now
                    elif wizard_on:
                        self.wizard.skip()
                    else:
                        on = getattr(self, "views_on", set(VIEWS))
                        for _ in VIEWS:           # naechste eingeschaltete Ansicht
                            self.view = (self.view + 1) % len(VIEWS)
                            if VIEWS[self.view] in on:
                                break
                self.wake()
            hotspot = portal.get("mode") in ("hotspot", "connecting")
            if (self.awake and now - self.last_event > getattr(self, "timeout", TIMEOUT)
                    and not hotspot and not wizard_on):
                self.sleep()

            self.led(s, batt)

            if self.awake and wizard_on:
                tok = read_json(LOGIN)
                url_host = f"http://{ip}" if ip else f"http://{HOST}"
                login_url = f"{url_host}/login?t={tok['token']}" if tok.get("token") else None
                cur_step = self.wizard.current()
                if cur_step == "lang":
                    if self.lang_t is None:
                        self.lang_t = now
                    remaining = LANG_WAIT - (now - self.lang_t)
                    if remaining <= 0:
                        try:
                            set_lang(self.lang_choice)
                        except OSError:
                            pass
                        self.lang_t = None
                        continue
                    content = ("lang", self.lang_choice, int(remaining + 0.99))
                    if content != self.last_frame:
                        self.last_frame = content
                        self.show(draw_language(self.lang_choice, remaining, self.wizard.marks()))
                    time.sleep(0.2)
                    continue
                content = ("wizard", cur_step, tuple(self.wizard.marks()), portal.get("mode"),
                           portal.get("message"), login_url, s.get("dist") if cur_step == "row" else 0,
                           get_lang())
                if content != self.last_frame:
                    self.last_frame = content
                    self.show(draw_wizard(cur_step, self.wizard.marks(), s, portal, login_url,
                                          f"http://{HOST}", read_json(CONF).get("password", "-")))
                time.sleep(0.5)
                continue

            if self.awake:
                view = VIEWS[self.view]
                if view == "auto":
                    if hotspot and not s.get("active"):
                        view = "hotspot"
                    elif s.get("active") or s.get("session"):
                        view = "training"          # auch in kurzen Pausen, mit Countdown
                    elif recent:
                        view = "summary"
                    elif s.get("app") and s.get("dist"):
                        view = "training"
                    else:
                        view = "status"
                content = (view, self.view, get_lang(), batt, plug, ssid, ip, s["fresh"], recent,
                           portal.get("mode"), portal.get("message"), portal.get("password"),
                           last.get("ended") if last else None,
                           *(s.get(k) for k in ("s4", "app", "active", "dist", "strokes",
                                                "spm", "watts", "elapsed", "pause_left", "session",
                                                "stroke_n", "recover")))
                # Animation: eigene Ansicht, oder Streifen in der Trainingsansicht beim Rudern
                anim = big = None
                if view in ("pacer", "wave"):
                    anim, (sy, sh), big = view, ANIM_STRIP, True
                elif (view == "training" and s.get("active")
                      and getattr(self, "anim_mode", "pacer") != "off"):
                    anim, (sy, sh), big = self.anim_mode, TRAIN_STRIP, False
                if content == self.last_frame:
                    if anim:                          # nur den Streifen neu zeichnen
                        self.show_region(self.strip(anim, sh, big), sy)
                        time.sleep(1 / ANIM_FPS)
                    else:
                        time.sleep(0.5)
                    continue
                self.last_frame = content
                if view == "hotspot":
                    img = (draw_message(portal.get("message") or "Verbinde ...")
                           if portal.get("mode") == "connecting" else draw_hotspot(portal))
                elif view == "training":
                    img = draw_training_compact(s, batt, plug) if anim else draw_training(s, batt, plug)
                elif view in ("pacer", "wave"):
                    img = draw_anim_frame(L(LABEL[view]), batt, plug)
                elif view == "force":
                    img = draw_force(s, last, batt, plug)
                elif view == "summary":
                    img = draw_summary(last, batt, plug, recent)
                elif view == "history":
                    img = draw_history(read_history(), batt, plug)
                elif view == "info":
                    img = draw_info(batt, plug, ssid, ip)
                elif view == "journey":
                    img = draw_journey(batt, plug, s)
                else:
                    img = draw_status(s, batt, plug, ssid, portal.get("mode") == "hotspot")
                if anim:
                    img.paste(self.strip(anim, sh, big), (0, sy))
                if self.view and view not in ("pacer", "wave"):  # manuelle Ansicht kennzeichnen
                    ImageDraw.Draw(img).text((W // 2, 272), L(LABEL[VIEWS[self.view]]), font=F_TINY,
                                             fill=C_DIM, anchor="mb")
                self.show(img)
            time.sleep(1 / ANIM_FPS if self.awake and VIEWS[self.view] in ("pacer", "wave") else 0.5)


if __name__ == "__main__":
    try:
        Display().run()
    except KeyboardInterrupt:
        pass
