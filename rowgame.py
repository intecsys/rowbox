# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 The Rowbox authors
"""rowgame: Ruderreise und Trophaeen.

Ohne Druck: keine Serien, keine Fristen, keine Ziele. Die Reise sammelt einfach
die geruderten Meter auf einer echten Strecke, Trophaeen kommen dazu und gehen
nie verloren. Alles wird aus dem Logbuch berechnet; gespeichert wird nur, welche
Trophaeen schon gemeldet wurden und welche Strecke seit wann gilt.

Dateien:  /var/lib/rowbridge/game.json        Strecke, Beginn der Reise, gemeldete Trophaeen
          /run/rowbridge/game_event.json       was die letzte Einheit Neues gebracht hat (fuer das Display)
"""
import json, os, statistics, time

GAME = "/var/lib/rowbridge/game.json"
EVENT = "/run/rowbridge/game_event.json"
DEFAULT_ROUTE = "rhine"

# Strecken: Laenge in km und Orte unterwegs (km ab Start). Flusskilometer gerundet und teils
# geschaetzt; fuer das Spiel kommt es auf ein paar Kilometer nicht an.
D = lambda de, en: {"de": de, "en": en}
CONTINENTS = [("europe", D("Europa", "Europe")), ("africa", D("Afrika", "Africa")), ("asia", D("Asien", "Asia")),
              ("namerica", D("Nordamerika", "North America")), ("samerica", D("Südamerika", "South America")),
              ("australia", D("Australien", "Australia")), ("antarctica", D("Antarktis", "Antarctica")),
              ("ocean", D("Meer", "Sea"))]
ROUTES = {
    "channel": {"cont": "ocean", "river": D("Ärmelkanal", "English Channel"), "km": 34,
                "stops": [(0, "Dover"), (17, D("Kanalmitte", "Mid-Channel")), (34, "Calais")]},
    "thames": {"cont": "europe", "river": D("Themse", "Thames"), "km": 178,
               "stops": [(0, "Oxford"), (13, "Abingdon"), (33, "Wallingford"), (61, "Reading"), (76, "Henley"),
                         (89, "Marlow"), (110, "Windsor"), (147, "Teddington"), (172, "Westminster"),
                         (178, "Tower Bridge")]},
    "seine": {"cont": "europe", "river": D("Seine", "Seine"), "km": 365,
              "stops": [(0, "Paris"), (70, "Conflans"), (110, "Mantes-la-Jolie"), (150, "Vernon"),
                        (175, "Les Andelys"), (245, "Rouen"), (300, "Caudebec-en-Caux"), (365, "Le Havre")]},
    "po": {"cont": "europe", "river": D("Po", "Po"), "km": 540,
           "stops": [(0, D("Turin", "Turin")), (85, "Casale Monferrato"), (180, "Pavia"),
                     (230, "Piacenza"), (280, "Cremona"), (370, D("Mantua (Borgoforte)", "Mantua (Borgoforte)")),
                     (460, "Ferrara"), (540, D("Po-Delta", "Po Delta"))]},
    "rhine": {"cont": "europe", "river": D("Rhein", "Rhine"), "km": 834,
              "stops": [(0, "Basel"), (60, "Breisach"), (127, D("Straßburg", "Strasbourg")), (196, "Karlsruhe"),
                        (258, "Mannheim"), (332, "Mainz"), (425, "Koblenz"), (489, "Bonn"),
                        (522, D("Köln", "Cologne")), (578, "Düsseldorf"), (614, "Duisburg"),
                        (686, "Emmerich"), (718, "Nijmegen"), (834, "Rotterdam")]},
    "nile": {"cont": "africa", "river": D("Nil", "Nile"), "km": 1150,
             "stops": [(0, D("Assuan", "Aswan")), (45, "Kom Ombo"), (105, "Edfu"),
                       (155, "Esna"), (210, "Luxor"), (270, "Qena"), (420, "Sohag"), (520, D("Assiut", "Asyut")),
                       (650, D("Minia", "Minya")), (790, "Beni Suef"), (910, D("Kairo", "Cairo")),
                       (1150, D("Rosette", "Rosetta"))]},
    "yangtze": {"cont": "asia", "river": D("Jangtse", "Yangtze"), "km": 2380,
                "stops": [(0, "Chongqing"), (330, "Wanzhou"), (610, D("Drei-Schluchten-Damm", "Three Gorges Dam")),
                          (660, "Yichang"), (1290, "Wuhan"), (1560, "Jiujiang"), (1990, "Nanjing"),
                          (2380, "Shanghai")]},
    "mississippi": {"cont": "namerica", "river": D("Mississippi", "Mississippi"), "km": 2755,
                    "stops": [(0, "Minneapolis"), (251, "La Crosse"), (441, "Dubuque"), (599, "Davenport"),
                              (1085, "St. Louis"), (1374, "Cairo (Illinois)"), (1725, "Memphis"),
                              (2205, "Vicksburg"), (2540, "Baton Rouge"), (2755, "New Orleans")]},
    "amazon": {"cont": "samerica", "river": D("Amazonas", "Amazon"), "km": 3600,
               "stops": [(0, "Iquitos"), (400, "Tabatinga"), (1100, "Tefé"), (1950, "Manaus"),
                         (2350, "Parintins"), (2700, "Santarém"), (3600, "Belém")]},
    "murray": {"cont": "australia", "river": D("Murray", "Murray"), "km": 2220,
               "stops": [(0, "Albury"), (520, "Echuca"), (830, "Swan Hill"), (1350, "Mildura"),
                         (1660, "Renmark"), (1900, "Morgan"), (2110, "Murray Bridge"),
                         (2220, D("Murray-Mündung", "Murray Mouth"))]},
    "onyx": {"cont": "antarctica", "river": D("Onyx", "Onyx"), "km": 32,
             "stops": [(0, D("Wright-Gletscher", "Wright Glacier")), (16, D("Wright Valley", "Wright Valley")),
                       (32, D("Vandasee", "Lake Vanda"))]},
    "atlantic": {"cont": "ocean", "river": D("Atlantik", "Atlantic"), "km": 4800,
                 "stops": [(0, "La Gomera"), (1200, D("Erstes Viertel", "First quarter")),
                           (2400, D("Halbzeit", "Halfway")), (3600, D("Letztes Viertel", "Last quarter")),
                           (4800, "Antigua")]},
}
for _r in ROUTES.values():                       # Name "Fluss: Start → Ziel" automatisch
    _a, _b = _r["stops"][0][1], _r["stops"][-1][1]
    _r["name"] = {l: f'{_r["river"][l]}: {_a[l] if isinstance(_a, dict) else _a} → '
                     f'{_b[l] if isinstance(_b, dict) else _b}' for l in ("de", "en")}

