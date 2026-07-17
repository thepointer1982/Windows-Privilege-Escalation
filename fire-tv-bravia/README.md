# Fire TV ↔ Sony Bravia – Netzwerkverbindung

Netzwerk- und Diagnose-Tool, um über einen Fire-TV-Stick am Sony-Bravia-Fernseher
die Verbindung im Heimnetz herzustellen und zu prüfen.

Fire TV und moderne Sony-Bravia-Geräte laufen beide auf **Android TV**. Der
saubere Steuerungsweg ist deshalb **ADB over Network (TCP 5555)**. Die Bravia
bietet zusätzlich eine eigene Steuerschnittstelle (**Simple IP Control** auf
TCP 20060 und die **Scalar Web API** auf Port 80).

## Wie es funktioniert

Das Tool `firetv_bravia_connect.py` arbeitet in Stufen:

1. **Subnetz ermitteln** – aus der lokalen IP wird automatisch das `/24`-Netz
   abgeleitet (oder per `--subnet` vorgeben).
2. **Diagnose / Discovery** – Ping-Sweep + gezieltes TCP-Port-Probing der
   relevanten Ports:

   | Port  | Dienst              | Deutet auf        |
   |-------|---------------------|-------------------|
   | 5555  | ADB over Network    | Fire TV / Bravia  |
   | 5556  | ADB-over-TLS        | Android 11+       |
   | 8009  | Google Cast         | Android TV        |
   | 8008/7000 | Fire-TV-Companion | Fire TV         |
   | 20060 | Sony Simple IP      | Bravia            |
   | 80    | Scalar Web API/DIAL | Bravia            |

   Optional zusätzlich **mDNS-Discovery** (`--mdns`, benötigt `zeroconf`).
3. **Klassifizieren** – jeder erreichbare Host wird als Fire TV / Bravia /
   Android TV / generisch eingeordnet (offene Ports + Hostname).
4. **Verbinden** – `adb connect <ip>:5555`, danach Verifikation über
   `adb devices` und Auslesen des Gerätemodells.

## Voraussetzungen

