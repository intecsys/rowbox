#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 The Rowbox authors
"""rowbridge: WaterRower S4 (USB) -> Bluetooth FTMS Rudergeraet, mit Logbuch."""
import json, os, queue, struct, sys, threading, time
from collections import deque
import dbus, dbus.service, dbus.mainloop.glib, serial
from gi.repository import GLib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # rowstroke.py liegt daneben
import rowstroke
import rowgame

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
NAME = "WaterRower S4"
VERSION = "2.1.1"
IDLE = 7                               # Sekunden ohne neuen Schlag = Pause
STATE = "/run/rowbridge/state.json"    # Zustand fuer die Anzeige (rowdisplay)
LOGBOOK = "/var/lib/rowbridge/sessions.jsonl"   # eine Zeile pro Einheit
TRACKS = "/var/lib/rowbridge/tracks"   # Messpunkte pro Sekunde, eine Datei pro Einheit
RAW_FLAG = "/var/lib/rowbridge/raw_on" # gibt es die Datei, werden S4-Rohmeldungen aufgezeichnet
WAVE = "/run/rowbridge/wave.json"      # Radtempo der letzten 6 s fuer Taktgeber und Live-Kurve
WAVE_ON = "/run/rowbridge/wave_on"     # das Display meldet so, dass es die Daten gerade braucht
WAVE_LEN = 240                         # Messwerte = 6 s bei 25 ms
RAW_DIR = "/var/lib/rowbridge/raw"
RAW_MAX = 50 * 1024 * 1024             # hoechstens so viel Rohdaten insgesamt
# Zusaetzliche Register nur fuer die Rohaufzeichnung (Einheit noch unklar):
# 142 = durchschnittliche Dauer eines Schlags, 143 = davon Durchzug
RAW_REG = {"142": "S", "143": "S"}
SESSION_END = 90                       # Sekunden Pause, dann gilt die Einheit als beendet
MIN_DUR, MIN_DIST = 60, 100            # kuerzere Einheiten werden nicht gespeichert
BLUEZ, ADAPTER = "org.bluez", "/org/bluez/hci0"
PROPS, OBJMGR = "org.freedesktop.DBus.Properties", "org.freedesktop.DBus.ObjectManager"
GCHAR = "org.bluez.GattCharacteristic1"
UUID = lambda short: f"0000{short}-0000-1000-8000-00805f9b34fb"
FTMS = UUID("1826")
DIS = UUID("180a")                     # Geraeteinformation (Hersteller, Modell, Version)

# S4-Register: Adresse -> (Name, Groesse, Zahlenbasis)
REG = {"055": ("dist", "D", 16), "140": ("strokes", "D", 16), "1A9": ("spm", "S", 16),
       "14A": ("cmps", "D", 16), "08A": ("cal", "T", 16),
       "1E1": ("sec", "S", 10), "1E2": ("min", "S", 10), "1E3": ("hr", "S", 10),
       "1A0": ("bpm", "D", 16)}       # Puls, nur mit Pulsempfaenger am S4, sonst 0
WATT = "088"                           # wird oefter abgefragt, steht nur kurz pro Schlag an
LEN = {"S": 2, "D": 4, "T": 6}
OPS = {0x00: "Kontrolle anfordern", 0x01: "Reset", 0x07: "Start/Fortsetzen", 0x08: "Stopp/Pause"}


def write_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass


