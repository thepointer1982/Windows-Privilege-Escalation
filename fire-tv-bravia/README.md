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
