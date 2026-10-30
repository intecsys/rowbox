# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 The Rowbox authors
"""rowtext: Texte der Rowbox auf Deutsch und Englisch.

Im Code stehen die deutschen Texte, eingepackt in L("..."). L() liefert sie in
der eingestellten Sprache. Die Sprache steht in /var/lib/rowbridge/language
("de" oder "en") und wird im Einrichtungsassistenten oder im Webportal gewählt.

Neue Texte: deutsch in L("...") schreiben und die englische Fassung unten in EN
eintragen. `python3 rowtext.py` prueft, ob alle Texte uebersetzt sind.
"""
import json, os, time

LANG_FILE = "/var/lib/rowbridge/language"
ONBOARD = "/var/lib/rowbridge/onboarding.json"
LANGS = {"de": "Deutsch", "en": "English"}
_cache = {"lang": None, "t": 0.0}


def chosen():
    """True, wenn schon eine Sprache gewaehlt wurde."""
    return os.path.exists(LANG_FILE)


def get_lang():
    """Aktuelle Sprache, hoechstens alle 2 s von der Karte gelesen."""
    now = time.time()
    if _cache["lang"] and now - _cache["t"] < 2:
        return _cache["lang"]
    try:
        with open(LANG_FILE) as f:
            lang = f.read().strip()
    except OSError:
        lang = ""
    if lang not in LANGS:
        # Boxen, die vor der Sprachwahl eingerichtet wurden, liefen auf Deutsch
        try:
            with open(ONBOARD) as f:
                lang = "de" if json.load(f).get("done") else "en"
        except (OSError, ValueError):
            lang = "en"
    _cache.update(lang=lang, t=now)
    return lang


def set_lang(lang):
    if lang not in LANGS:
        return
    os.makedirs(os.path.dirname(LANG_FILE), exist_ok=True)
    with open(LANG_FILE + ".tmp", "w") as f:
        f.write(lang)
    os.replace(LANG_FILE + ".tmp", LANG_FILE)
    _cache.update(lang=lang, t=time.time())


def L(text, lang=None, **kw):
    lang = lang or get_lang()
    out = EN.get(text, text) if lang == "en" else text
    return out.format(**kw) if kw else out


def fmt_num(n, lang=None):
    s = f"{int(n):,}"
    return s.replace(",", ".") if (lang or get_lang()) == "de" else s


def fmt_date(iso, with_time=True, lang=None):
    """'2026-10-26T16:33:44' -> '26.10. 16:33' bzw. '26 Oct 16:33'."""
    if len(iso) < 10:
        return ""
    day, month = iso[8:10], iso[5:7]
    if (lang or get_lang()) == "de":
        out = f"{day}.{month}."
    else:
        months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
        out = f"{int(day)} {months[int(month) - 1]}"
    return out + (f" {iso[11:16]}" if with_time and len(iso) >= 16 else "")


