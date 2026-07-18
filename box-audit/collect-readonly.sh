#!/usr/bin/env sh
# =============================================================================
# collect-readonly.sh  --  Amlogic-Box Read-Only Evidence Collector
# -----------------------------------------------------------------------------
# Zweck: Sammelt AUSSCHLIESSLICH lesend die Fakten fuer Gate 0/1/3/4
#        (Board-Identitaet, Device-Tree, Sicherheits-Posture, OTA-Oberflaeche).
#
# Sicherheitsgarantien (by construction):
#   * Laeuft auf dem PC, nicht auf der Box. Ausgabe geht auf PC-stdout.
#   * Es wird NICHTS auf das Geraet geschrieben.
#   * Es werden KEINE eMMC-/Storage-Bloecke gelesen (kein dd, kein cat auf
#     /dev/block/*). Nur getprop, /proc, /sys/.../device-tree, Paket-/Settings-
#     Abfragen und das LISTEN (nicht Lesen) der by-name-Symlinks.
#   * Kein Reboot, kein Flash, kein Schreibzugriff auf 1,8-TB-Volume.
#
# Nutzung:
#   1) USB-Debugging auf der Box aktiv, Box per USB am PC, `adb devices` zeigt sie.
#   2) sh collect-readonly.sh > box-evidence-$(date +%Y%m%d).txt
#   3) Die erzeugte Textdatei zur Auswertung weitergeben.
#
# Root ist NICHT erforderlich. Einige Zeilen liefern ohne Root ggf. leer/Fehler
# -- das ist erwartet und unkritisch; das Skript laeuft weiter.
# =============================================================================

set -u

ADB="${ADB:-adb}"

hr()  { printf '\n===== %s =====\n' "$1"; }
sub() { printf '\n--- %s ---\n' "$1"; }

# run <beschreibung> <shell-kommando-auf-der-box>
# Fuehrt ein reines Lesekommando via `adb shell` aus. Fehler werden angezeigt,
# brechen das Skript aber nicht ab.
run() {
    desc="$1"; shift
    sub "$desc"
    # 2>&1, damit auch Permission-Hinweise sichtbar sind (Diagnose-Wert).
    "$ADB" shell "$@" 2>&1 || printf '[kein Ergebnis / nicht verfuegbar]\n'
}

# --- Vorbedingungen -----------------------------------------------------------
if ! command -v "$ADB" >/dev/null 2>&1; then
    echo "FEHLER: '$ADB' nicht gefunden. adb installieren oder ADB=/pfad/zu/adb setzen." >&2
    exit 1
fi

printf '########################################################################\n'
printf '# Amlogic-Box Read-Only Evidence  --  erzeugt: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf '# NUR LESEND. Keine Geraeteaenderung, kein Flash, kein eMMC-Zugriff.\n'
printf '########################################################################\n'

hr "ADB-Verbindung"
"$ADB" devices -l 2>&1 || { echo "FEHLER: adb nicht ansprechbar." >&2; exit 1; }

# =============================================================================
# GATE 0 -- Board- und SoC-Identitaet (bestimmt Image + dtb eindeutig)
# =============================================================================
hr "GATE 0  Identitaet"
run "Modell"        getprop ro.product.model
run "Device"        getprop ro.product.device
run "Brand"         getprop ro.product.brand
run "Build-Fingerprint" getprop ro.build.fingerprint
run "SoC-Plattform (Familie)" getprop ro.board.platform
run "Android-Version" getprop ro.build.version.release
run "Hardware"      getprop ro.hardware

hr "GATE 0  Device-Tree (Ground Truth fuer dtb-Matching)"
run "DT model"      cat /proc/device-tree/model
run "DT compatible" cat /proc/device-tree/compatible
run "amlogic-dt-id" cat /proc/device-tree/amlogic-dt-id

hr "GATE 0  CPU / RAM"
run "cpuinfo"       cat /proc/cpuinfo
run "meminfo (Kopf)" cat /proc/meminfo

hr "GATE 0  Partitionslayout (nur Namen listen, KEIN Inhalt gelesen)"
# 'ls -l' listet die by-name-Symlinks. Es wird KEIN Blockgeraet geoeffnet.
run "by-name Symlinks" 'ls -l /dev/block/platform/*/by-name/ 2>/dev/null || ls -l /dev/block/by-name/ 2>/dev/null'
run "/proc/partitions" cat /proc/partitions

# =============================================================================
# GATE 3 -- Sicherheits-Posture des laufenden Android (Angriffsflaeche)
# =============================================================================
hr "GATE 3  Sicherheits-Posture"
run "SELinux-Modus" getenforce
run "ro.secure"     getprop ro.secure
run "ro.adb.secure" getprop ro.adb.secure
run "ADB aktiviert (global)" settings get global adb_enabled
run "Netz-ADB Port (LEER erwartet!)" getprop service.adb.tcp.port
run "AVB/verity-Modus" getprop ro.boot.veritymode
run "Boot-Cmdline"  cat /proc/cmdline

# =============================================================================
# GATE 4 -- OTA-Oberflaeche (read-only erkennen, NICHT kontaktieren)
# =============================================================================
hr "GATE 4  OTA-Oberflaeche"
run "OTA/Update Properties" 'getprop | grep -iE "ota|update|otaupdate" || echo "(keine)"'
run "Update-/OTA-Pakete"    'pm list packages 2>/dev/null | grep -iE "ota|update|fota" || echo "(keine gefunden / kein pm)"'

# =============================================================================
hr "FERTIG"
printf 'Alle Abfragen waren reine Lesezugriffe. Es wurde nichts geaendert.\n'
printf 'Ausgabe zur Auswertung weitergeben (Board-Match, dtb-Kandidaten, SB-Sequenz).\n'
