#!/usr/bin/env sh
# =============================================================================
# verify-image.sh  --  Hash- und Signaturprüfung eines Linux-Images (Phase 2)
# -----------------------------------------------------------------------------
# Läuft auf dem PC. Reine Prüfung, verändert nichts am Image.
#
# Nutzung:
#   sh verify-image.sh <image> <sha256|SHA256SUMS-datei> [<signatur.asc> <fingerprint|keyring>]
#
# Beispiele:
#   sh verify-image.sh CoreELEC.img 3f2a...9c            # direkter Hash
#   sh verify-image.sh CoreELEC.img SHA256SUMS           # Summendatei
#   sh verify-image.sh SHA256SUMS SHA256SUMS SHA256SUMS.asc 0xDEADBEEF...
#
# Prüfstufen:
#   1) SHA256 des Images gegen erwarteten Wert / gegen SHA256SUMS.
#   2) (optional) PGP-Signatur der Summendatei gegen den publizierten
#      Projekt-Fingerprint  ->  erst DAS macht den Hash vertrauenswürdig.
# =============================================================================

set -eu

IMG="${1:-}"; EXPECT="${2:-}"; SIG="${3:-}"; KEYREF="${4:-}"

if [ -z "$IMG" ] || [ -z "$EXPECT" ]; then
    echo "Nutzung: sh verify-image.sh <image> <sha256|SHA256SUMS> [<sig.asc> <fingerprint|keyring>]" >&2
    exit 1
fi
[ -f "$IMG" ] || { echo "FEHLER: Image '$IMG' nicht gefunden." >&2; exit 1; }

# --- Stufe 1: SHA256 ---------------------------------------------------------
ACTUAL="$(sha256sum "$IMG" | awk '{print $1}')"
echo "Image-SHA256: $ACTUAL"

if [ -f "$EXPECT" ]; then
    # EXPECT ist eine SHA256SUMS-Datei: passende Zeile suchen.
    # Dateinamen LITERAL vergleichen (nicht als Regex interpolieren, sonst
    # matcht z. B. '.' jedes Zeichen). awk trennt Hash ($1) und Name ($2,
    # fuehrendes '*' des Binaer-Markers entfernt).
    WANT="$(awk -v f="$(basename "$IMG")" '
        { name=$2; sub(/^[*]/,"",name) }
        name==f && $1 ~ /^[0-9a-fA-F]{64}$/ { print tolower($1); exit }
    ' "$EXPECT" 2>/dev/null)"
    if [ -z "$WANT" ]; then
        echo "FEHLER: kein passender Eintrag für '$(basename "$IMG")' in '$EXPECT'." >&2
        exit 2
    fi
else
    WANT="$(printf '%s' "$EXPECT" | tr 'A-F' 'a-f')"
fi
echo "Erwartet    : $WANT"

if [ "$(printf '%s' "$ACTUAL" | tr 'A-F' 'a-f')" = "$WANT" ]; then
    echo "STUFE 1 OK: SHA256 stimmt überein."
else
    echo "STUFE 1 FEHLER: SHA256 weicht ab -- Image NICHT verwenden." >&2
    exit 3
fi

# --- Stufe 2: PGP-Signatur (optional) ----------------------------------------
if [ -n "$SIG" ]; then
    if ! command -v gpg >/dev/null 2>&1; then
        echo "WARNUNG: gpg nicht installiert -- Signatur ungeprüft." >&2
        exit 4
    fi
    [ -f "$SIG" ] || { echo "FEHLER: Signaturdatei '$SIG' nicht gefunden." >&2; exit 4; }
    # Signatur bezieht sich auf die Summendatei, wenn EXPECT eine Datei ist,
    # sonst auf das Image.
    SIGNED_TARGET="$EXPECT"; [ -f "$SIGNED_TARGET" ] || SIGNED_TARGET="$IMG"

    echo "Prüfe Signatur '$SIG' über '$SIGNED_TARGET' ..."
    if gpg --verify "$SIG" "$SIGNED_TARGET" 2>&1 | tee /tmp/verify-gpg.$$ ; then
        if [ -n "$KEYREF" ]; then
            # Fingerprint-Abgleich auf BEIDEN Seiten normalisieren: Leerzeichen/
            # Tabs und ein fuehrendes 0x entfernen, Gross-/Kleinschreibung
            # angleichen. gpg gibt den Fingerprint in 4er-Gruppen mit
            # Leerzeichen aus -- ohne diese Normalisierung schlug der Abgleich
            # auch bei korrektem Schluessel fehl.
            norm_out="$(tr -d ' \t' < /tmp/verify-gpg.$$ | tr 'A-F' 'a-f')"
            norm_key="$(printf '%s' "$KEYREF" | sed 's/^0[xX]//' | tr -d ' \t' | tr 'A-F' 'a-f')"
            case "$norm_out" in
                *"$norm_key"*)
                    echo "STUFE 2 OK: Signatur gültig und Fingerprint passt." ;;
                *)
                    echo "STUFE 2 WARNUNG: Signatur gültig, aber Fingerprint '$KEYREF' nicht bestätigt -- prüfen!" >&2
                    rm -f /tmp/verify-gpg.$$; exit 5 ;;
            esac
        else
            echo "STUFE 2 OK: Signatur gültig (kein Fingerprint zum Abgleich angegeben)."
        fi
    else
        echo "STUFE 2 FEHLER: Signatur ungültig -- Image NICHT verwenden." >&2
        rm -f /tmp/verify-gpg.$$; exit 5
    fi
    rm -f /tmp/verify-gpg.$$
else
    echo "Hinweis: keine Signatur angegeben. Ein Hash ohne Signatur belegt nur"
    echo "Integrität, nicht Herkunft. Wenn das Projekt Signaturen anbietet, nutzen."
fi

echo "FERTIG: Verifikation bestanden."
