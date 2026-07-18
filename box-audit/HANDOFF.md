# Box-Workstream — Handoff / Übergabe

Zweck: Dieses Dokument verbindet alle Teile so, dass ein Mensch **oder** ein
Nachfolge-Agent den Box-Workstream **kalt** übernehmen und Ende-zu-Ende zu Ende
führen kann — bis Linux aus der eMMC bootet.

Branch: `claude/box-ota-audit-stability-an15ud`

---

## Zielbild

Die Amlogic-Box soll **Linux direkt aus der eMMC** booten. Die Android-
Notfallbasis bleibt erhalten, indem der **Dual-Boot-Weg (CoreELEC `ceemmc`)**
gewählt wird. Alles Irreversible ist durch eine Rücksetz-Versicherung (Phase 0)
und einen non-destruktiven SD-Beweis (Phase 2) abgesichert.

## Artefakte in diesem Verzeichnis

| Datei | Rolle | Läuft wo |
|---|---|---|
| `README.md` | Gate-Modell, vier Sicherheitsebenen, zwei Boot-Ketten, dtb-Map | — |
| `collect-readonly.sh` | **read-only** Board-/Sicherheits-/OTA-Evidenz | PC (adb) |
| `INSTALL-eMMC.md` | vollständiges A–Z-eMMC-Runbook (Phase 0–5) | — |
| `backup-emmc.sh` | Phase-0 eMMC-Voll-Dump auf externes Medium (nur lesend) | SD-Linux (root) |
| `verify-image.sh` | Phase-2 Hash-/PGP-Prüfung des Images | PC |
| `HANDOFF.md` | dieses Dokument | — |

## Status — erledigt vs. offen

**Erledigt (im Repo, ausführbar):**
- Entscheidungsgerüst (Gates, Weg A/B, Recovery-Boden).
- Read-only Collector, Backup-Helfer, Image-Verify-Helfer, eMMC-Runbook.
- Alle Skripte `sh -n`-geprüft und ausführbar.

**Offen — erzeugbar NUR am Gerät / durch den Operator** (nicht fabrizierbar):
1. Ausgabe von `collect-readonly.sh` → exaktes Board, `compatible`/`dt-id`, RAM,
   WLAN/PHY, Partitionsnamen, Sicherheits-Posture, OTA-Oberfläche.
2. Secure-Boot-Status (nur für Weg B) via UART-Bootlog oder Verhaltenstest.
3. Original-Android-Firmware-Image der exakten Box (Phase 0, Rückweg 1).
4. Bestätigter SD-Boot des gewählten Images (Phase 2).

## Der genaue nächste Schritt

```sh
# 1) Board + Posture erheben (read-only, keine Geräteänderung):
sh box-audit/collect-readonly.sh > box-evidence-$(date +%Y%m%d).txt
```
Dann: Ausgabe auswerten → Weg A (CoreELEC, empfohlen) oder B (Armbian) →
Image + dtb wählen → mit `verify-image.sh` prüfen → SD bauen und **von SD**
booten (Phase 2). Erst nach erfolgreichem SD-Boot: Phase 0 abschließen
(`backup-emmc.sh` + Original-IMG), dann eMMC-Install (Phase 3).

## Sequenz-Invarianten (nicht verletzen)

1. **Kein eMMC-Write ohne vollständige Phase 0** (Original-IMG **und** eMMC-Dump).
2. **Kein eMMC-Write ohne bestandene Phase 2** (identisches Image bootet von SD
   inkl. Netz/HDMI/WLAN).
3. **Weg B nur, wenn Secure Boot = aus.** Sonst Weg A.
4. Meldet die Box **„WorldCup Device"** → BootROM hat abgelehnt; vor dem Write
   unkritisch, nach dem Write → Rollback über Phase-0-Backups.

## Konsolidierte Reihenfolge

```
0  Rücksetz-Versicherung   (backup-emmc.sh + Original-IMG + USB Burning Tool)
1  collect-readonly.sh     → Weg A oder B, dtb/Image-Kandidaten
2  Image verify + SD-Boot  (verify-image.sh, Toothpick) — non-destruktiv
3  eMMC-Install            A) ceemmc -x → 1 (Dual)   B) armbian-install
4  Boot aus eMMC           (kein Toothpick; Dual: Android via CE-Menü)
5  Verifizieren + Rollback-Pfad testen
```

## Was bewusst NICHT getan wurde

- Keine Audit-/Test-/Report-Ergebnisse fabriziert (die OneDrive-Reports und
  Codex-Threads sind aus dieser Umgebung nicht erreichbar).
- Kein Zugriff auf das Gerät, kein Flash, kein Reboot ausgelöst.
- Kein Pull Request erstellt (nicht angefordert).
