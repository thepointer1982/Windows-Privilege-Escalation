#!/usr/bin/env sh
# =============================================================================
# backup-emmc.sh  --  Phase-0 eMMC-Voll-Dump (Rücksetz-Versicherung)
# -----------------------------------------------------------------------------
# LÄUFT IM VON SD GEBOOTETEN LINUX (CoreELEC/LibreELEC/Armbian), als root.
# Liest die interne eMMC byteweise und schreibt das Abbild auf ein EXTERNES
# Medium. Die eMMC selbst wird NUR GELESEN.
#
# Sicherheit:
#   * Schreibt ausschliesslich auf das per Argument angegebene Ausgabeverzeichnis.
#   * Weigert sich, wenn das Ausgabeverzeichnis auf der eMMC selbst liegt.
#   * Fasst KEINE Partition zum Schreiben an. Kein Flash, kein Reboot.
#
# Nutzung:
#   sh backup-emmc.sh /pfad/zu/externem/medium
#   (z. B. /media/usb  oder  /storage/<usb-label>)
# =============================================================================

set -eu

OUTDIR="${1:-}"
if [ -z "$OUTDIR" ]; then
    echo "Nutzung: sh backup-emmc.sh <ausgabeverzeichnis-auf-externem-medium>" >&2
    exit 1
fi
if [ ! -d "$OUTDIR" ]; then
    echo "FEHLER: '$OUTDIR' ist kein Verzeichnis." >&2
    exit 1
fi

# --- eMMC erkennen -----------------------------------------------------------
# Die eMMC hat immer einen boot0-Bereich (/dev/mmcblkXboot0). Eine SD-Karte
# hat das nicht. Darüber unterscheiden wir eMMC von der Boot-SD.
EMMC=""
for b in /dev/mmcblk*boot0; do
    [ -e "$b" ] || continue
    cand="${b%boot0}"          # /dev/mmcblk1boot0 -> /dev/mmcblk1
    EMMC="$cand"
    break
done

if [ -z "$EMMC" ]; then
    echo "FEHLER: keine eMMC (mmcblkXboot0) gefunden. Läuft dies wirklich auf der Box?" >&2
    echo "Vorhandene Blockgeräte:" >&2
    ls -l /dev/mmcblk* 2>/dev/null >&2 || true
    exit 1
fi

# --- Schutz: Ausgabe darf nicht auf der eMMC liegen --------------------------
OUT_SRC="$(df -P "$OUTDIR" 2>/dev/null | awk 'NR==2{print $1}')"
case "$OUT_SRC" in
    ${EMMC}*)
        echo "ABBRUCH: Ausgabeverzeichnis liegt auf der eMMC ($OUT_SRC)." >&2
        echo "Ein externes Medium (USB/SD) als Ziel angeben." >&2
        exit 1
        ;;
esac

STAMP="$(date -u '+%Y%m%d-%H%M%S')"
IMG="$OUTDIR/emmc-full-$STAMP.img"
SUM="$IMG.sha256"

echo "eMMC-Quelle : $EMMC   (nur lesend)"
echo "Ausgabe     : $IMG    (auf $OUT_SRC)"
echo
printf 'Voll-Dump jetzt starten? Das kann je nach Grösse lange dauern. [j/N] '
read -r ans
case "$ans" in
    j|J|y|Y) : ;;
    *) echo "Abgebrochen."; exit 0 ;;
esac

echo "Lese eMMC ..."
dd if="$EMMC" of="$IMG" bs=8M conv=noerror,sync status=progress

echo "Bilde Prüfsumme ..."
( cd "$OUTDIR" && sha256sum "$(basename "$IMG")" > "$(basename "$SUM")" )

echo
echo "FERTIG."
echo "Abbild : $IMG"
echo "Hash   : $SUM"
echo "Verifizieren: ( cd '$OUTDIR' && sha256sum -c '$(basename "$SUM")' )"
echo "Dieses Abbild + das Amlogic USB Burning Tool sind dein Rückweg."
