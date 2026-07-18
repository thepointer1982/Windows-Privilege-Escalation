# Box-Audit — Amlogic-Box → Linux, non-destruktiv

Arbeitsstand des Box-Workstreams (Branch `claude/box-ota-audit-stability-an15ud`).
Dieses Verzeichnis enthält **kein** fabriziertes Audit-Ergebnis, sondern das
Werkzeug und das Entscheidungsgerüst, um die noch offenen Fakten **read-only am
Gerät** selbst zu erheben.

> **Grundregel:** keine Geräteänderung, kein Flash, kein Reboot-Zwang, kein
> eMMC-/1,8-TB-Zugriff. Das Sammelskript hält diese Regel by construction ein.

## Warum ein generisches Linux-Image bisher „gesperrt" schien

Die Boot-Reihenfolge (eMMC vor SD) ist **nicht** der eigentliche Blocker. Der
non-destruktive Umweg ist die **Toothpick-/Reset-Methode**: Reset gedrückt
halten, Strom anlegen, nach einigen Sekunden loslassen → die BootROM überspringt
eMMC und bootet vom SD-Image; Android auf der eMMC bleibt unangetastet
(SD raus → Android, SD rein → Linux).

Die realen Blocker sind zwei andere:

1. **Device Tree (dtb):** ein Image bootet nur mit einem dtb, das zur *exakten*
   Platine passt (SoC-Variante, RAM, WLAN-/Ethernet-PHY).
2. **Secure Boot (efuse):** entscheidet, ob ein *unsigniertes* U-Boot von SD
   akzeptiert wird.

## Vier Sicherheitsebenen sauber trennen

| Ebene | Was es ist | Prüfung | Relevanz |
|---|---|---|---|
| **SoC Secure Boot** | efuse-erzwungene Signaturkette | UART-Bootlog / Verhaltenstest | entscheidet Boot **B** |
| **AVB / dm-verity** | Android Verified Boot der eMMC-Partitionen | `ro.boot.veritymode` | betrifft nur Android |
| **ADB-Sicherheit** | `ro.adb.secure`, Netz-ADB Port 5555 | `service.adb.tcp.port` | Angriffsfläche jetzt |
| **SELinux** | MAC Enforcing/Permissive | `getenforce` | Angriffsfläche jetzt |

`ro.secure` ist **ADB**, nicht SoC-Secure-Boot — diese Verwechslung ist der
häufigste Fehler.

## Zwei Boot-Ketten — die eigentliche Weggabelung

- **Weg A — Box-Image (CoreELEC/LibreELEC):** bringt einen **Amlogic-signierten**
  Bootloader mit, läuft daher auch auf vielen secure-gefusten Boxen; dtb kommt
  aus dem Box-Ökosystem. Toleranter, pragmatischer Weg.
- **Weg B — Mainline/Armbian:** unsigniertes Mainline-U-Boot + `meson-*.dtb`.
  Setzt voraus, dass Secure Boot **nicht** erzwungen ist. Sauber, aber empfindlich.

Secure Boot ist damit **kein** absoluter Blocker: Weg A umgeht ihn per Signatur,
nur Weg B verlangt SB=aus.

## Recovery-Boden ZUERST (stärker als jeder zweite SD-Trick)

Vor dem ersten Linux-Versuch absichern — beides non-destruktiv erzeugbar:

1. **Original-Android-Firmware** der exakten Box beschaffen (falls verfügbar) und
   mit Prüfsumme ablegen.
2. Die **BootROM-USB-Ebene (MaskROM / „WorldCup Device")** kennen: kommt die
   eMMC-Bootstufe nicht zustande, flasht das **Amlogic USB Burning Tool** ein
   komplettes Original-`.img` zurück — auch bei korruptem Bootloader.

→ Mit einem Original-Image ist die Box faktisch **nicht brickbar**. Auf einem
secure-gefusten Board lässt sich über USB Burning Tool allerdings nur das
**signierte Original** zurückspielen (für Recovery ausreichend).

## dtb-Namensschema (Mainline)

| `ro.board.platform` | Mainline-dtb-Präfix |
|---|---|
| gxbb (S905) | `meson-gxbb-*.dtb` |
| gxl (S905X/W/D) | `meson-gxl-s905x-*.dtb` |
| gxm (S912) | `meson-gxm-*.dtb` |
| g12a (S905X2) | `meson-g12a-*.dtb` |
| sm1 (S905X3) | `meson-sm1-*.dtb` |

Der `compatible`-String aus `collect-readonly.sh` nennt das Amlogic-Referenz-
Baseboard (p212, q200, …) — danach wird gematcht, nicht nach dem Verkaufsnamen.

## Hash-Disziplin (wenn Image feststeht)

1. Download über TLS von der **Projekt**-Quelle.
2. `sha256sum` gegen die **projektveröffentlichte** Prüfsumme.
3. Die Prüfsummendatei per **PGP-Signatur** gegen den publizierten
   Key-Fingerprint verifizieren.
4. Board-Variante muss zum Image-Namen passen (S905X ≠ S905X3).

## Go/No-Go

```
Recovery-Boden gesichert? (Original-IMG vorhanden)   ── nein ─► STOP, zuerst das
        │ ja
        ▼
Ziel = nur Linux booten?
   ├─ ja  ─► Weg A (signiertes Box-Image); Blocker = korrektes Box-dtb
   └─ mainline nötig ─► Secure-Boot-Status klären (UART/Verhaltenstest)
             ├─ SB AN  ─► Weg B gesperrt; bei Weg A bleiben
             └─ SB AUS ─► Weg B offen; exaktes meson-dtb nötig
        │
        ▼
Toothpick-SD-Boot (eMMC unangetastet)
        │
   Box meldet sich als „WorldCup Device" statt zu booten?
        └─ ja ─► SD-BL nicht akzeptiert; NICHTS geschrieben,
                 Original per USB Burning Tool rückspielbar
```

## Nutzung des Sammelskripts

```sh
# USB-Debugging aktiv, Box am PC, `adb devices` zeigt sie:
sh box-audit/collect-readonly.sh > box-evidence-$(date +%Y%m%d).txt
```

Das Skript ist reine Lektüre (getprop, /proc, device-tree, Paket-/Settings-
Abfragen, Listen der by-name-Symlinks). Es liest **keine** Blockgeräte und
schreibt **nichts** auf die Box.

## Offen, in dieser Reihenfolge

1. Recovery-Boden sichern (Original-IMG + Kenntnis MaskROM/USB Burning Tool).
2. `collect-readonly.sh` laufen lassen → `compatible`/`amlogic-dt-id`, RAM,
   WLAN/PHY, Partitionsnamen, Sicherheits-Posture, OTA-Oberfläche.
3. Weg A vs. Weg B entscheiden (hängt an Secure-Boot-Status; für Weg B UART).
4. Image + dtb bestimmen, Hash-Disziplin anwenden.
5. Erst dann Toothpick-SD-Boot testen.

## Quellen

- Amlogic USB Burning Tool — <https://wiki.coreelec.org/coreelec:aml_usb_tool>
- Restore your Amlogic device — <https://wiki.coreelec.org/coreelec:restore>
- Install community builds (S905/S905X/S912) — <https://forum.libreelec.tv/thread/5556-howto-faq-install-community-builds-on-s905-s905d-s905w-s905x-s912-device/>
- S905 boot from SD — <https://forum.armbian.com/topic/46088-s905-how-to-make-default-boot-from-sd/>
