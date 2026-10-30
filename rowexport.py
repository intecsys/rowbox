#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 The Rowbox authors
"""rowexport: macht aus einer Einheit im Logbuch eine FIT- oder TCX-Datei.

FIT (Garmin) ist das bessere Format: Sportart „Rudern, indoor“, wird von Garmin
Connect, Strava, Intervals.icu, TrainingPeaks usw. gelesen. TCX ist das alte
XML-Format fuer Plattformen, die kein FIT nehmen.

Eingabe: der Logbuch-Eintrag (dict) und die Messpunkte aus tracks/<id>.jsonl,
je [Sekunde, Meter, Schlaege, Schlagzahl, Watt, cm/s, Puls].

Aufruf zum Testen:  python3 rowexport.py <logbuch.jsonl> <tracks-ordner> <id> fit|tcx
"""
import datetime, json, os, struct, sys
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # rowtext.py liegt daneben
from rowtext import L

FIT_EPOCH = 631065600            # 31.12.1989 00:00 UTC in Unix-Sekunden
SPORT_ROWING, SUB_INDOOR_ROWING = 15, 14


def load_track(tracks_dir, track_id):
    if not track_id or not str(track_id).isdigit():
        return None
    try:
        with open(os.path.join(tracks_dir, f"{track_id}.jsonl")) as f:
            return [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        return None


def _points(rec, pts):
    """Messpunkte als dicts, mit Unix-Zeit."""
    t0 = int(rec["track"])
    out = []
    for p in pts:
        sec, dist, strokes, spm, watts, cmps, bpm = (list(p) + [0] * 7)[:7]
        out.append({"ts": t0 + sec, "dist": dist, "strokes": strokes, "spm": spm,
                    "watts": watts, "speed": cmps / 100, "bpm": bpm})
    return out


def _avg_speed(rec):
    return rec["dist"] / rec["dur"] if rec.get("dur") else 0


# ---------- FIT ----------

def _crc(data, crc=0):
    table = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]
    for b in data:
        tmp = table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ table[b & 0xF]
        tmp = table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ table[(b >> 4) & 0xF]
    return crc


# Basistypen: (Kennung, struct-Format, ungueltiger Wert)
ENUM, UINT8, UINT16, UINT32, UINT32Z = (0x00, "B", 0xFF), (0x02, "B", 0xFF), \
    (0x84, "H", 0xFFFF), (0x86, "I", 0xFFFFFFFF), (0x8C, "I", 0)


class FitWriter:
    def __init__(self):
        self.body = bytearray()
        self.defs = {}

    def msg(self, local, global_num, fields):
        """fields: Liste (Feldnummer, Basistyp, Wert). Definition nur beim ersten Mal."""
        layout = tuple((num, bt) for num, bt, _ in fields)
        if self.defs.get(local) != (global_num, layout):
            self.defs[local] = (global_num, layout)
            self.body += struct.pack("<BBBHB", 0x40 | local, 0, 0, global_num, len(fields))
            for num, bt, _ in fields:
                self.body += struct.pack("<BBB", num, struct.calcsize(bt[1]), bt[0])
        self.body += bytes([local])
        for _, bt, val in fields:
            val = bt[2] if val is None else int(round(val))
            self.body += struct.pack("<" + bt[1], val)

    def bytes(self):
        header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(self.body), b".FIT")
        header += struct.pack("<H", _crc(header))
        data = header + bytes(self.body)
        return data + struct.pack("<H", _crc(data))