# Trophaeen: Name und kurze Beschreibung, jeweils deutsch und englisch
TROPHIES = {
    "first":     ("Erste Einheit", "First session", "Die erste gespeicherte Einheit.", "The first saved session."),
    "m1000":     ("1.000 m am Stück", "1,000 m in one go", "Eine Einheit mit mindestens 1.000 m.", "A session of at least 1,000 m."),
    "m5000":     ("5.000 m am Stück", "5,000 m in one go", "Eine Einheit mit mindestens 5.000 m.", "A session of at least 5,000 m."),
    "m10000":    ("10.000 m am Stück", "10,000 m in one go", "Eine Einheit mit mindestens 10.000 m.", "A session of at least 10,000 m."),
    "half":      ("Halbmarathon", "Half marathon", "21.097 m in einer Einheit.", "21,097 m in one session."),
    "min30":     ("Halbe Stunde", "Half an hour", "30 Minuten am Stück gerudert.", "Rowed for 30 minutes in one go."),
    "hour":      ("Eine Stunde", "One hour", "60 Minuten am Stück gerudert.", "Rowed for 60 minutes in one go."),
    "total10":   ("10 km insgesamt", "10 km in total", "Zusammen 10 km gerudert.", "10 km rowed in total."),
    "total100":  ("100 km insgesamt", "100 km in total", "Zusammen 100 km gerudert.", "100 km rowed in total."),
    "total500":  ("500 km insgesamt", "500 km in total", "Zusammen 500 km gerudert.", "500 km rowed in total."),
    "total1000": ("1.000 km insgesamt", "1,000 km in total", "Zusammen 1.000 km gerudert.", "1,000 km rowed in total."),
    "sess10":    ("10 Einheiten", "10 sessions", "Zehn Einheiten im Logbuch.", "Ten sessions in the logbook."),
    "sess50":    ("50 Einheiten", "50 sessions", "Fünfzig Einheiten im Logbuch.", "Fifty sessions in the logbook."),
    "sess100":   ("100 Einheiten", "100 sessions", "Hundert Einheiten im Logbuch.", "A hundred sessions in the logbook."),
    "early":     ("Frühaufsteher", "Early bird", "Eine Einheit vor 7 Uhr begonnen.", "A session started before 7 am."),
    "late":      ("Nachtschicht", "Night shift", "Eine Einheit nach 21 Uhr begonnen.", "A session started after 9 pm."),
    "steady":    ("Gleichmäßig", "Steady", "Mindestens 100 Züge in sehr gleichmäßigem Takt.", "At least 100 strokes at a very even rate."),
}
for _k, _r in ROUTES.items():                    # eine Trophaee je abgeschlossener Strecke
    _de, _en = _r["name"]["de"], _r["name"]["en"]
    TROPHIES["route_" + _k] = (f'{_r["river"]["de"]} geschafft', f'{_r["river"]["en"]} done',
                               f'Ganze Strecke gerudert: {_de.split(": ", 1)[1]}.',
                               f'Rowed the whole route: {_en.split(": ", 1)[1]}.')