def write_state(data):
    """Schreibt den aktuellen Zustand atomar fuer andere Programme (z.B. das Display)."""
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, STATE)
    except OSError:
        pass


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Logbook:
    """Erkennt Einheiten (erster Schlag bis 90 s Pause oder Reset) und speichert sie.

    Waehrend einer Einheit wird jede Sekunde ein Messpunkt gesammelt
    [Sekunde, Meter, Schlaege, Schlagzahl, Watt, cm/s, Puls] und alle 30 s in
    tracks/current.jsonl geschrieben. Wird die Einheit gespeichert, heisst die
    Datei danach tracks/<Startzeit>.jsonl; daraus macht das Portal FIT und TCX."""
    def __init__(self, path, tracks=TRACKS):
        self.path, self.cur, self.base = path, None, (0, 0)
        self.prev_dist = None
        self.tracks = tracks
        self.buf = []
        self.strokes = []                             # Schlaege der laufenden Einheit (Kraftkurven)
        self.last = self._load_last()
        self._discard_track()                         # Reste nach Absturz

    def _track_tmp(self):
        return os.path.join(self.tracks, "current.jsonl")

    def _flush(self):
        if not self.buf:
            return
        try:
            os.makedirs(self.tracks, exist_ok=True)
            with open(self._track_tmp(), "a") as f:
                f.write("".join(json.dumps(x) + "\n" for x in self.buf))
        except OSError as e:
            log(f"Messpunkte nicht gespeichert: {e}")
        self.buf = []

    def _discard_track(self):
        self.buf = []
        try:
            os.remove(self._track_tmp())
        except OSError:
            pass

    def _load_last(self):
        try:
            with open(self.path) as f:
                lines = f.read().splitlines()
            return json.loads(lines[-1]) if lines else None
        except (OSError, ValueError):
            return None

    def pause_left(self, now):
        """Sekunden bis zum automatischen Ende der Einheit, wenn gerade Pause ist."""
        c = self.cur
        if not c or now - c["last_active"] < 1:
            return None
        return max(0, round(SESSION_END - (now - c["last_active"])))

    def update(self, v, active, now, strokes=()):
        c = self.cur
        if c and v["dist"] < c["d1"]:                 # S4 wurde zurueckgesetzt
            self.close()
            c = None
        if v["dist"] < self.base[0]:                  # Reset ausserhalb einer Einheit
            self.base = (0, 0)
        if active:
            if not c:
                c = self.cur = {"start": now, "d0": self.base[0], "s0": self.base[1],
                                "c0": v.get("cal", 0),
                                "w_sum": 0, "w_n": 0, "w_max": 0, "h_sum": 0, "h_n": 0, "h_max": 0}
                self._discard_track()
                self.strokes = []
                log("Einheit beginnt")
            c.update(last_active=now, d1=v["dist"], s1=v["strokes"])
            if v["watts"] > 0:
                c["w_sum"] += v["watts"]; c["w_n"] += 1; c["w_max"] = max(c["w_max"], v["watts"])
            if 30 <= v.get("bpm", 0) <= 240:
                c["h_sum"] += v["bpm"]; c["h_n"] += 1; c["h_max"] = max(c["h_max"], v["bpm"])
            c["c1"] = v.get("cal", 0)
        else:
            if c:                                      # das Rad rollt aus und zaehlt weiter Meter
                c["d1"] = v["dist"]
            if c and now - c["last_active"] > SESSION_END:
                self.close()
            if not self.cur and (self.prev_dist is None or v["dist"] == self.prev_dist):
                self.base = (v["dist"], v["strokes"])   # nur im Stillstand: sonst fehlen die ersten Meter
        self.prev_dist = v["dist"]
        c = self.cur
        if c:
            for st in strokes:                          # Kraftkurven mit Zeit ab Start der Einheit
                self.strokes.append(dict(st, t=round(now - c["start"], 1)))
        if c:                                          # Messpunkt, auch in kurzen Pausen
            bpm = v.get("bpm", 0)
            self.buf.append([round(now - c["start"]), max(v["dist"] - c["d0"], 0),
                             max(v["strokes"] - c["s0"], 0), v["spm"] if active else 0,
                             v["watts"] if active else 0, v.get("cmps", 0) if active else 0,
                             bpm if 30 <= bpm <= 240 else 0])
            if len(self.buf) >= 30:
                self._flush()

    def close(self):
        c, self.cur = self.cur, None
        if not c:
            return
        dur = round(c["last_active"] - c["start"])
        dist = max(c["d1"] - c["d0"], 0)
        strokes = max(c["s1"] - c["s0"], 0)
        if dur < MIN_DUR or dist < MIN_DIST:
            log(f"Einheit verworfen ({dist} m, {dur} s)")
            self._discard_track()
            self.strokes = []
            return
        rec = {"start": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(c["start"])),
               "dur": dur, "dist": dist, "strokes": strokes,
               "spm": round(strokes / (dur / 60), 1),
               "watts": round(c["w_sum"] / c["w_n"]) if c["w_n"] else 0,
               "watts_max": c["w_max"],
               "pace": round(dur / dist * 500, 1), "ended": time.time(),
               "kcal": max(c.get("c1", c["c0"]) - c["c0"], 0) // 1000}
        if c["h_n"]:
            rec.update(hr=round(c["h_sum"] / c["h_n"]), hr_max=c["h_max"])
        # Messpunkte nach dem letzten Schlag abschneiden und Datei umbenennen
        self._flush()
        track = str(int(c["start"]))
        try:
            with open(self._track_tmp()) as f:
                pts = [json.loads(x) for x in f if x.strip()]
            with open(os.path.join(self.tracks, track + ".jsonl"), "w") as f:
                f.write("".join(json.dumps(p) + "\n" for p in pts if p[0] <= dur + 1))
            os.remove(self._track_tmp())
            rec["track"] = track
        except (OSError, ValueError) as e:
            log(f"Messpunkte nicht verfuegbar: {e}")
        # Kraftkurven: Durchschnitt ins Logbuch, alle Schlaege in eine eigene Datei
        sts, self.strokes = self.strokes, []
        if sts:
            rec["curve"] = rowstroke.average([x["curve"] for x in sts])
            rec["drive"] = round(sum(x["drive"] for x in sts) / len(sts), 2)
            recs = [x["recover"] for x in sts if x.get("recover")]
            if recs:
                rec["recover"] = round(sum(recs) / len(recs), 2)
            try:
                os.makedirs(self.tracks, exist_ok=True)
                with open(os.path.join(self.tracks, track + ".strokes.jsonl"), "w") as f:
                    f.write("".join(json.dumps(x) + "\n" for x in sts))
            except OSError as e:
                log(f"Kraftkurven nicht gespeichert: {e}")
        try:
            if os.path.dirname(self.path):
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError as e:
            log(f"Logbuch nicht gespeichert: {e}")
        self.last = rec
        log(f"Einheit gespeichert: {dist} m in {dur // 60}:{dur % 60:02d}")
        try:                                          # Reise und Trophaeen nachfuehren
            ev = rowgame.on_session(self.path, self.tracks, rec)
            if ev["trophies"] or ev["stops"]:
                log(f"Neu: Trophaeen {ev['trophies']}, Orte {ev['stops']}")
        except Exception as e:                         # darf das Logbuch nie stoeren
            log(f"Reise/Trophaeen nicht aktualisiert: {e}")


