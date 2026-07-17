#!/usr/bin/env python3
"""
bravia_advisor.py

Wertet die Audit-Daten von bravia_harden.py aus und macht daraus eine
priorisierte Risiko- und Härtungs-Analyse mit Hardening-Score (0-100).

Zwei Modi:
  * Offline-Experten-Engine (Standard) - regelbasiert, kein Datenabfluss,
    vollständig testbar. Das ist der Kern.
  * Optionaler KI-Modus (--ai) - schickt eine NICHT-identifizierende
    Befund-Zusammenfassung (ohne Hostnames/IPs) an Claude für eine
    Zweitmeinung. Nur mit gesetztem ANTHROPIC_API_KEY und installiertem
    anthropic-SDK.

Eingabe: JSON von `bravia_harden.py audit --json`, entweder
  - per Datei (--file report.json),
  - per stdin (Pipe), oder
  - live vom Gerät (--serial ip:5555, ruft bravia_harden intern auf).

Beispiele:
  bravia_harden.py audit --json | bravia_advisor.py
  bravia_advisor.py --file report.json
  bravia_advisor.py --serial 192.168.178.42:5555
  bravia_advisor.py --file report.json --ai        # + KI-Zweitmeinung
  bravia_advisor.py --file report.json --json       # maschinenlesbar
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict

# Modell für die optionale KI-Zweitmeinung. Opus 4.8 ist der sinnvolle Default
# fuer eine einmalige Analyse-Anfrage.
AI_MODEL = "claude-opus-4-8"

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
# Punktabzug vom Score (Start 100) je Befund-Schwere.
SEVERITY_PENALTY = {"critical": 40, "high": 25, "medium": 12, "low": 5, "info": 0}


# --------------------------------------------------------------------------- #
# Datenmodell
# --------------------------------------------------------------------------- #

@dataclass
class Finding:
    id: str
    severity: str          # critical | high | medium | low | info
    title: str
    detail: str
    recommendation: str

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Ausgabe-Helfer
# --------------------------------------------------------------------------- #

_TTY = sys.stderr.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def info(m: str) -> None:
    print(f"[*] {m}", file=sys.stderr)


def err(m: str) -> None:
    print(_c(f"[-] {m}", "31"), file=sys.stderr)


SEV_COLOR = {"critical": "31", "high": "31", "medium": "33", "low": "36", "info": "0"}


# --------------------------------------------------------------------------- #
# Offline-Experten-Engine
# --------------------------------------------------------------------------- #

def _flag_on(value) -> bool:
    """Android settings liefern '1'/'0' (oder int). True bei '1'/1."""
    return str(value).strip() in ("1", "true", "True")


def analyze(audit: dict) -> tuple[list[Finding], int]:
    """Regelbasierte Analyse. Gibt (Befunde, Hardening-Score) zurueck."""
    findings: list[Finding] = []
    device = audit.get("device", {}) or {}
    settings = audit.get("settings", {}) or {}
    packages = audit.get("packages", []) or []

    # 1. Security-Patch-Level
    patch = str(device.get("security_patch", "")).strip()
    if patch:
        year = patch[:4]
        if year and year.isdigit():
            y = int(year)
            if y < 2019:
                findings.append(Finding(
                    "patch-ancient", "critical",
                    f"Security-Patch-Level sehr alt ({patch})",
                    "Das Gerät hat seit Jahren keine Sicherheitsupdates. Bekannte "
                    "Lücken im System und in der WebView/Browser-Komponente bleiben offen.",
                    "TV netzwerkseitig strikt einzäunen: eigenes IoT-/Gäste-VLAN mit "
                    "AP-Isolation, per Firewall vom Internet trennen wenn Streaming-Apps "
                    "nicht mehr nötig sind (siehe `wlan`)."))
            elif y < 2022:
                findings.append(Finding(
                    "patch-old", "high",
                    f"Security-Patch-Level veraltet ({patch})",
                    "Mehrere Jahre ohne Sicherheitsupdates.",
                    "Netzexposition minimieren (IoT-/Gäste-VLAN), Telemetrie abschalten."))
    else:
        findings.append(Finding(
            "patch-unknown", "info",
            "Security-Patch-Level nicht auslesbar",
            "Kein ro.build.version.security_patch vorhanden.",
            "Ggf. kein Android-TV - dann greift die ADB-Härtung nicht."))

    # 2. Netzwerk-ADB / Debugging offen
    if _flag_on(settings.get("adb_wifi_enabled")):
        findings.append(Finding(
            "adb-wifi-on", "medium",
            "Netzwerk-ADB (adb_wifi) aktiv",
            "Port 5555 ist offen - jeder im selben Netz kann eine ADB-Verbindung "
            "versuchen. Für die Härtung nötig, danach aber ein Risiko.",
            "Nach Abschluss der Härtung `bravia_harden.py lockdown --apply` ausführen "
            "und am TV Netzwerk-Debugging deaktivieren."))
    if _flag_on(settings.get("adb_enabled")) and not _flag_on(settings.get("adb_wifi_enabled")):
        findings.append(Finding(
            "adb-on", "low",
            "ADB-Debugging aktiv",
            "ADB ist eingeschaltet (aktuell nicht übers Netz exponiert).",
            "Nach der Härtung ADB deaktivieren."))

    # 3. Entwickleroptionen
    if _flag_on(settings.get("development_settings_enabled")):
        findings.append(Finding(
            "devopts-on", "low",
            "Entwickleroptionen aktiv",
            "Entwicklermodus ist eingeschaltet.",
            "Nach der Härtung wieder deaktivieren, wenn nicht mehr benötigt."))

    # 4. Standort
    loc = str(settings.get("location_mode", "")).strip()
    if loc and loc not in ("0", "", "None"):
        findings.append(Finding(
            "location-on", "medium",
            "Standortdienste aktiv",
            "Der TV ermittelt/teilt Standortdaten.",
            "Einstellungen > Standort AUS (bzw. settings put secure location_mode 0)."))

    # 5. Bluetooth
    if _flag_on(settings.get("bluetooth_on")):
        findings.append(Finding(
            "bluetooth-on", "low",
            "Bluetooth aktiv",
            "Bluetooth-Funk erhöht die Angriffsfläche.",
            "Falls keine BT-Fernbedienung/Geräte genutzt werden: "
            "`bravia_harden.py services --disable-bluetooth --apply`. "
            "Achtung: BT-Fernbedienungen brauchen Bluetooth."))

    # 6. Telemetrie/Ads-Pakete
    tele = [p["package"] for p in packages
            if p.get("category") == "telemetry" and not p.get("disabled")]
    if tele:
        findings.append(Finding(
            "telemetry-active", "medium",
            f"{len(tele)} aktive Telemetrie/Tracking/Ads-Pakete",
            "Verdächtige Pakete: " + ", ".join(tele),
            "`bravia_harden.py privacy --disable-telemetry --apply` (reversibel)."))

    # 7. Bloatware / optionale Apps
    optional = [p["package"] for p in packages
                if p.get("category") == "optional" and not p.get("disabled")]
    recommended = [p["package"] for p in packages
                   if p.get("recommended_remove") and not p.get("disabled")]
    if recommended:
        findings.append(Finding(
            "bloatware-recommended", "info",
            f"{len(recommended)} entfernbare vorinstallierte Apps",
            "Kuratierte Empfehlung: " + ", ".join(recommended),
            "Ungenutzte entfernen: `bravia_harden.py apps --uninstall <pkgs> --apply` "
            "(reversibel)."))
    elif optional:
        findings.append(Finding(
            "bloatware-optional", "info",
            f"{len(optional)} optionale Apps installiert",
            "Kandidaten siehe `bravia_harden.py apps --list`.",
            "Ungenutzte Apps entfernen (reversibel)."))

    # Score berechnen
    score = 100
    for f in findings:
        score -= SEVERITY_PENALTY.get(f.severity, 0)
    score = max(0, min(100, score))

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.id))
    return findings, score


# --------------------------------------------------------------------------- #
# Optionale KI-Zweitmeinung
# --------------------------------------------------------------------------- #

def _redacted_summary(audit: dict, findings: list[Finding], score: int) -> str:
    """Baut eine NICHT-identifizierende Zusammenfassung (ohne Hostnames/IPs)."""
    device = audit.get("device", {}) or {}
    safe_device = {
        "model": device.get("model"),
        "android_release": device.get("android_release"),
        "sdk": device.get("sdk"),
        "security_patch": device.get("security_patch"),
    }
    lines = [
        f"Gerät: {json.dumps(safe_device, ensure_ascii=False)}",
        f"Offline-Hardening-Score: {score}/100",
        "Befunde der Offline-Engine:",
    ]
    for f in findings:
        lines.append(f"- [{f.severity}] {f.title}: {f.detail}")
    return "\n".join(lines)


def ai_second_opinion(summary: str) -> str:
    """Schickt die Zusammenfassung an Claude. Braucht anthropic-SDK + API-Key."""
    try:
        import anthropic
    except ImportError:
        return ("(KI-Modus übersprungen: anthropic-SDK nicht installiert. "
                "`pip install anthropic` und ANTHROPIC_API_KEY setzen.)")

    prompt = (
        "Du bist ein Security-Berater für Heimnetz-Geräte. Unten stehen die "
        "Befunde eines regelbasierten Audits eines älteren Sony-Bravia-Android-TV, "
        "der seit Jahren keine Updates bekommen hat. Gib eine knappe Zweitmeinung "
        "auf Deutsch: (1) die 3 wichtigsten Maßnahmen in Reihenfolge, "
        "(2) etwaige Risiken, die die Regeln übersehen haben könnten, "
        "(3) ob der Score plausibel wirkt. Keine erfundenen Gerätedetails.\n\n"
        + summary
    )
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=AI_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:  # noqa: BLE001 - dem Nutzer die Ursache zeigen
        return f"(KI-Modus fehlgeschlagen: {e})"


# --------------------------------------------------------------------------- #
# Eingabe laden
# --------------------------------------------------------------------------- #

def load_audit(args) -> dict:
    if args.serial or (not args.file and sys.stdin.isatty()):
        # Live vom Gerät via bravia_harden importieren.
        try:
            import bravia_harden as bh
        except ImportError:
            raise SystemExit("bravia_harden.py nicht im Pfad - mit --file oder Pipe nutzen.")
        serial = bh.resolve_serial(args.serial)
        bh.ensure_android_tv(serial)
        dinfo = bh.device_info(serial)
        pkgs = bh.classify_packages(serial)
        settings_keys = {
            "adb_enabled": "settings get global adb_enabled",
            "adb_wifi_enabled": "settings get global adb_wifi_enabled",
            "development_settings_enabled": "settings get global development_settings_enabled",
            "location_mode": "settings get secure location_mode",
            "bluetooth_on": "settings get global bluetooth_on",
        }
        settings = {k: bh.shell(serial, c, timeout=10).strip()
                    for k, c in settings_keys.items()}
        return {"device": dinfo, "settings": settings, "packages": pkgs}

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            return json.load(fh)
    return json.load(sys.stdin)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def score_label(score: int) -> str:
    if score >= 85:
        return "gut"
    if score >= 65:
        return "ok, Luft nach oben"
    if score >= 40:
        return "mangelhaft"
    return "kritisch"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="bravia_advisor",
        description="Priorisierte Risiko-/Härtungs-Analyse aus den Audit-Daten "
                    "(Offline-Engine + optionale KI-Zweitmeinung).")
    p.add_argument("--file", help="Audit-JSON aus `bravia_harden.py audit --json`")
    p.add_argument("--serial", help="Live vom Gerät lesen (ip:port)")
    p.add_argument("--ai", action="store_true",
                   help="KI-Zweitmeinung via Claude (braucht anthropic-SDK + API-Key; "
                        "sendet nur nicht-identifizierende Befunde)")
    p.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    args = p.parse_args(argv)

    try:
        audit = load_audit(args)
    except (OSError, json.JSONDecodeError) as e:
        err(f"Audit-Daten nicht lesbar: {e}")
        return 2

    findings, score = analyze(audit)
    summary = _redacted_summary(audit, findings, score)
    ai_text = ai_second_opinion(summary) if args.ai else None

    if args.json:
        print(json.dumps({
            "score": score,
            "findings": [f.as_dict() for f in findings],
            "ai_second_opinion": ai_text,
        }, indent=2, ensure_ascii=False))
        return 0

    print(_c("=== Bravia Härtungs-Analyse ===", "1"))
    print(f"  Hardening-Score: {_c(str(score) + '/100', SEV_COLOR['critical' if score < 40 else 'medium' if score < 85 else 'info'])}"
          f"  ({score_label(score)})")
    print()
    if not findings:
        print("  Keine Befunde - sauber.")
    else:
        print(_c(f"=== Befunde (priorisiert, {len(findings)}) ===", "1"))
        for f in findings:
            tag = _c(f.severity.upper(), SEV_COLOR.get(f.severity, "0"))
            print(f"  [{tag}] {f.title}")
            print(f"        {f.detail}")
            print(f"        → {f.recommendation}")
    if ai_text is not None:
        print()
        print(_c("=== KI-Zweitmeinung (Claude) ===", "1"))
        print(ai_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
