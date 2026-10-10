#!/usr/bin/env bash
#
# connect_and_harden.sh
#
# EIN Befehl, den du ZU HAUSE auf einem Rechner IM SELBEN NETZ wie der TV
# ausführst. Er verbindet sich mit dem Bravia und startet die Härtung.
#
# WICHTIG: Das muss auf deinem Laptop/PC/Raspberry Pi im Heimnetz laufen –
# NICHT in einer Cloud/CI-Umgebung. Eine ADB-Verbindung zum TV ist nur aus
# demselben WLAN/LAN möglich.
#
# Benutzung:
#   ./connect_and_harden.sh                 # TV automatisch suchen, Dry-run
#   ./connect_and_harden.sh 192.168.178.42  # feste TV-IP, Dry-run
#   ./connect_and_harden.sh 192.168.178.42 --apply   # sichere Layer ausführen
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3 || true)"
TV_IP="${1:-}"
APPLY=""
[[ "${2:-}" == "--apply" || "${1:-}" == "--apply" ]] && APPLY="--apply"
[[ "${1:-}" == "--apply" ]] && TV_IP=""

log()  { printf '\033[36m[*]\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[-]\033[0m %s\n' "$*" >&2; exit 1; }

[[ -n "$PY" ]] || die "python3 nicht gefunden. Bitte Python 3 installieren."

# --- 1. adb sicherstellen -------------------------------------------------- #
if ! command -v adb >/dev/null 2>&1; then
  warn "adb (Android Platform Tools) fehlt. Installationsbefehl für dein System:"
  case "$(uname -s)" in
    Linux)  echo "    sudo apt install adb            # Debian/Ubuntu"
            echo "    sudo dnf install android-tools   # Fedora"
            echo "    sudo pacman -S android-tools     # Arch" ;;
    Darwin) echo "    brew install android-platform-tools" ;;
    *)      echo "    https://developer.android.com/tools/releases/platform-tools" ;;
  esac
  die "Nach der Installation dieses Skript erneut ausführen."
fi
ok "adb gefunden: $(command -v adb)"

# --- 2. TV finden (falls keine IP angegeben) ------------------------------- #
if [[ -z "$TV_IP" ]]; then
  log "Keine IP angegeben – suche den TV im Heimnetz ..."
  # diagnose gibt JSON; erste Kandidaten-IP mit offenem ADB-Port herausziehen.
  REPORT="$("$PY" "$HERE/firetv_bravia_connect.py" diagnose --no-connect --json 2>/dev/null || true)"
  TV_IP="$("$PY" - "$REPORT" <<'PYEOF'
import json, sys
try:
    data = json.loads(sys.argv[1] or "{}")
except Exception:
    data = {}
best = ""
for h in data.get("hosts", []):
    if 5555 in h.get("open_ports", []) and h.get("device_type") in ("firetv","bravia","androidtv"):
        best = h["ip"]; break
print(best)
PYEOF
)"
  [[ -n "$TV_IP" ]] || die "Kein Gerät mit offenem ADB-Port (5555) gefunden.
    -> Am TV ADB-Debugging aktivieren (Einstellungen > Geräteeinstellungen >
       Entwickleroptionen > Netzwerk-Debugging) und erneut versuchen,
       oder die IP direkt angeben: ./connect_and_harden.sh <TV-IP>"
  ok "TV-Kandidat gefunden: $TV_IP"
fi

# --- 3. Verbinden ---------------------------------------------------------- #
log "Verbinde per ADB mit $TV_IP ..."
"$PY" "$HERE/firetv_bravia_connect.py" connect "$TV_IP" || \
  die "Verbindung fehlgeschlagen. Am TV das Debugging-Popup 'Zulassen' bestätigen
    und erneut versuchen."

# --- 4. Härten ------------------------------------------------------------- #
if [[ -n "$APPLY" ]]; then
  warn "MODUS: --apply — sichere Layer (Telemetrie aus, optional Bluetooth) werden AUSGEFÜHRT."
else
  log "MODUS: Dry-run — es wird nur angezeigt, was getan würde."
fi
"$PY" "$HERE/bravia_harden.py" --serial "${TV_IP}:5555" run $APPLY

echo
ok "Fertig."
if [[ -z "$APPLY" ]]; then
  echo "    Zum echten Ausführen der sicheren Layer:"
  echo "        ./connect_and_harden.sh $TV_IP --apply"
fi
echo "    Alte Apps entfernen (Vorschlag ansehen):"
echo "        $PY $HERE/bravia_harden.py --serial ${TV_IP}:5555 apps --recommend"
echo "    Zum Schluss ADB wieder schließen:"
echo "        $PY $HERE/bravia_harden.py --serial ${TV_IP}:5555 lockdown --apply"