EN = {
    # ---------- Display ----------
    "Status": "Status",
    "Training": "Training",
    "Letzte Einheit": "Last session",
    "Verlauf": "History",
    "Info": "Info",
    "Akku {pct} %": "Battery {pct} %",
    "  lädt": "  charging",
    "Brücke": "Bridge",
    "läuft": "running",
    "aus": "off",
    "verbunden": "connected",
    "Kabel?": "cable?",
    "wartet": "waiting",
    "WLAN": "Wi-Fi",
    "keins": "none",
    "Letzte Einheit: {m}": "Last session: {m}",
    "Brücke startet oder ist gestoppt. Bitte kurz warten.": "Bridge is starting or stopped. Please wait a moment.",
    "S4 einschalten, Kabel in den inneren USB-Anschluss": "Switch on the S4, cable into the inner USB port",
    "In der App mit „WaterRower S4“ verbinden": "Connect to “WaterRower S4” in the app",
    "Pause": "Pause",
    "Meter": "metres",
    "Schläge/min": "strokes/min",
    "Watt": "watts",
    "Geschafft": "Done",
    "Noch keine Einheit": "No session yet",
    "Ø Watt": "avg watts",
    "Ø Schläge/min": "avg strokes/min",
    "Schläge": "Strokes",
    "Hotspot schließen?": "Close hotspot?",
    "Hotspot öffnen?": "Open hotspot?",
    "weiter halten: {n}": "keep holding: {n}",
    "loslassen = abbrechen": "release = cancel",
    "Hotspot wird geschlossen": "Closing hotspot",
    "Hotspot wird geöffnet": "Opening hotspot",
    "Verbindung fehlgeschlagen": "Connection failed",
    "WLAN einrichten": "Set up Wi-Fi",
    "Passwort: {pw}": "Password: {pw}",
    "QR scannen, Seite öffnet sich": "Scan QR, page opens",
    "QR-Code scannen,": "Scan the QR code,",
    "Seite öffnet sich": "the page opens",
    "Webseite": "Website",
    "Passwort": "Password",
    "Einrichtung {n}/{total}": "Setup {n}/{total}",
    "Einrichtung": "Setup",
    "Taste: überspringen": "Button: skip",
    "Taste: ohne WLAN weiter": "Button: continue without Wi-Fi",
    "Verbinde ...": "Connecting ...",
    "Suche bekannte Netze. Sonst erscheint gleich ein QR-Code.": "Looking for known networks. Otherwise a QR code appears shortly.",
    "Zum Rudern ist kein WLAN nötig.": "Wi-Fi is not needed for rowing.",
    "Webportal öffnen": "Open web portal",
    "oder {url}": "or {url}",
    "S4 anschließen": "Connect the S4",
    "1. Monitor einschalten": "1. Switch on the monitor",
    "2. Kabel in den inneren Micro-USB-Anschluss der Box (beschriftet „USB“)": "2. Cable into the box's inner micro USB port (labelled “USB”)",
    "Der Schritt hakt sich von selbst ab.": "This step ticks itself off.",
    "App verbinden": "Connect the app",
    "In der App „WaterRower S4“ wählen, nicht in den Bluetooth-Einstellungen.": "Choose “WaterRower S4” in the app, not in the Bluetooth settings.",
    "Probeschlag": "Test stroke",
    "Zieh ein paar Schläge am Rudergerät.": "Take a few strokes on the rower.",
    "Alles bereit": "All set",
    "Viel Spaß beim Rudern.": "Enjoy your rowing.",
    "Übersprungenes lässt sich im Webportal unter Einstellungen nachholen.": "Skipped steps can be done later in the web portal under Settings.",
    "Kurz drücken: andere Sprache": "Press: other language",
    "Weiter in {n} s": "Continuing in {n} s",
    # ---------- Webportal ----------
    "Anmelden": "Sign in",
    "Das Passwort steht auf dem Display der Box unter „Info“.": "The password is shown on the box's display under “Info”.",
    "Live": "Live",
    "Logbuch": "Logbook",
    "Protokoll": "Logs",
    "Einstellungen": "Settings",
    "Abmelden": "Sign out",
    "nicht verbunden": "not connected",
    "Akku": "Battery",
    "unbekannt": "unknown",
    "Training läuft": "Rowing",
    "Distanz": "Distance",
    "Zeit": "Time",
    "Schlagzahl": "Stroke rate",
    "Leistung": "Power",
    "Große Anzeige öffnen": "Open big display",
    "Gerät": "Device",
    "Box neu starten?": "Restart the box?",
    "Neu starten": "Restart",
    "Box ausschalten? Wieder an: Taste am Akku, oder Strom neu einstecken.": "Switch off the box? To turn it on again: battery button, or plug the power back in.",
    "Ausschalten": "Shut down",
    "Zustand": "State",
    "Pace": "Pace",
    "Energie": "Energy",
    "Puls": "Heart rate",
    "Brücke aus": "Bridge off",
    "S4 nicht verbunden": "S4 not connected",
    "rudert": "rowing",
    "keine Verbindung zur Box": "no connection to the box",
    "Entfernen": "Remove",
    "Keine gespeicherten Netze": "No saved networks",
    "Gespeicherte Netze": "Saved networks",
    "Netz hinzufügen": "Add network",
    "Gefundene Netze": "Networks found",
    "– bitte wählen –": "– please choose –",
    "oder Name von Hand": "or enter the name",
    "z. B. MeinWLAN": "e.g. MyWiFi",
    "Speichern und verbinden": "Save and connect",
    "Die Box trennt dabei die aktuelle Verbindung. Ob es geklappt hat, steht auf dem Display.": "The box drops its current connection. The display shows whether it worked.",
    "Einheit": "session",
    "Einheiten": "sessions",
    "{n} {what}, {m} m gesamt": "{n} {what}, {m} m in total",
    "Start": "Start",
    "Export": "Export",
    "FIT passt für Strava, Garmin Connect, Intervals.icu und die meisten anderen; TCX nur, wo FIT nicht geht. Einheiten vor Version 1.2 haben keine Messpunkte und deshalb keinen Export.": "FIT works for Strava, Garmin Connect, Intervals.icu and most others; use TCX only where FIT does not work. Sessions from before version 1.2 have no data points and therefore no export.",
    "Herunterladen": "Download",
    "Löschen": "Delete",
    "Aufzeichnung beenden": "Stop recording",
    "Aufzeichnung starten": "Start recording",
    "Rohdaten vom S4 (Test)": "Raw data from the S4 (test)",
    "Zeichnet alle Meldungen des S4 auf, auch Sensorimpulse und Zuganfang/-ende. Nur zum Auswerten gedacht, am normalen Betrieb ändert sich nichts.": "Records every message from the S4, including sensor pulses and stroke start/end. Meant for analysis only; normal operation is unchanged.",
    "Läuft gerade.": "Recording now.",
    "Anzeige": "Display",
    "Sprache": "Language",
    "nur bei Problemen": "only on problems",
    "immer Zustandsfarbe": "always show state colour",
    "Display aus nach (Sekunden)": "Display off after (seconds)",
    "Zusammenfassung zeigen (Sekunden)": "Show summary for (seconds)",
    "Töne": "Sounds",
    "an": "on",
    "Hotspot": "Hotspot",
    "Eigenen Hotspot starten, wenn kein WLAN da ist": "Start own hotspot when no Wi-Fi is available",
    "ja": "yes",
    "nein": "no",
    "Name": "Name",
    "Passwort (auch für diese Seite, mind. 8 Zeichen)": "Password (also for this page, at least 8 characters)",
    "MQTT (optional)": "MQTT (optional)",
    "Sendet die Werte an einen MQTT-Server, z. B. für Home Assistant. Leer lassen = aus.": "Sends the values to an MQTT server, e.g. for Home Assistant. Leave empty = off.",
    "Server": "Server",
    "z. B. homeassistant.local": "e.g. homeassistant.local",
    "Port": "Port",
    "Benutzer": "User",
    "Topic": "Topic",
    "Zustand: {s}": "State: {s}",
    "Speichern": "Save",
    "Startet den Assistenten auf dem Display neu, zum Beispiel um übersprungene Schritte nachzuholen.": "Restarts the assistant on the display, for example to do skipped steps.",
    "Einrichtungsassistent neu starten": "Restart setup assistant",
    "nicht verfügbar (Paket python3-paho-mqtt fehlt)": "not available (package python3-paho-mqtt missing)",
    "verbinde mit {host} …": "connecting to {host} …",
    "Fehler: {err}": "Error: {err}",
    "verbunden mit {host}": "connected to {host}",
    "keine Verbindung zu {host}": "no connection to {host}",
    "Rudert": "Rowing",
    "Rohdaten": "Raw data",
    "Datei nicht gefunden.": "File not found.",
    "Keine Messpunkte für diese Einheit.": "No data points for this session.",
    "Nicht gefunden": "Not found",
    "Seite nicht gefunden.": "Page not found.",
    "Falsches Passwort. Bitte noch einmal.": "Wrong password. Please try again.",
    "Bitte ein Netz auswählen oder eingeben.": "Please choose or enter a network.",
    "Das Passwort muss 8 bis 63 Zeichen haben.": "The password must have 8 to 63 characters.",
    "Verbinde mit {ssid}": "Connecting to {ssid}",
    "Die Box trennt jetzt die bisherige Verbindung und wählt sich neu ein. Das Ergebnis steht in etwa einer Minute auf dem Display. Klappt es, ist die Box danach unter <b>{url}</b> im neuen Netz erreichbar. Klappt es nicht, öffnet sie wieder ihren Hotspot.": "The box now drops its current connection and reconnects. The result appears on the display in about a minute. If it works, the box is reachable at <b>{url}</b> in the new network. If not, it opens its hotspot again.",
    "Bitte nur Zahlen bei den Sekunden.": "Please use numbers only for the seconds.",
    "Gespeichert. Hotspot-Änderungen gelten beim nächsten Start des Hotspots.": "Saved. Hotspot changes apply the next time the hotspot starts.",
    "Der Assistent erscheint in wenigen Sekunden auf dem Display.": "The assistant appears on the display in a few seconds.",
    "Startet neu ...": "Restarting ...",
    "Schaltet aus ...": "Shutting down ...",
    "Verbinde mit {ssid} ...": "Connecting to {ssid} ...",
    "Speichern fehlgeschlagen: {err}": "Saving failed: {err}",
    "Verbunden mit {ssid}": "Connected to {ssid}",
    "Verbindung zu {ssid} fehlgeschlagen. Passwort prüfen.": "Connection to {ssid} failed. Check the password.",
    # Kraftkurve und Pause
    "Automatisches Ende in": "Automatic end in",
    "Kraftkurve": "Force curve",
    "Zug (s)": "drive (s)",
    "Rollphase (s)": "recovery (s)",
    "Noch keine Kraftkurve. Sie erscheint nach dem ersten Zug.": "No force curve yet. It appears after the first stroke.",
    "Pause – automatisches Ende in {t}": "Pause – automatic end in {t}",
    "Kraftkurve (letzter Zug orange)": "Force curve (last stroke in orange)",
    "Zug": "Drive",
    "Rollphase": "Recovery",
    "Verhältnis": "Ratio",
    "Anfang und Ende der Einheit": "Start and end of the session",
    "Grau: erstes Drittel der Schläge, orange: letztes Drittel.": "Grey: first third of the strokes, orange: last third.",
    "Orange: Durchschnitt, grau: jeder einzelne Zug ({n}). Die Kurve zeigt die Form des Zugs, die Spitze ist 100 %.": "Orange: average, grey: every single stroke ({n}). The curve shows the shape of the stroke, the peak is 100 %.",
    # Taktgeber und Live-Kurve
    "Taktgeber": "Pacer",
    "Live-Kurve": "Live wave",
    "Takt {spm}/min · fest": "Pace {spm}/min · fixed",
    "Takt {spm}/min · dein Rhythmus": "Pace {spm}/min · your rhythm",
    "Startet mit dem ersten Zug": "Starts with the first stroke",
    "Animation in der Trainingsansicht": "Animation in the training screen",
    "Takt des Taktgebers (Schläge/min, 0 = eigener Rhythmus)": "Pacer rate (strokes/min, 0 = your own rhythm)",
    "Ansichten auf dem Display": "Screens on the display",
    "Welche Ansichten die Display-Taste durchblättert. Die Automatik ist immer dabei, Info auch (dort steht das Passwort).": "Which screens the display button cycles through. The automatic screen is always included, and so is Info (it shows the password).",
    "Verbinden": "Connect",
    "Ein Netz ist doppelt gespeichert. Ein altes Profil mit falschen Einstellungen kann das automatische Verbinden stören; im Zweifel das ältere entfernen.": "A network is saved twice. An old profile with wrong settings can disturb connecting automatically; if in doubt, remove the older one.",
    # Reise und Trophaeen
    "Reise": "Journey",
    "km von {total}": "km of {total}",
    "Ziel erreicht": "Finish reached",
    "noch {km} km": "{km} km to go",
    "Trophäen: {n}": "Trophies: {n}",
    "Neue Trophäe": "New trophy",
    "erreicht": "reached",
    "{km} von {total} km": "{km} of {total} km",
    "Du bist hier": "You are here",
    "noch {km} km bis {name}": "{km} km to {name}",
    "Jeder geruderte Meter zählt. Es gibt kein Datum und keine Frist.": "Every metre you row counts. There is no date and no deadline.",
    "Trophäen": "Trophies",
    "Die erste Trophäe gibt es nach der ersten Einheit.": "The first trophy comes with the first session.",
    "Weitere Trophäen gibt es unterwegs zu entdecken.": "More trophies are waiting to be discovered along the way.",
    "Reise und Trophäen": "Journey and trophies",
    "Anzeigen": "Show",
    "Strecke (ein Wechsel beginnt eine neue Reise bei 0 km)": "Route (switching starts a new journey at 0 km)",
    "an {t}": "ETA {t}",
    "{km} km gerudert": "{km} km rowed",
    "Neue Strecke im Portal unter Einstellungen wählen.": "Choose a new route in the portal under Settings.",
    "ca. {t}": "≈ {t}",
    "Ziel": "Destination",
    "Nächste Etappe": "Next stage",
    "Ankunft beim Rudern nach deinem aktuellen Tempo, sonst nach deinem Schnitt der letzten vier Wochen ({km} km pro Tag), jeweils mit Puffer.": "Arrival while rowing is based on your current pace, otherwise on your average of the last four weeks ({km} km per day), always with some buffer.",
    "Eine Ankunftszeit gibt es, sobald ein paar Einheiten im Logbuch stehen.": "Arrival times appear once there are a few sessions in the logbook.",
    # Logbuch als CSV
    "Dauer_s": "Duration_s",
    "Distanz_m": "Distance_m",
    "Schlaege": "Strokes",
    "Watt_max": "Watts_max",
    "Pace_500m_s": "Pace_500m_s",
    "Puls_max": "HR_max",
    # Export
    "Indoor-Rudern (Rowbox)": "Indoor rowing (Rowbox)",
}


if __name__ == "__main__":
    # Prueft, ob jeder L("...")-Text in den Programmen eine englische Fassung hat
    import ast, glob, sys
    here = os.path.dirname(os.path.abspath(__file__))
    missing = []
    for path in glob.glob(os.path.join(here, "*.py")):
        for node in ast.walk(ast.parse(open(path).read())):
            fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None) \
                if isinstance(node, ast.Call) else None
            if (fname in ("L", "say", "mqtt_set") and node.args
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value not in EN):
                missing.append(f"{os.path.basename(path)}:{node.lineno}: {node.args[0].value!r}")
    print("\n".join(missing) or "Alle Texte übersetzt.")
    sys.exit(1 if missing else 0)