class S4(threading.Thread):
    """Fragt den Monitor zyklisch ab und haelt die letzten Werte in self.v."""
    def __init__(self, port):
        super().__init__(daemon=True)
        self.port, self.ok, self.cmds = port, False, queue.Queue()
        self.v = {n: 0 for n, _, _ in REG.values()} | {"watts": 0}
        self.stroke_at = 0.0
        self.strokes_seen = False
        self.raw, self.raw_check = None, 0.0   # offene Rohdaten-Datei
        self.analyzer = rowstroke.Analyzer()   # Kraftkurve aus den Sensorimpulsen
        self.p_at = 0.0                        # wann die letzte P-Meldung kam
        self.new_strokes = queue.Queue()       # fertige Schlaege fuer Logbuch und Anzeige
        self.last_stroke = None
        self.wave = deque(maxlen=WAVE_LEN)     # Impulse/s je 25 ms
        self.catches = deque(maxlen=12)        # Zeitpunkte (monotonic) der letzten Zuganfaenge

    def take_strokes(self):
        out = []
        while not self.new_strokes.empty():
            out.append(self.new_strokes.get())
        return out

    def raw_update(self):
        """Rohaufzeichnung je nach Schalter im Portal starten oder beenden (jede Sekunde)."""
        now = time.time()
        if now - self.raw_check < 1:
            return
        self.raw_check = now
        want = os.path.exists(RAW_FLAG)
        if want and not self.raw:
            try:
                os.makedirs(RAW_DIR, exist_ok=True)
                used = sum(os.path.getsize(os.path.join(RAW_DIR, n)) for n in os.listdir(RAW_DIR))
                if used > RAW_MAX:
                    log("Rohdaten: Speichergrenze erreicht, bitte alte Dateien im Portal loeschen")
                    os.remove(RAW_FLAG)
                    return
                name = time.strftime("s4-roh-%Y-%m-%d_%H-%M-%S.log")
                self.raw = open(os.path.join(RAW_DIR, name), "a", buffering=1)
                self.raw.write(f"# Rowbox {VERSION} S4-Rohdaten, Spalten: Millisekunden seit Start, Meldung\n")
                self.raw_t0 = time.monotonic()
                log(f"Rohdaten-Aufzeichnung an: {name}")
            except OSError as e:
                log(f"Rohdaten: {e}")
        elif not want and self.raw:
            self.raw.close()
            self.raw = None
            log("Rohdaten-Aufzeichnung aus")

    def read_lines(self, s):
        while s.in_waiting:
            line = s.readline().decode(errors="ignore").strip()
            if not line:
                continue
            if self.raw:
                try:
                    self.raw.write(f"{round((time.monotonic() - self.raw_t0) * 1000)} {line}\n")
                except (OSError, ValueError):
                    self.raw = None
            self.parse(line)

    def active(self):
        return time.time() - self.stroke_at < IDLE

    def reset(self):
        self.cmds.put("RESET")

    def parse(self, line):
        if len(line) == 3 and line[0] == "P":         # Pxx: Sensorimpulse der letzten 25 ms
            try:
                n = int(line[1:], 16)
            except ValueError:
                return
            now = time.monotonic()
            if now - self.p_at > 0.3:                  # Rad stand still oder Meldungen fehlten
                self.analyzer.reset()
            self.p_at = now
            self.wave.append(round(n / rowstroke.WIN))
            st = self.analyzer.add(n)
            if self.analyzer.catch is not None:
                self.catches.append(now - self.analyzer.catch)
                self.analyzer.catch = None
            if st:
                self.last_stroke = st
                self.new_strokes.put(st)
            return
        addr = line[3:6]
        if line[:2] != "ID" or (addr not in REG and addr != WATT):
            return
        name, size, base = REG.get(addr, ("watts", "D", 16))
        try:
            val = int(line[6:6 + LEN[size]], base)
        except ValueError:
            return
        if name == "watts":
            if val > 0:
                self.v["watts"] = val
        else:
            if name == "strokes" and val != self.v["strokes"]:
                # erster Wert nach dem Start ist nur der Zaehlerstand, kein neuer Schlag
                fresh = 0 < val - self.v["strokes"] <= 3 and self.strokes_seen
                self.stroke_at = time.time() if fresh else 0.0
            if name == "strokes":
                self.strokes_seen = True
            self.v[name] = val

    def run(self):
        while True:
            try:
                with serial.Serial(self.port, 19200, timeout=0.2) as s:
                    s.write(b"USB\r\n"); time.sleep(0.3)
                    self.ok = True
                    log(f"S4 verbunden an {self.port}")
                    while True:
                        while not self.cmds.empty():
                            cmd = self.cmds.get()
                            s.write(f"{cmd}\r\n".encode()); time.sleep(0.5)
                            log(f"An S4 gesendet: {cmd}")
                        self.raw_update()
                        regs = [(a, sz) for a, (_, sz, _) in REG.items()]
                        if self.raw:
                            regs += list(RAW_REG.items())
                        for addr, size in regs:
                            s.write(f"IR{size}{addr}\r\n".encode()); time.sleep(0.025)
                            s.write(f"IRD{WATT}\r\n".encode()); time.sleep(0.025)
                            self.read_lines(s)     # oft lesen, damit die Zeitstempel stimmen
            except (serial.SerialException, OSError) as e:
                if self.ok is not None:    # nur beim Zustandswechsel melden
                    log(f"S4 nicht erreichbar, neuer Versuch alle 3 s: {e}")
                self.ok = None
                time.sleep(3)


