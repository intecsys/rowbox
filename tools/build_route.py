#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 The Rowbox authors
"""Baut aus OpenStreetMap-Rohdaten eine kompakte Strecke fuer die 3D-Flussfahrt.

Eingabe:  Overpass-Ergebnisse (`way(r) ... out geom tags;`) des Flusses,
          optional Wikidata-Bruecken (SPARQL-JSON mit ?coord und ?de/?en).
Ausgabe:  static/routes/<id>.json  — Flusslinie in Metern, Bruecken, Uferseite der Etappen.

Die Linie wird so skaliert, dass die Laenge zwischen den Etappen genau den Kilometern
der Fahrt in rowgame.py entspricht. Die Rohdaten selbst kommen nicht ins Repo.

Daten: © OpenStreetMap-Mitwirkende (ODbL 1.0), Wikidata (CC0).

    python3 tools/build_route.py rhine --osm daten/rhein-*.json --bridges wd-bridges.json
"""
import argparse
import heapq
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import rowgame  # noqa: E402

R = 6371000.0

# Lage der Etappen (lat, lon) je Strecke, in derselben Reihenfolge wie rowgame.ROUTES[id]["stops"].
# Der erste und letzte Punkt legen Anfang und Ende der Flusslinie fest.
PLACES = {
    "rhine": [
        (47.5601, 7.5897),   # Basel, Mittlere Bruecke
        (48.0300, 7.5820),   # Breisach, Muensterberg
        (48.5717, 7.8010),   # Strassburg, Europabruecke
        (49.0365, 8.3036),   # Karlsruhe, Rheinbruecke Maxau
        (49.4875, 8.4555),   # Mannheim
        (49.9990, 8.2739),   # Mainz, Dom
        (50.3644, 7.6061),   # Koblenz, Deutsches Eck
        (50.7374, 7.1088),   # Bonn
        (50.9413, 6.9583),   # Koeln, Dom
        (51.2277, 6.7735),   # Duesseldorf
        (51.4344, 6.7398),   # Duisburg
        (51.8306, 6.2449),   # Emmerich
        (51.8475, 5.8640),   # Nijmegen
        (51.9050, 4.4830),   # Rotterdam
    ],
}


def local(lat0, lat, lon0, lon):
    """Kleiner Schritt in Metern (Ost, Nord) an der Stelle lat0."""
    return (math.radians(lon - lon0) * R * math.cos(math.radians(lat0)),
            math.radians(lat - lat0) * R)


def dist(a, b):
    dx, dy = local(a[0], b[0], a[1], b[1])
    return math.hypot(dx, dy)


def load_ways(files):
    ways = {}
    for f in files:
        for e in json.load(open(f, encoding="utf-8")).get("elements", []):
            if e.get("type") == "way" and e.get("geometry"):
                ways[e["id"]] = e
    return ways


def graph(ways):
    """Ungerichteter Graph ueber die gemeinsamen Punkte der Wege; Kanaele zaehlen dreifach."""
    g = {}
    for w in ways.values():
        pen = 3.0 if w.get("tags", {}).get("waterway") == "canal" else 1.0
        pts = [(round(p["lat"], 7), round(p["lon"], 7)) for p in w["geometry"]]
        for a, b in zip(pts, pts[1:]):
            d = dist(a, b) * pen
            g.setdefault(a, []).append((b, d))
            g.setdefault(b, []).append((a, d))
    return g


def shortest(g, start, end):
    s = min(g, key=lambda p: dist(p, start))
    t = min(g, key=lambda p: dist(p, end))
    best, prev, q = {s: 0.0}, {}, [(0.0, s)]
    while q:
        d, p = heapq.heappop(q)
        if p == t:
            break
        if d > best[p]:
            continue
        for n, w in g[p]:
            if d + w < best.get(n, math.inf):
                best[n], prev[n] = d + w, p
                heapq.heappush(q, (d + w, n))
    if t not in prev and t != s:
        sys.exit("Keine durchgehende Flusslinie zwischen Start und Ziel gefunden.")
    path = [t]
    while path[-1] != s:
        path.append(prev[path[-1]])
    return path[::-1]


def to_xy(path):
    """Lat/Lon-Kette in Meter (Ost, Nord), Schritt fuer Schritt aufsummiert: Formen und Laengen
    bleiben vor Ort richtig, auch ueber mehrere Breitengrade."""
    xy = [(0.0, 0.0)]
    for a, b in zip(path, path[1:]):
        dx = math.radians(b[1] - a[1]) * R * math.cos(math.radians((a[0] + b[0]) / 2))
        dy = math.radians(b[0] - a[0]) * R
        xy.append((xy[-1][0] + dx, xy[-1][1] + dy))
    return xy