def txt(value, lang):
    return value.get(lang, value["de"]) if isinstance(value, dict) else value


def trophy_text(tid, lang):
    de, en, dde, den = TROPHIES[tid]
    return (de, dde) if lang == "de" else (en, den)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump(data, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def load_sessions(logbook):
    try:
        with open(logbook) as f:
            return [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        return []


def steady(tracks, rec):
    """Sehr gleichmaessig: Schlagdauer schwankt um weniger als 4 % (mindestens 100 Zuege)."""
    t = rec.get("track")
    if not t or rec.get("strokes", 0) < 100:
        return False
    try:
        with open(os.path.join(tracks, f"{t}.strokes.jsonl")) as f:
            sts = [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        return False
    periods = [s["drive"] + s["recover"] for s in sts if s.get("recover")]
    if len(periods) < 100:
        return False
    return statistics.pstdev(periods) / statistics.mean(periods) < 0.04


def earned(sessions, tracks, game):
    """Alle verdienten Trophaeen mit Zeitpunkt, chronologisch aus dem Logbuch berechnet."""
    got, total = {}, 0
    for i, r in enumerate(sorted(sessions, key=lambda x: x.get("ended", 0))):
        when = r.get("ended", 0)
        dist, dur = r.get("dist", 0), r.get("dur", 0)
        total += dist
        hour = int(r.get("start", "T00")[11:13] or 0) if len(r.get("start", "")) >= 13 else 12
        checks = {"first": True, "m1000": dist >= 1000, "m5000": dist >= 5000, "m10000": dist >= 10000,
                  "half": dist >= 21097, "min30": dur >= 1800, "hour": dur >= 3600,
                  "total10": total >= 10000, "total100": total >= 100000, "total500": total >= 500000,
                  "total1000": total >= 1000000, "sess10": i + 1 >= 10, "sess50": i + 1 >= 50,
                  "sess100": i + 1 >= 100, "early": hour < 7, "late": hour >= 21}
        for tid, ok in checks.items():
            if ok and tid not in got:
                got[tid] = when
        if "steady" not in got and steady(tracks, r):
            got["steady"] = when
    for rid, t in game.get("finished", {}).items():   # abgeschlossene Reisen
        got.setdefault("route_" + rid, t)
    return got


def journey(sessions, game, upto=None):
    """Fortschritt der aktuellen Reise in km (bis einschliesslich Einheit 'upto')."""
    rid = game.get("route", DEFAULT_ROUTE)
    route = ROUTES.get(rid, ROUTES[DEFAULT_ROUTE])
    since = game.get("since", 0)
    m = sum(r.get("dist", 0) for r in sessions
            if r.get("ended", 0) >= since and (upto is None or r.get("ended", 0) <= upto))
    km = min(m / 1000, route["km"])
    return rid, route, km


def daily_km(sessions, now=None):
    """Durchschnittlich geruderte km pro Tag der letzten 4 Wochen, Ruhetage eingerechnet."""
    now = now or time.time()
    ends = [r.get("ended", 0) for r in sessions if r.get("ended")]
    if not ends:
        return 0.0
    days = min(28, max(7, (now - min(ends)) / 86400))
    m = sum(r.get("dist", 0) for r in sessions if now - r.get("ended", 0) <= days * 86400)
    return m / 1000 / days


def eta(km_left, sessions, speed_kmh=None, now=None):
    """Geschaetzte Ankunft mit Puffer. Beim Rudern nach aktuellem Tempo (Uhrzeit), sonst nach
    dem Tagesschnitt (Datum). Gibt (Zeitpunkt, "time"|"date") oder (None, None)."""
    now = now or time.time()
    if km_left <= 0:
        return now, "time"
    if speed_kmh and speed_kmh > 1:
        t = now + km_left / speed_kmh * 3600 * 1.1        # 10 % Puffer
        if t - now < 3 * 3600:
            return t, "time"
    per_day = daily_km(sessions, now)
    if per_day <= 0:
        return None, None
    return now + km_left / per_day * 86400 * 1.25, "date"   # 25 % Puffer, kein Druck


def fmt_eta(ts, mode, lang, now=None):
    if not ts:
        return "–"
    now = now or time.time()
    lt, ln = time.localtime(ts), time.localtime(now)
    if mode == "time":
        return time.strftime("%H:%M", lt)
    days = (time.mktime(lt[:3] + (0, 0, 0, 0, 0, -1)) - time.mktime(ln[:3] + (0, 0, 0, 0, 0, -1))) / 86400
    if days < 0.5:
        return "heute" if lang == "de" else "today"
    if days < 1.5:
        return "morgen" if lang == "de" else "tomorrow"
    months = ("Jan Feb Mär Apr Mai Jun Jul Aug Sep Okt Nov Dez" if lang == "de" else
              "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec").split()
    out = f"{lt.tm_mday}. {months[lt.tm_mon - 1]}" if lang == "de" else f"{lt.tm_mday} {months[lt.tm_mon - 1]}"
    return out + (f" {lt.tm_year}" if lt.tm_year != ln.tm_year else "")


def status(sessions, game, lang, extra_km=0.0, speed_kmh=None):
    """Fuer Anzeige und Portal: Strecke, km, naechster Ort, Ziel, Ankunftszeiten.
    extra_km: Meter der gerade laufenden Einheit (noch nicht im Logbuch)."""
    rid, route, km = journey(sessions, game)
    km = min(km + extra_km, route["km"])
    nxt = next(((k, n) for k, n in route["stops"] if k > km), None)
    passed = [(k, txt(n, lang)) for k, n in route["stops"] if k <= km]
    left = route["km"] - km
    next_left = nxt[0] - km if nxt else 0
    e_next = eta(next_left, sessions, speed_kmh) if nxt else (None, None)
    e_goal = eta(left, sessions, speed_kmh)
    return {"route": rid, "name": txt(route["name"], lang), "river": txt(route["river"], lang),
            "goal": txt(route["stops"][-1][1], lang), "km": round(km, 1), "total": route["km"],
            "left": round(left, 1), "next": txt(nxt[1], lang) if nxt else None,
            "next_left": round(next_left, 1), "passed": passed,
            "stops": [(k, txt(n, lang)) for k, n in route["stops"]], "done": km >= route["km"],
            "eta_next": fmt_eta(*e_next, lang), "eta_goal": fmt_eta(*e_goal, lang),
            "eta_mode": e_goal[1], "per_day": round(daily_km(sessions), 1)}


def on_session(logbook, tracks, rec):
    """Nach dem Speichern einer Einheit: neue Trophaeen und erreichte Orte ermitteln.
    Schreibt game.json und das Ereignis fuer das Display."""
    game = load_json(GAME, None)
    sessions = load_sessions(logbook)
    first_run = game is None
    if first_run:                                  # Reise beginnt mit allen bisherigen Einheiten
        game = {"route": DEFAULT_ROUTE, "since": 0, "notified": [], "finished": {}}
    rid, route, before = journey(sessions, game, upto=rec.get("ended", 0) - 0.001)
    _, _, after = journey(sessions, game)
    if after >= route["km"] and rid not in game["finished"]:
        game["finished"][rid] = rec.get("ended", time.time())
    stops = [n for k, n in route["stops"] if before < k <= after and k > 0]
    got = earned(sessions, tracks, game)
    new = [t for t in got if t not in game["notified"]]
    game["notified"] = sorted(set(game["notified"]) | set(got))
    save_json(GAME, game)
    if first_run:                                  # beim ersten Mal nicht mit Altlasten fluten
        new, stops = [t for t in new if got[t] >= rec.get("ended", 0)], stops[-1:]
    event = {"ended": rec.get("ended"), "trophies": new, "stops": stops, "route": rid,
             "km": round(after, 1), "total": route["km"], "done": after >= route["km"]}
    save_json(EVENT, event)
    return event


def set_route(rid):
    """Neue Reise auf einer anderen Strecke beginnen (ab jetzt)."""
    if rid not in ROUTES:
        return
    game = load_json(GAME, {"notified": [], "finished": {}})
    game.update(route=rid, since=time.time())
    save_json(GAME, game)
