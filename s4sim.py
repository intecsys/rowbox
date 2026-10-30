#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 The Rowbox authors
"""s4sim: simulierter WaterRower-S4-Monitor zum Testen ohne Rudergeraet.

Legt einen virtuellen seriellen Anschluss an (Standard: ~/s4sim) und
beantwortet die USB-Befehle wie ein echter S4. Rudert in Intervallen:
60 s rudern, 20 s Pause, dann von vorn.

Start:  python3 s4sim.py            (Strg+C beendet)
Bruecke dagegen testen:  sudo python3 rowbridge.py ~/s4sim
Mit Puls (wie mit Pulsempfaenger am S4):  S4SIM_PULS=1 python3 s4sim.py
"""
import math, os, pty, random, sys, time, tty

LINK = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/s4sim")
ROW, PAUSE = 60, 20            # Sekunden rudern / Pause
SPM, SPEED = 22, 3.3           # Schlagzahl, Meter pro Sekunde beim Rudern
PULSE = os.environ.get("S4SIM_PULS") == "1"   # Pulsempfaenger am S4 nachbilden

master, slave = pty.openpty()
tty.setraw(master); tty.setraw(slave)
os.chmod(os.ttyname(slave), 0o666)
try:
    os.remove(LINK)
except FileNotFoundError:
    pass
os.symlink(os.ttyname(slave), LINK)
print(f"Simulierter S4 an {LINK} ({os.ttyname(slave)})", flush=True)

state = {"t0": time.time(), "dist": 0.0, "strokes": 0, "last": time.time(),
         "stroke_t": 0.0, "kcal": 0.0, "pulse_t": time.time(), "in_drive": False}


def rowing(now):
    return (now - state["t0"]) % (ROW + PAUSE) < ROW


def update():
    now = time.time()
    dt, state["last"] = now - state["last"], now
    if rowing(now):
        state["dist"] += SPEED * dt
        state["kcal"] += 0.15 * dt          # grob, nur damit sich etwas bewegt
        state["stroke_t"] += dt
        if state["stroke_t"] >= 60 / SPM:
            state["stroke_t"] -= 60 / SPM
            state["strokes"] += 1


def value(addr):
    now = time.time()
    on = rowing(now)
    secs = int(now - state["t0"])
    vals = {
        "055": f"{int(state['dist']) & 0xFFFF:04X}",
        "140": f"{state['strokes'] & 0xFFFF:04X}",
        "1A9": f"{SPM if on else 0:02X}",
        "088": f"{(120 + int(now) % 40) if on and int(now * 2) % 6 == 0 else 0:04X}",
        "14A": f"{int(SPEED * 100) if on else 0:04X}",
        "08A": f"{int(state['kcal'] * 1000) & 0xFFFFFF:06X}",
        "1E1": f"{secs % 60:02d}",
        "1E2": f"{secs // 60 % 60:02d}",
        "1E3": f"{secs // 3600:02d}",
        "142": f"{int(60 / SPM * 25):02X}",       # Einheit beim echten S4 noch unklar
        "143": f"{int(60 / SPM * 0.35 * 25):02X}",
        "1A0": f"{(118 + int(now) % 10) if on and PULSE else 0:04X}",
    }
    return vals.get(addr)


def drive_force(x):
    """Zugkraft ueber den Durchzug (x = 0..1): erst Beine (Spitze), dann Ruecken und Arme."""
    if not 0 <= x <= 1:
        return 0.0
    base = math.sin(math.pi * x) ** 0.8
    dip = 1 - 0.3 * math.exp(-((x - 0.5) / 0.1) ** 2)
    early = 1 + 0.35 * math.exp(-((x - 0.3) / 0.15) ** 2)
    return base * dip * early


def events():
    """Meldungen, die der S4 von selbst schickt: Impulse je 25 ms (P), Zuganfang (SS), Zugende (SE).
    Das Paddelrad wird physikalisch nachgerechnet: Zugkraft beschleunigt, das Wasser bremst."""
    now = time.time()
    out = []
    while now - state["pulse_t"] >= 0.025:        # ein P je 25-ms-Fenster
        state["pulse_t"] += 0.025
        t = state["pulse_t"]
        period = 60 / SPM
        phase = ((t - state["t0"]) % period) / period
        x = phase / 0.35                           # Durchzug = erstes gutes Drittel
        force = 620 * drive_force(x) * (0.85 + 0.3 * random.random()) if rowing(t) else 0.0
        w = state.get("w", 0.0)
        w = max(0.0, w + (force - 0.0009 * w * w) * 0.025)
        state["w"] = w
        drive = force > 0
        if drive != state["in_drive"]:
            state["in_drive"] = drive
            out.append("SS" if drive else "SE")
        n = w * 0.025
        n = int(n) + (1 if random.random() < n - int(n) else 0)
        if n > 0:
            out.append(f"P{min(n, 255):02X}")
    return out


buf = b""
try:
    while True:
        buf += os.read(master, 256)
        update()
        for ev in events():
            os.write(master, (ev + "\r\n").encode())
        while b"\r\n" in buf:
            line, buf = buf.split(b"\r\n", 1)
            cmd = line.decode(errors="ignore").strip()
            if cmd == "USB":
                os.write(master, b"_WR_\r\n")
            elif cmd == "IV?":
                os.write(master, b"IV40210\r\n")
            elif cmd == "RESET":
                state.update(t0=time.time(), dist=0.0, strokes=0, kcal=0.0, stroke_t=0.0)
                os.write(master, b"OK\r\n")
                print("RESET empfangen, Werte auf null", flush=True)
            elif cmd.startswith("IR") and len(cmd) >= 6:
                v = value(cmd[3:6])
                if v is not None:
                    os.write(master, f"ID{cmd[2]}{cmd[3:6]}{v}\r\n".encode())
except KeyboardInterrupt:
    pass
finally:
    try:
        os.remove(LINK)
    except FileNotFoundError:
        pass