def resample(xy, step):
    out, carry = [xy[0]], 0.0
    for a, b in zip(xy, xy[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        t = step - carry
        while t <= seg:
            k = t / seg
            out.append((a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k))
            t += step
        carry = seg - (t - step)
    if out[-1] != xy[-1]:
        out.append(xy[-1])
    return out


def smooth(xy, radius):
    """Gleitender Mittelwert ueber +-radius Punkte; Anfang und Ende bleiben fest."""
    n, out = len(xy), []
    for i in range(n):
        r = min(radius, i, n - 1 - i)
        xs = xy[i - r:i + r + 1]
        out.append((sum(p[0] for p in xs) / len(xs), sum(p[1] for p in xs) / len(xs)))
    return out


def simplify(xy, tol):
    """Douglas-Peucker, iterativ."""
    keep = [False] * len(xy)
    keep[0] = keep[-1] = True
    stack = [(0, len(xy) - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = xy[i]
        bx, by = xy[j]
        L = math.hypot(bx - ax, by - ay) or 1e-9
        far, k = 0.0, None
        for m in range(i + 1, j):
            d = abs((bx - ax) * (ay - xy[m][1]) - (ax - xy[m][0]) * (by - ay)) / L
            if d > far:
                far, k = d, m
        if k is not None and far > tol:
            keep[k] = True
            stack += [(i, k), (k, j)]
    return [p for p, f in zip(xy, keep) if f]


def cumulative(xy):
    s = [0.0]
    for a, b in zip(xy, xy[1:]):
        s.append(s[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return s


def project(xy, cum, p):
    """Naechster Punkt der Linie: (Strecke s, Abstand, Seite +1 rechts / -1 links in Fahrtrichtung)."""
    best = (math.inf, 0.0, 1)
    for i, (a, b) in enumerate(zip(xy, xy[1:])):
        vx, vy = b[0] - a[0], b[1] - a[1]
        L2 = vx * vx + vy * vy or 1e-9
        t = max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / L2))
        qx, qy = a[0] + vx * t, a[1] + vy * t
        d = math.hypot(p[0] - qx, p[1] - qy)
        if d < best[0]:
            side = 1 if vx * (p[1] - a[1]) - vy * (p[0] - a[0]) < 0 else -1
            best = (d, cum[i] + math.sqrt(L2) * t, side)
    return best[1], best[0], best[2]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("route")
    ap.add_argument("--osm", nargs="+", required=True)
    ap.add_argument("--bridges")
    ap.add_argument("--width", type=float, default=160)
    ap.add_argument("--out")
    a = ap.parse_args()

    route, places = rowgame.ROUTES[a.route], PLACES[a.route]
    ways = load_ways(a.osm)
    g = graph(ways)
    # Nur die Etappen, deren Punkt die Rohdaten auch abdecken (Prototyp mit Teilstrecke).
    have = [i for i, p in enumerate(places) if min(dist(q, p) for q in g) < 2500]
    first, last = have[0], have[-1]
    path = shortest(g, places[first], places[last])
    lat0, lon0 = path[0]

    def xy_of(lat, lon):
        # gleiche Aufsummierung wie to_xy, ueber den naechsten Linienpunkt angeschlossen
        i = min(range(len(path)), key=lambda k: dist(path[k], (lat, lon)))
        dx, dy = local(path[i][0], lat, path[i][1], lon)
        return raw[i][0] + dx, raw[i][1] + dy

    raw = to_xy(path)
    line = smooth(resample(raw, 10), 6)                 # Ecken der OSM-Mittellinie weich ziehen
    cum = cumulative(line)

    # Auf die Kilometer der Fahrt skalieren, Etappe fuer Etappe.
    stops = route["stops"]
    marks = [(stops[i][0] * 1000, project(line, cum, xy_of(*places[i]))) for i in range(first, last + 1)]
    knots = [(km, pr[0]) for km, pr in marks]
    def game_s(sr):
        for (g0, r0), (g1, r1) in zip(knots, knots[1:]):
            if sr <= r1 or (g1, r1) == knots[-1]:
                return g0 + (sr - r0) * (g1 - g0) / ((r1 - r0) or 1)
        return sr
    scaled = [line[0]]
    for i in range(1, len(line)):
        a_, b_ = line[i - 1], line[i]
        k = (game_s(cum[i]) - game_s(cum[i - 1])) / ((cum[i] - cum[i - 1]) or 1)
        scaled.append((scaled[-1][0] + (b_[0] - a_[0]) * k, scaled[-1][1] + (b_[1] - a_[1]) * k))
    off = knots[0][0] - 0.0                              # Linie beginnt an der ersten abgedeckten Etappe
    pts = simplify(scaled, 0.8)
    pcum = cumulative(pts)

    bridges = []
    if a.bridges:
        seen = []
        for b in json.load(open(a.bridges, encoding="utf-8"))["results"]["bindings"]:
            if "ferry" in b.get("type", {}).get("value", ""):
                continue
            lon, lat = map(float, b["coord"]["value"][6:-1].split())
            s, d, _ = project(line, cum, xy_of(lat, lon))
            if d > 250 or s <= 0 or s >= cum[-1]:
                continue
            s = game_s(s)
            name = (b.get("de") or b.get("en") or {}).get("value", "")
            if any(abs(s - t) < 80 for t in seen):
                continue
            seen.append(s)
            bridges.append([round(s), name])
        bridges.sort()

    out = {
        "id": a.route,
        "attribution": "© OpenStreetMap-Mitwirkende (ODbL), Wikidata",
        "width": a.width,
        "start": round(off),
        "pts": [v for p in pts for v in (round(p[0], 1), round(p[1], 1))],
        "bridges": bridges,
        "stops": [[stops[i][0], pr[2]] for i, (_, pr) in zip(range(first, last + 1), marks)],
    }
    dst = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "routes", a.route + ".json")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{a.route}: {len(pts)} Punkte, {pcum[-1] / 1000:.1f} km (Etappen {first}–{last}), "
          f"{len(bridges)} Bruecken, {os.path.getsize(dst) // 1024} KB → {dst}")
    for km, pr in marks:
        print(f"  km {km / 1000:6.1f}: OSM {pr[0] / 1000:6.1f} km, {pr[1]:5.0f} m neben der Linie, "
              f"{'rechts' if pr[2] > 0 else 'links'}")


if __name__ == "__main__":
    main()