def fit(rec, pts):
    p = _points(rec, pts)
    t0 = int(rec["track"])
    t_end = t0 + rec["dur"]
    ft = lambda unix: unix - FIT_EPOCH
    hr = lambda x: x if x else None
    w = FitWriter()
    # file_id
    w.msg(0, 0, [(0, ENUM, 4), (1, UINT16, 255), (2, UINT16, 1), (3, UINT32Z, 1),
                 (4, UINT32, ft(t0))])
    # Timer-Start
    w.msg(1, 21, [(253, UINT32, ft(t0)), (0, ENUM, 0), (1, ENUM, 0)])
    # Messpunkte
    for x in p:
        w.msg(2, 20, [(253, UINT32, ft(x["ts"])), (5, UINT32, x["dist"] * 100),
                      (6, UINT16, x["speed"] * 1000), (4, UINT8, x["spm"]),
                      (7, UINT16, x["watts"]), (3, UINT8, hr(x["bpm"]))])
    # Timer-Stopp
    w.msg(1, 21, [(253, UINT32, ft(t_end)), (0, ENUM, 0), (1, ENUM, 4)])
    max_speed = max((x["speed"] for x in p), default=0)
    common = dict(elapsed=rec["dur"] * 1000, dist=rec["dist"] * 100, strokes=rec["strokes"],
                  kcal=rec.get("kcal"), avg_speed=_avg_speed(rec) * 1000,
                  max_speed=max_speed * 1000, avg_w=rec.get("watts") or None,
                  max_w=rec.get("watts_max") or None, avg_hr=rec.get("hr"),
                  max_hr=rec.get("hr_max"), spm=round(rec.get("spm") or 0) or None)
    c = common
    # Runde
    w.msg(3, 19, [(253, UINT32, ft(t_end)), (0, ENUM, 9), (1, ENUM, 1), (2, UINT32, ft(t0)),
                  (7, UINT32, c["elapsed"]), (8, UINT32, c["elapsed"]), (9, UINT32, c["dist"]),
                  (10, UINT32, c["strokes"]), (11, UINT16, c["kcal"]),
                  (13, UINT16, c["avg_speed"]), (14, UINT16, c["max_speed"]),
                  (15, UINT8, c["avg_hr"]), (16, UINT8, c["max_hr"]), (17, UINT8, c["spm"]),
                  (19, UINT16, c["avg_w"]), (20, UINT16, c["max_w"]),
                  (25, ENUM, SPORT_ROWING), (39, ENUM, SUB_INDOOR_ROWING)])
    # Einheit
    w.msg(4, 18, [(253, UINT32, ft(t_end)), (0, ENUM, 8), (1, ENUM, 1), (2, UINT32, ft(t0)),
                  (5, ENUM, SPORT_ROWING), (6, ENUM, SUB_INDOOR_ROWING),
                  (7, UINT32, c["elapsed"]), (8, UINT32, c["elapsed"]), (9, UINT32, c["dist"]),
                  (10, UINT32, c["strokes"]), (11, UINT16, c["kcal"]),
                  (14, UINT16, c["avg_speed"]), (15, UINT16, c["max_speed"]),
                  (16, UINT8, c["avg_hr"]), (17, UINT8, c["max_hr"]), (18, UINT8, c["spm"]),
                  (20, UINT16, c["avg_w"]), (21, UINT16, c["max_w"]),
                  (25, UINT16, 0), (26, UINT16, 1)])
    # Aktivitaet
    offset = datetime.datetime.fromtimestamp(t_end).astimezone().utcoffset().total_seconds()
    w.msg(5, 34, [(253, UINT32, ft(t_end)), (0, UINT32, c["elapsed"]), (1, UINT16, 1),
                  (2, ENUM, 0), (3, ENUM, 26), (4, ENUM, 1), (5, UINT32, ft(t_end) + offset)])
    return w.bytes()


# ---------- TCX ----------

def _iso(unix):
    return datetime.datetime.fromtimestamp(unix, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tcx(rec, pts):
    p = _points(rec, pts)
    t0 = int(rec["track"])
    tps = []
    for x in p:
        hr = f"<HeartRateBpm><Value>{x['bpm']}</Value></HeartRateBpm>" if x["bpm"] else ""
        tps.append(
            f"<Trackpoint><Time>{_iso(x['ts'])}</Time><DistanceMeters>{x['dist']}</DistanceMeters>"
            f"{hr}<Cadence>{x['spm']}</Cadence><Extensions><ns3:TPX><ns3:Speed>{x['speed']:.2f}"
            f"</ns3:Speed><ns3:Watts>{x['watts']}</ns3:Watts></ns3:TPX></Extensions></Trackpoint>")
    hr_avg = (f"<AverageHeartRateBpm><Value>{rec['hr']}</Value></AverageHeartRateBpm>"
              f"<MaximumHeartRateBpm><Value>{rec['hr_max']}</Value></MaximumHeartRateBpm>"
              if rec.get("hr") else "")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
 xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">
<Activities><Activity Sport="Other"><Id>{_iso(t0)}</Id>
<Lap StartTime="{_iso(t0)}"><TotalTimeSeconds>{rec['dur']}</TotalTimeSeconds>
<DistanceMeters>{rec['dist']}</DistanceMeters><Calories>{rec.get('kcal') or 0}</Calories>{hr_avg}
<Intensity>Active</Intensity><Cadence>{round(rec.get('spm') or 0)}</Cadence><TriggerMethod>Manual</TriggerMethod>
<Track>
{chr(10).join(tps)}
</Track>
<Extensions><ns3:LX><ns3:AvgSpeed>{_avg_speed(rec):.2f}</ns3:AvgSpeed><ns3:AvgWatts>{rec.get('watts') or 0}</ns3:AvgWatts></ns3:LX></Extensions>
</Lap><Notes>{escape(L('Indoor-Rudern (Rowbox)'))}</Notes>
<Creator xsi:type="Device_t" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><Name>Rowbox</Name><UnitId>0</UnitId><ProductID>0</ProductID></Creator>
</Activity></Activities></TrainingCenterDatabase>
""".encode()


def filename(rec, ext):
    return f"rowbox-{rec['start'][:16].replace(':', '-').replace('T', '_')}.{ext}"


if __name__ == "__main__":
    book, tracks, tid, kind = sys.argv[1:5]
    rec = next(json.loads(x) for x in open(book) if json.loads(x).get("track") == tid)
    data = (fit if kind == "fit" else tcx)(rec, load_track(tracks, tid))
    sys.stdout.buffer.write(data)
