# Linux fest in die eMMC — vollständiges Runbook (A bis Z)

Ziel: Die Box bootet Linux **direkt aus der eMMC** (ohne SD, ohne Toothpick im
Alltag). Dieser Schritt **überschreibt eMMC-Inhalt** und ist der irreversible
Teil des Projekts.

> **Bewusster Regelwechsel.** Bis hier galt „kein eMMC-Zugriff/kein Flash".
> Ab jetzt wird auf die eMMC geschrieben. Deshalb ist **Phase 0 Pflicht** und
> nicht überspringbar. Wer die Android-Notfallbasis behalten will, nimmt den
> **Dual-Boot-Weg (A)** — er legt Linux **neben** Android in die eMMC.

Platzhalter in `<spitzen Klammern>` werden aus den read-only Befunden
(`collect-readonly.sh`) gefüllt. Nichts davon rät das Runbook für dich.

---

## Phase 0 — Rücksetz-Versicherung (VOR jedem eMMC-Write, Pflicht)

Ohne diese Phase kein Schreibzugriff. Zwei unabhängige Rückwege:

1. **Amlogic USB Burning Tool + Original-Firmware.**
   - Original-Android-`.img` der **exakten** Box beschaffen, mit `sha256`
     festhalten. Ohne Original-Image ist ein secure-gefustes Board nur auf den
     Vendor-Stand, ein offenes Board gar nicht garantiert rücksetzbar.
   - Tool installieren (Windows), MaskROM-/„WorldCup Device"-Modus verstehen:
     kommt eMMC-Boot nicht zustande, flasht das Tool das Original zurück — auch
     bei zerstörtem Bootloader. Notfalls eMMC-Pins kurzschließen, um eMMC-Boot
     zu überspringen.
2. **Voll-Dump der eMMC (empfohlen, wenn möglich).**
   - Erst **nach** erfolgreichem SD-Boot (Phase 2) aus dem SD-Linux:
     `dd if=/dev/<emmc-device> of=/pfad/auf/externem/medium/emmc-full.img bs=8M`
     — sichert Android-Partitionen inkl. Bootloader byteweise. Prüfsumme bilden.

**Abbruch, wenn Phase 0 nicht vollständig ist.** Kein Write ohne Rückweg.

Entscheidung festhalten:
- **Dual-Boot** (Android + Linux in eMMC, ~5 GB Android-Layout gehen verloren) — erhält Notfallbasis. **Empfohlen.**
- **Single-Boot** (Linux allein, Android wird gelöscht) — nur mit verifiziertem Voll-Backup.

---

## Phase 1 — Identität und Kettenwahl

1. `sh box-audit/collect-readonly.sh > box-evidence.txt` laufen lassen.
2. Aus der Ausgabe festhalten:
   - `ro.board.platform` → SoC-Familie `<gxbb|gxl|gxm|g12a|sm1>`
   - `/proc/device-tree/compatible` → Referenz-Baseboard `<p212|q200|…>`
   - `amlogic-dt-id`, RAM-Menge, WLAN-/Ethernet-Chip, Partitionsnamen
3. **Boot-Kette wählen:**
   - **Weg A — CoreELEC/LibreELEC (signiert, tolerant, Dual-Boot möglich).**
     Erste Wahl, wenn das Ziel „Linux bootet zuverlässig aus eMMC und Android
     bleibt als Rückfall" ist. Secure Boot meist irrelevant.
   - **Weg B — Armbian/Mainline (echtes Desktop-/Server-Linux).**
     Nur wenn Secure Boot **aus** ist (UART/Verhaltenstest) und ein passendes
     `meson-*.dtb` existiert. Überschreibt Android → Voll-Backup zwingend.

`ro.board.platform` → Mainline-dtb: gxbb `meson-gxbb-*`, gxl
`meson-gxl-s905x-*`, gxm `meson-gxm-*`, g12a `meson-g12a-*`, sm1 `meson-sm1-*`.

---

## Phase 2 — SD-Boot beweisen (non-destruktiv) — HARTE VORBEDINGUNG

**Niemals** in die eMMC schreiben, bevor das **identische** Image sauber von SD
gebootet hat.

1. Image auf SD schreiben:
   - Weg A: CoreELEC/LibreELEC-„Box"-Image für `<SoC>` mit balenaEtcher/Rufus.
     Passendes Box-`dtb` im Image setzen (Datei `device_trees`/`dtb.img` je nach
     Projekt) gemäß Projekt-Anleitung.
   - Weg B: Armbian-Image für `<SoC>`; nach dem Schreiben in
     `/boot/extlinux/extlinux.conf` die **FDT-Zeile** auf das korrekte dtb aus
     `/boot/dtb/amlogic/` setzen.
2. **Hash-Disziplin:** Image über TLS von der Projektquelle, `sha256sum` gegen
   veröffentlichte Prüfsumme, Prüfsummendatei per PGP gegen Projekt-Fingerprint.
3. **Toothpick-Boot:** Strom weg, SD rein, Reset gedrückt halten, Strom anlegen,
   ~7 s halten, loslassen. Timing variiert — experimentieren.
4. **Verifizieren, bevor es weitergeht:** bootet vollständig, Bild/HDMI, Netz,
   WLAN, Ethernet, ggf. IR-Fernbedienung. Erst wenn **alles Wesentliche**
   funktioniert, ist dieses Image ein eMMC-Kandidat. Sonst: dtb/Image korrigieren
   und Phase 2 wiederholen. **Android in der eMMC ist bis hier unangetastet.**