def rower_data(v, active):
    """FTMS Rower Data (0x2AD1), 19 Byte, mit Puls 20. In der Pause sind Schlagzahl, Pace, Watt null."""
    pace = min(round(50000 / v["cmps"]), 0xFFFF) if active and v["cmps"] > 0 else 0
    elapsed = v["hr"] * 3600 + v["min"] * 60 + v["sec"]
    bpm = v.get("bpm", 0)
    has_hr = 30 <= bpm <= 240                   # nur senden, wenn der S4 wirklich einen Puls hat
    data = struct.pack("<HBH", 0x092C | (0x0200 if has_hr else 0),
                       min(v["spm"] * 2, 255) if active else 0, v["strokes"] & 0xFFFF) \
        + (v["dist"] & 0xFFFFFF).to_bytes(3, "little") \
        + struct.pack("<HhHHB", pace, min(v["watts"], 32767) if active else 0,
                      min(v["cal"] // 1000, 0xFFFF), 0xFFFF, 0xFF)
    if has_hr:
        data += bytes([bpm])
    return data + struct.pack("<H", min(elapsed, 0xFFFF))


class Char(dbus.service.Object):
    def __init__(self, bus, service, short, flags, label, on_write=None):
        self.path = f"{service}/c{short}"
        super().__init__(bus, self.path)
        self.uuid, self.flags, self.service = UUID(short), flags, service
        self.label, self.on_write = label, on_write
        self.value, self.notifying = dbus.Array([], signature="y"), False

    def props(self):
        return {GCHAR: {"Service": dbus.ObjectPath(self.service), "UUID": self.uuid,
                        "Flags": dbus.Array(self.flags, signature="s")}}

    def set(self, data):
        self.value = dbus.Array([dbus.Byte(b) for b in data], signature="y")
        if self.notifying:
            self.PropertiesChanged(GCHAR, {"Value": self.value}, [])

    @dbus.service.method(GCHAR, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        return self.value

    @dbus.service.method(GCHAR, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        if value and self.on_write:
            self.on_write(bytes(int(b) for b in value))

    @dbus.service.method(GCHAR)
    def StartNotify(self):
        self.notifying = True
        log(f"App abonniert: {self.label}")

    @dbus.service.method(GCHAR)
    def StopNotify(self):
        self.notifying = False
        log(f"App beendet Abo: {self.label}")

    @dbus.service.signal(PROPS, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass


class App(dbus.service.Object):
    def __init__(self, bus, s4):
        super().__init__(bus, "/rowbridge")
        self.svc, self.s4 = "/rowbridge/ftms", s4
        self.feature = Char(bus, self.svc, "2acc", ["read"], "Features")
        self.rower = Char(bus, self.svc, "2ad1", ["notify"], "Ruderdaten")
        self.training = Char(bus, self.svc, "2ad3", ["read", "notify"], "Trainingsstatus")
        self.ctrl = Char(bus, self.svc, "2ad9", ["write", "indicate"], "Steuerkanal", self.command)
        self.status = Char(bus, self.svc, "2ada", ["notify"], "Geraetestatus")
        self.chars = (self.feature, self.rower, self.training, self.ctrl, self.status)
        # Geraeteinformation: manche Apps verbinden sich erst, wenn es diesen Dienst gibt
        self.dis = "/rowbridge/dis"
        self.info = []
        for short, text in (("2a29", "Rowbox"), ("2a24", "S4 Bridge"),
                            ("2a26", VERSION), ("2a28", VERSION)):
            c = Char(bus, self.dis, short, ["read"], "Geraeteinfo")
            c.set(text.encode())
            self.info.append(c)
        # Features: Distanz, Pace, Energie, Zeit, Leistung
        self.feature.set(struct.pack("<II", 0x5224, 0))
        self.training.set(b"\x00\x01")             # Idle
        self.was_active = False

    def command(self, data):
        op, result = data[0], 0x01                 # 0x01 = Erfolg
        log(f"App-Befehl: {OPS.get(op, 'nicht unterstuetzt')} ({data.hex()})")
        if op not in OPS:
            result = 0x02                          # Op Code nicht unterstuetzt
        self.ctrl.set(bytes([0x80, op, result]))
        if op == 0x01:
            self.s4.reset()
            self.status.set(b"\x01")
        elif op == 0x07:
            self.status.set(b"\x04")
        elif op == 0x08:
            self.status.set(bytes([0x02, data[1] if len(data) > 1 else 0x01]))

    def tick(self):
        active = self.s4.active()
        if active != self.was_active:              # Wechsel Rudern <-> Pause melden
            self.was_active = active
            log("Rudern erkannt" if active else "Pause erkannt")
            self.status.set(b"\x04" if active else b"\x02\x02")
            self.training.set(b"\x00\x0d" if active else b"\x00\x01")
            if not active:
                self.s4.v["watts"] = 0
        self.rower.set(rower_data(self.s4.v, active))

    @dbus.service.method(OBJMGR, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        objs = {dbus.ObjectPath(self.svc): {"org.bluez.GattService1":
                {"UUID": FTMS, "Primary": dbus.Boolean(True)}},
                dbus.ObjectPath(self.dis): {"org.bluez.GattService1":
                {"UUID": DIS, "Primary": dbus.Boolean(True)}}}
        for c in (*self.chars, *self.info):
            objs[dbus.ObjectPath(c.path)] = c.props()
        return objs


class Advert(dbus.service.Object):
    def __init__(self, bus):
        super().__init__(bus, "/rowbridge/adv")

    @dbus.service.method(PROPS, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return {"Type": "peripheral", "LocalName": NAME,
                "ServiceUUIDs": dbus.Array([FTMS], signature="s"),
                # verfuegbar (0x01) + Geraetetyp Rudergeraet (Bit 4)
                "ServiceData": dbus.Dictionary({FTMS: dbus.Array(
                    [dbus.Byte(0x01), dbus.Byte(0x10), dbus.Byte(0x00)], signature="y")},
                    signature="sv")}

    @dbus.service.method("org.bluez.LEAdvertisement1")
    def Release(self):
        log("Bluetooth-Werbung von BlueZ beendet")


def write_wave(s4):
    """Nur wenn das Display Taktgeber oder Live-Kurve zeigt: 10-mal pro Sekunde Radtempo schreiben."""
    try:
        if time.time() - os.path.getmtime(WAVE_ON) > 5:
            return
    except OSError:
        return
    now = time.monotonic()
    try:
        w, cs = list(s4.wave), list(s4.catches)
    except RuntimeError:                       # gerade veraendert, naechstes Mal
        return
    missing = min(WAVE_LEN, int((now - s4.p_at) / rowstroke.WIN)) if s4.p_at else WAVE_LEN
    if missing > 1:                            # Rad steht: Nullen nachschieben
        w = (w + [0] * missing)[-WAVE_LEN:]
    # Schlagdauer: Median der letzten Abstaende, robust gegen einzelne Fehlerkennungen
    periods = sorted(b - a for a, b in list(zip(cs, cs[1:]))[-7:] if 1.2 < b - a < 6)
    write_json(WAVE, {"ts": time.time(), "w": w,
                      "catch_ago": [round(now - c, 3) for c in cs if now - c < 12],
                      "period": round(periods[len(periods) // 2], 3) if periods else None})


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    adapter = bus.get_object(BLUEZ, ADAPTER)
    props = dbus.Interface(adapter, PROPS)
    props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
    props.Set("org.bluez.Adapter1", "Alias", NAME)
    loop = GLib.MainLoop()

    def fail(what):
        def cb(err):
            log(f"FEHLER {what}: {err}")
            loop.quit()
        return cb

    def on_device(interface, changed, invalidated, path=None):
        if interface != "org.bluez.Device1":
            return
        dev = str(path).split("/")[-1]
        for key in ("Connected", "Paired", "Bonded"):
            if key in changed:
                log(f"Bluetooth {dev}: {key} = {bool(changed[key])}")

    bus.add_signal_receiver(on_device, "PropertiesChanged", PROPS, bus_name=BLUEZ,
                            path_keyword="path")

    s4 = S4(PORT)
    app, adv = App(bus, s4), Advert(bus)
    dbus.Interface(adapter, "org.bluez.GattManager1").RegisterApplication(
        "/rowbridge", {}, reply_handler=lambda: log("Bluetooth-Dienst aktiv"),
        error_handler=fail("GATT"))
    dbus.Interface(adapter, "org.bluez.LEAdvertisingManager1").RegisterAdvertisement(
        "/rowbridge/adv", {}, reply_handler=lambda: log(f"Sichtbar als '{NAME}'"),
        error_handler=fail("Advertising"))
    s4.start()
    state = {"n": 0, "last": None}
    book = Logbook(LOGBOOK)

    def push():
        app.tick()
        state["n"] += 1
        v = s4.v
        strokes = s4.take_strokes()
        if s4.ok is True:
            book.update(v, s4.active(), time.time(), strokes)
        # Live: Kurve gleich nach dem Zug zeigen, Rollphase kommt mit dem naechsten Zug
        cur = s4.analyzer.last or s4.last_stroke if (s4.active() or book.cur) else None
        prev = s4.last_stroke if cur else None
        write_state({"ts": time.time(), "s4": s4.ok is True, "app": app.rower.notifying,
                     "session": book.cur is not None, "last": book.last,
                     "active": s4.active(), "dist": v["dist"], "strokes": v["strokes"],
                     "spm": v["spm"] if s4.active() else 0,
                     "watts": v["watts"] if s4.active() else 0,
                     "bpm": v["bpm"] if 30 <= v["bpm"] <= 240 else 0,
                     "pace": round(50000 / v["cmps"]) if s4.active() and v["cmps"] > 0 else 0,
                     "cal": v["cal"] // 1000, "version": VERSION,
                     "pause_left": book.pause_left(time.time()) if not s4.active() else None,
                     "session_m": max(v["dist"] - book.cur["d0"], 0) if book.cur else 0,
                     "curve": cur["curve"] if cur else None,
                     "drive": cur["drive"] if cur else None,
                     "recover": prev["recover"] if prev else None,
                     "stroke_n": cur["n"] if cur else None,
                     "elapsed": v["hr"] * 3600 + v["min"] * 60 + v["sec"]})
        now = (v["dist"], v["strokes"])
        if state["n"] % 5 == 0 and now != state["last"]:
            state["last"] = now
            log(f"{v['dist']} m  {v['strokes']} Schlaege  {v['spm'] if s4.active() else 0}/min  "
                f"{v['watts']} W" + ("  [App empfaengt]" if app.rower.notifying else "  [keine App]"))
        return True

    GLib.timeout_add(100, lambda: write_wave(s4) or True)
    GLib.timeout_add(1000, push)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