- **Python 3.8+** (nur Standardbibliothek erforderlich).
- **adb** (Android Platform Tools) für die eigentliche Verbindung:
  - Debian/Ubuntu: `sudo apt install adb`
  - macOS: `brew install android-platform-tools`
  - Windows: [Platform Tools](https://developer.android.com/tools/releases/platform-tools)
- Optional **zeroconf** für mDNS: `pip install zeroconf`

## Am Fernseher vorbereiten (einmalig)

ADB-Debugging muss am Zielgerät aktiviert sein:

- **Fire TV:** Einstellungen → *Mein Fire TV* → *Entwickleroptionen* →
  *ADB-Debugging* aktivieren.
- **Sony Bravia (Android TV):** Einstellungen → *Geräteeinstellungen* →
  *Info* → mehrmals auf *Build* tippen (Entwickleroptionen freischalten), dann
  *Entwickleroptionen* → *Netzwerk-Debugging / USB-Debugging* aktivieren.

Beim ersten `adb connect` erscheint am TV ein Popup **„USB-Debugging zulassen?"** –
dort bestätigen (RSA-Fingerprint), sonst bleibt die Verbindung `unauthorized`.

## Benutzung

```bash
# Kompletter Durchlauf: scannen, klassifizieren, besten Kandidaten verbinden
python3 firetv_bravia_connect.py diagnose

# Nur scannen und auflisten (Subnetz explizit)
python3 firetv_bravia_connect.py scan --subnet 192.168.178.0/24

# Nur diagnostizieren, nicht automatisch verbinden
python3 firetv_bravia_connect.py diagnose --no-connect

# Gezielt mit einer bekannten IP verbinden
python3 firetv_bravia_connect.py connect 192.168.178.42

# Mit mDNS-Discovery (falls zeroconf installiert)
python3 firetv_bravia_connect.py scan --mdns

# Maschinenlesbare Ausgabe
python3 firetv_bravia_connect.py diagnose --json > report.json
```

### Optionen (scan / diagnose)

| Option        | Bedeutung                                            |
|---------------|------------------------------------------------------|
| `--subnet`    | CIDR-Subnetz, z. B. `192.168.178.0/24`               |
| `--timeout`   | Timeout pro Port-Probe (Sekunden, Standard 0.6)      |
| `--workers`   | Parallele Scan-Threads (Standard 100)                |
| `--mdns`      | Zusätzlich mDNS-Discovery                             |
| `--mdns-time` | Dauer der mDNS-Discovery (Sekunden)                  |
| `--json`      | Ausgabe als JSON (nur Daten auf stdout)              |
| `--no-connect`| (nur `diagnose`) nur diagnostizieren, nicht verbinden|

## Beispiel-Ausgabe

```
[*] Lokale IP: 192.168.178.20   Subnetz: 192.168.178.0/24
[*] adb verfuegbar: ja   ping verfuegbar: ja
[*] Scanne 254 Adressen in 192.168.178.0/24 (Ports: 5555, 20060, ...) ...

[+] 6 erreichbare Hosts:
  192.168.178.42 (fire-tv-wohnzimmer)
      Typ       : Fire TV  [Konfidenz: medium]
      Ports     : 5555/adb, 8009/googlecast
      Hinweis   : ADB (5555) offen - per adb verbindbar

[+] TV-Kandidaten (priorisiert):
  - 192.168.178.42   firetv     [medium]

[*] Bester Kandidat: 192.168.178.42 (firetv) - versuche ADB-Verbindung.
[+] Verbindung hergestellt zu 192.168.178.42 (AFTKA)
```

## Hinweise

- Das Tool scannt nur das **eigene Heimnetz** und stellt eine autorisierte
  ADB-Verbindung her (Bestätigung am TV erforderlich). Es umgeht keine
  Zugangssicherung.
- Findet der Scan keinen offenen Port 5555, ist am Zielgerät meist das
  ADB-Debugging noch nicht aktiviert (siehe oben).
- Ein WLAN mit **Client-Isolation / AP-Isolation** blockiert die Verbindung
  zwischen Geräten – dann Isolation im Router deaktivieren oder beide Geräte
  per LAN verbinden.

---

# Bravia optimieren & härten (`bravia_harden.py`)

Für den Fall „der TV hat seit Jahren keine Updates bekommen": Schicht für
Schicht entschlacken und die Angriffsfläche reduzieren – über die gleiche
ADB-Verbindung.

> **Voraussetzung:** Der Bravia muss ein **Android TV** sein (ca. ab Modelljahr
> 2015). Pre-2015-Bravia laufen auf Sonys altem Linux/Opera-System **ohne ADB** –
> dort greift dieses Tool nicht. Prüfen: `firetv_bravia_connect.py connect <ip>`;
> klappt die ADB-Verbindung, ist es Android TV.

## Sicherheitsprinzipien

- **DRY-RUN ist Standard.** Jeder Befehl zeigt zunächst nur, *was* er täte.
  Echte Änderungen erst mit `--apply`.
- **Alles reversibel.** `pm disable-user` / `uninstall --user 0` lässt sich per
  `enable` / `install-existing` zurückholen. Kein Root, kein Flashen.
- **Harte Sperrliste.** Kritische System-Pakete (SystemUI, Settings, Launcher,
  WebView, Play-Services-Kern, Sony-TV-Framework …) werden nie angefasst – nur
  mit `--force`, und mit deutlicher Warnung.

## Was es NICHT tut (bewusst)

| Wunsch | Warum nicht |
|--------|-------------|
| Service-Menü öffnen | Nur per Fernbedienungs-Code, nicht übers Netz. Kann Panel/Weißabgleich zerstören → nur Warnhinweis. |
| „Treiber ziehen" | Auf SoC-TVs gibt es keine austauschbaren Treiber – kein realer Hebel. |
| WLAN am Router härten | Das ist Router-Sache. `wlan` liefert dafür eine Checkliste. |

## Ein-Befehl-Bootstrap (zu Hause ausführen)

> **Muss auf einem Rechner im selben Netz wie der TV laufen** (Laptop/PC/Raspberry
> Pi im Heim-WLAN/LAN) – **nicht** in einer Cloud/CI-Umgebung. Eine ADB-Verbindung
> zum TV geht nur aus demselben Netz.

```bash
./connect_and_harden.sh                 # TV automatisch suchen, Dry-run
./connect_and_harden.sh 192.168.178.42  # feste TV-IP, Dry-run
./connect_and_harden.sh 192.168.178.42 --apply   # sichere Layer ausführen
```

Das Skript prüft/erklärt `adb`, findet den TV (oder nimmt die angegebene IP),
verbindet per ADB und startet den Härtungs-Durchlauf. Details der einzelnen
Schritte siehe unten.

## Schnellstart: orchestrierter Durchlauf

Ein Befehl geht alle Schichten der Reihe nach durch (Dry-run als Standard):

```bash
python3 bravia_harden.py run              # zeigt alles, ändert nichts
python3 bravia_harden.py run --apply      # führt die SICHEREN Layer aus
```

`run` führt mit `--apply` nur die risikoarmen Layer wirklich aus
(Telemetrie-Pakete aus, optional Bluetooth). **App-Entfernung** bleibt bewusst
eine eigene Entscheidung – `run` zeigt dafür nur das fertige
`apps --uninstall …`-Kommando an. Der finale **Lockdown** (kappt die
ADB-Verbindung) läuft nur mit `--include-lockdown`.

## Ablauf – Schicht für Schicht (manuell, volle Kontrolle)

```bash
# 0. Verbinden (siehe oben)
python3 firetv_bravia_connect.py connect 192.168.178.42

# 1. Inventur: Gerät, Security-Patch-Level, klassifizierte Pakete, Settings
python3 bravia_harden.py audit

# 2. Alte/ungenutzte Apps ansehen und entfernen (reversibel, dry-run zuerst)
python3 bravia_harden.py apps --recommend   # kuratierter Entfernen-Vorschlag
python3 bravia_harden.py apps --list        # alle Kandidaten klassifiziert
python3 bravia_harden.py apps --uninstall com.netflix.ninja,com.spotify.tv.android
python3 bravia_harden.py apps --uninstall com.netflix.ninja,com.spotify.tv.android --apply
#   Zurückholen:
python3 bravia_harden.py apps --restore com.netflix.ninja --apply

# 3. Telemetrie/Tracking abschalten + Datenschutz-Checkliste
python3 bravia_harden.py privacy --disable-telemetry --apply

# 4. Unnötige Funk-Dienste (z.B. Bluetooth, falls keine BT-Fernbedienung)
python3 bravia_harden.py services --disable-bluetooth --apply

# 5. WLAN/Netzwerk router-seitig härten (Checkliste, ändert nichts am TV)
python3 bravia_harden.py wlan

# 6. Zum Schluss: Netzwerk-ADB wieder zu (Angriffsfläche schließen)
python3 bravia_harden.py lockdown --apply
```

## Paket-Klassifizierung

`audit` / `apps --list` lesen die real installierten Pakete aus und ordnen sie ein:

| Kategorie   | Bedeutung |
|-------------|-----------|
| `critical`  | Sperrliste – nie automatisch anfassen. |
| `keep`      | System, nicht kritisch, unauffällig – bleibt. |
| `optional`  | Vorinstallierte/installierte Apps (Netflix, Spotify …) – frei entfernbar. |
| `telemetry` | Namensmuster deutet auf Tracking/Ads/Analytics – Kandidat zum Abschalten. |

Deine Haupt-Ziele für „alte Programme runter" sind die `optional`-Apps.

## Tests

Die Logik (Klassifizierung, Sperrliste, Empfehlungen, Dry-run/Apply) ist ohne
echtes Gerät testbar – der ADB-Layer wird gemockt:

```bash
python3 test_bravia_harden.py       # stdlib unittest, keine Abhängigkeiten
# oder: pytest test_bravia_harden.py
```

## Reversibilität / Notfall

- App versehentlich entfernt → `apps --restore <pkg> --apply`.
- App deaktiviert → `apps --enable <pkg> --apply`.
- Alles zurücksetzen → am TV Werksreset (letzte Instanz).
- **Nie** `--force` gegen Sperrlisten-Pakete verwenden, außer du weißt genau,
  was das Paket ist – das kann den TV soft-bricken.