---

## Phase 3 — Installation in die eMMC

### Weg A — CoreELEC `ceemmc` (Dual- oder Single-Boot)

Voraussetzung: das CE-Image bootet sauber von SD (Phase 2). SSH aktiviert.

1. Per SSH auf die von SD gebootete CoreELEC einloggen.
2. Tool starten: `ceemmc -x`
3. `Y` bestätigen, dann Modus wählen:
   - `1` = **Dual-Boot** → CE landet in eMMC, **Android bleibt bootbar**
     (Wechsel via CE-Menü „Reboot from eMMC/NAND"). ~5 GB gehen ans Android-
     Layout verloren. **Empfohlen.**
   - Single-Boot → CE allein, Android wird ersetzt. Nur mit Voll-Backup.
4. Nach Abschluss externes Medium (SD) **entfernen**.
5. Neustart → CE bootet nun **aus der eMMC**, ohne SD/Toothpick.

> Warnung aus der offiziellen Doku: das Tool **kann die Box bricken**. Doku
> vollständig lesen. Migration braucht je nach Gerät rooted Android 7+;
> Android 14 wird nicht unterstützt.

### Weg B — Armbian in die eMMC

Voraussetzung: Armbian bootet sauber von SD (Phase 2), FDT/dtb korrekt gesetzt.

1. Von SD gebootetes Armbian, als root einloggen.
2. Installer starten: `armbian-install` (bzw. das mitgelieferte
   `install-aml.sh`). Wenn `install-aml.sh` bereits erfolgreich kopiert hat, ist
   ein manuelles `armbian-install` nicht mehr nötig.
3. Ziel „eMMC/internal" wählen. **Das überschreibt Android** → Voll-Backup aus
   Phase 0 muss vorliegen.
4. Nach Abschluss herunterfahren, SD entfernen, neu starten → Armbian bootet aus
   eMMC.

> Realität: S905 (gxbb) hat schlechte eMMC-Install-Unterstützung; S905X (gxl)
> deutlich besser. Passt das nicht, bei Weg A / SD-Betrieb bleiben.

---

## Phase 4 — Boot-Reihenfolge nach dem Install

- Nach dem eMMC-Install liegt der **Bootloader in der eMMC** → die Box bootet
  Linux **direkt**, ohne Toothpick, ohne SD.
- **Dual-Boot (Weg A):** zurück zu Android über das CE-Shutdown-Menü
  „Reboot from eMMC/NAND". Damit bleibt die Android-Notfallbasis erreichbar.
- Toothpick wird künftig nur noch gebraucht, um wieder von **SD** zu starten
  (z. B. für Recovery oder ein neues Image).

---

## Phase 5 — Verifikation & Rollback

**Verifizieren:**
1. SD entfernt → Box bootet Linux allein aus eMMC. ✔
2. (Dual) Wechsel Linux ↔ Android funktioniert. ✔
3. Netz/WLAN/HDMI/Fernbedienung wie im SD-Test. ✔
4. Sicherheits-Posture erneut prüfen (Netz-ADB aus, OTA neutralisiert — bei
   Linux entfällt die Android-OTA ohnehin).

**Rollback (falls nötig):**
- Original-Android per **USB Burning Tool** zurückflashen (Phase 0, Weg 1),
  oder
- den **eMMC-Voll-Dump** zurückschreiben (Phase 0, Weg 2).

---

## Warn- und Abbruchsignale

- Box meldet sich als **„WorldCup Device"** statt zu booten → BootROM hat
  eMMC/SD verworfen. Wenn **vor** dem Write: nichts passiert, Original per Tool
  rückspielbar. Wenn **nach** dem Write: Rollback fahren.
- `ceemmc`/`armbian-install` bricht mit Partitions-/Signaturfehler ab → **nicht**
  erneut blind starten; Ursache klären (falsches dtb, Secure Boot, Android-
  Version), sonst Brick-Risiko.
- Kein vollständiges Phase-0-Backup → **Write nicht beginnen.**

---

## Kurzfassung der Reihenfolge

```
0  Rücksetz-Versicherung (USB Burning Tool + Original-IMG, optional eMMC-Dump)
1  Identität (collect-readonly) → Weg A oder B
2  SD-Boot BEWEISEN (identisches Image, Hash-geprüft, non-destruktiv)
3  eMMC-Install:  A) ceemmc -x → 1 (Dual, Android bleibt)   B) armbian-install
4  Boot aus eMMC (kein Toothpick mehr; Dual: Android via CE-Menü)
5  Verifizieren; Rollback nur über Phase-0-Backups
```

## Quellen

- CoreELEC eMMC / ceemmc — <https://wiki.coreelec.org/coreelec:eemc>
- ceemmc Dual-Boot-Installation — <https://wiki.coreelec.org/coreelec:ceemc_ins>
- ceemmc Tool-Anleitung — <https://discourse.coreelec.org/t/how-to-use-the-team-coreelec-ceemmc-tool/7630>
- Armbian → eMMC (Tutorial) — <https://forum.armbian.com/topic/32520-install-armbian-on-the-internal-emmc-storage-tutorial/>
- Armbian → eMMC (How-to) — <https://forum.armbian.com/topic/19561-how-to-install-armbian-to-emmc/>
- Amlogic USB Burning Tool / Restore — <https://wiki.coreelec.org/coreelec:restore>
