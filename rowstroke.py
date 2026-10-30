# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 The Rowbox authors
"""rowstroke: Kraftkurve und Zug-/Rollphase aus den Sensorimpulsen des S4.

Der S4 meldet alle 25 ms, wie viele Impulse sein Sensor am Paddelrad gezaehlt
hat ("Pxx"). Daraus ergibt sich das Tempo des Rads. Beim Ziehen beschleunigt
es, in der Rollphase bremst das Wasser es ab.

    Kraft ~ Beschleunigung + c * Tempo^2

c (der Wasserwiderstand) wird laufend aus der Rollphase geschaetzt: dort zieht
niemand, also gilt d(1/Tempo)/dt = c. Die Kurve hat damit die richtige Form,
aber keine absolute Einheit; sie wird pro Schlag auf 0..100 % gebracht.

Benutzung:  a = Analyzer();  for n in impulse_je_25ms: stroke = a.add(n)
            stroke ist None oder ein dict mit curve, drive, recover, peak.
"""
from collections import deque

WIN = 0.025            # Sekunden pro P-Meldung
POINTS = 30            # Stuetzstellen einer gespeicherten Kurve
C_START = 0.0009       # Startwert Wasserwiderstand (aus einer Aufzeichnung, passt sich an)
MAX_RECOVER = 6.0      # laengere "Rollphasen" sind Pausen und zaehlen nicht mit


def resample(vals, n=POINTS):
    if len(vals) < 2:
        return [0] * n
    out = []
    for i in range(n):
        x = i * (len(vals) - 1) / (n - 1)
        j = int(x)
        f = x - j
        out.append(vals[j] if j + 1 >= len(vals) else vals[j] * (1 - f) + vals[j + 1] * f)
    return out


class Analyzer:
    def __init__(self):
        self.c = C_START
        self.rates = deque(maxlen=5)       # Impulse/s, zum Glaetten
        self.hist = deque(maxlen=12)       # (Tempo, Kraft) der letzten Fenster, fuer den Anlauf
        self.ws = deque(maxlen=5)          # geglaettetes Tempo, fuer die Beschleunigung
        self.t = 0.0                       # Sekunden seit Start
        self.in_drive = False
        self.drive = []                    # Kraftwerte des laufenden Zugs
        self.drive_t0 = 0.0
        self.drive_end = None              # Zeitpunkt Zugende des letzten Schlags
        self.last = None                   # letzter fertiger Schlag (wartet auf Rollphase)
        self.rec = []                      # (t, 1/Tempo) fuer die Widerstandsschaetzung
        self.peak_avg = None               # typische Spitzenkraft, fuer die Schwellen
        self.below = 0
        self.n = 0                         # Zahl der erkannten Zuege, fuer die Live-Anzeige
        self.catch = None                  # gesetzt bei Zuganfang: wie viele Sekunden der Anlauf zurueckliegt

    def reset(self):
        """Rad stand still oder Daten fehlten: neu anfangen, Widerstand behalten."""
        c, peak, n = self.c, self.peak_avg, self.n
        self.__init__()
        self.c, self.peak_avg, self.n = c, peak, n

    def add(self, count):
        """Eine P-Meldung verarbeiten. Gibt einen fertigen Schlag zurueck oder None."""
        self.t += WIN
        self.rates.append(count / WIN)
        w = sum(self.rates) / len(self.rates)
        self.ws.append(w)
        if len(self.ws) < 5:
            return None
        accel = (self.ws[-1] - self.ws[0]) / (4 * WIN)   # ueber 100 ms, sonst zu unruhig
        force = accel + self.c * w * w
        self.hist.append(force)
        on = max(0.25 * (self.peak_avg or 0), 400.0)
        off = 0.12 * (self.peak_avg or 3000.0)
        done = None
        if not self.in_drive:
            if w > 0:
                self.rec.append((self.t, 1.0 / w))
            if len(self.hist) >= 2 and self.hist[-1] > on and self.hist[-2] > on:
                done = self._start_drive()
        else:
            self.drive.append(force)
            self.below = self.below + 1 if force < off else 0
            if self.below >= 3:
                self._end_drive()
        return done

    def _start_drive(self):
        """Zuganfang: vorigen Schlag mit seiner Rollphase abschliessen, neuen beginnen."""
        done = None
        if self.last is not None:
            rec = self.t - self.drive_end
            self.last["recover"] = round(rec, 2) if rec <= MAX_RECOVER else None   # laenger = Pause
            done = self.last
            self.last = None
        self._update_c()
        # Anlauf mitnehmen: rueckwaerts bis die Kraft unter 5 % der ueblichen Spitze faellt
        lead = []
        floor = 0.05 * (self.peak_avg or 3000.0)
        for f in reversed(list(self.hist)[:-1]):
            if f < floor:
                break
            lead.append(f)
        self.drive = lead[::-1] + [self.hist[-1]]
        self.drive_t0 = self.t - (len(self.drive) - 1) * WIN
        self.catch = (len(self.drive) - 1) * WIN
        self.in_drive, self.below = True, 0
        return done

    def _end_drive(self):
        dur = (len(self.drive) - self.below) * WIN
        vals = self.drive                      # mit Auslauf, damit die Kurve gegen null geht
        self.in_drive = False
        self.drive_end = self.t - self.below * WIN
        self.rec = []
        if dur < 0.15:                     # zu kurz fuer einen echten Zug
            return
        vals = [sum(vals[max(0, i - 1):i + 2]) / len(vals[max(0, i - 1):i + 2])
                for i in range(len(vals))]          # leicht glaetten
        peak = max(vals)
        self.peak_avg = peak if self.peak_avg is None else 0.8 * self.peak_avg + 0.2 * peak
        curve = [max(0, round(v / peak * 100)) for v in resample(vals)]
        self.n += 1
        self.last = {"curve": curve, "drive": round(dur, 2), "recover": None,
                     "peak": round(peak), "t": round(self.drive_t0, 2), "n": self.n}

    def _update_c(self):
        """Widerstand aus der Rollphase: Steigung von 1/Tempo ueber der Zeit."""
        pts = [p for p in self.rec if p[0] - (self.drive_end or 0) > 0.3]
        if len(pts) < 20:
            return
        n = len(pts)
        mt = sum(p[0] for p in pts) / n
        my = sum(p[1] for p in pts) / n
        var = sum((p[0] - mt) ** 2 for p in pts)
        if var <= 0:
            return
        slope = sum((p[0] - mt) * (p[1] - my) for p in pts) / var
        if 0.0001 < slope < 0.01:          # nur plausible Werte uebernehmen
            self.c = 0.8 * self.c + 0.2 * slope


def average(curves):
    if not curves:
        return []
    return [round(sum(c[i] for c in curves) / len(curves)) for i in range(len(curves[0]))]
