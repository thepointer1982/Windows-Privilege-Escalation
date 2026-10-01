#!/usr/bin/env python3
"""
bravia_harden.py

Optimierungs- und Härtungs-Tool für ältere Sony-Bravia-Android-TV-Geräte,
angesprochen über ADB (siehe firetv_bravia_connect.py für den Verbindungsaufbau).

Gedacht für den Fall "der TV hat seit Jahren keine Updates bekommen": Schicht
für Schicht entschlacken und die Angriffsfläche reduzieren.

Leitprinzipien (bewusst konservativ, damit ein nicht mehr flashbarer TV nicht
soft-bricked):
  * DRY-RUN ist Standard. Es wird nur gezeigt, WAS getan würde. Echte
    Änderungen nur mit --apply.
  * Alles ist reversibel: `pm disable-user` / `uninstall --user 0` lässt sich
    per `enable` / `install-existing` zurückholen. Kein Root, kein Flashen.
  * Harte Sperrliste: kritische System-Pakete werden nie angefasst
    (nur mit --force, mit deutlicher Warnung).
  * Klassifizieren statt raten: die real installierten Pakete werden ausgelesen
    und eingeordnet; entscheiden tust du.

Was dieses Tool NICHT tut (weil über ADB nicht sinnvoll/sicher machbar):
  * Service-Menü öffnen (nur per Fernbedienungs-Code; kann Panel/Weißabgleich
    zerstören) -> wird nur als Warnhinweis erwähnt.
  * "Treiber ziehen" -> auf SoC-TVs gibt es keine austauschbaren Treiber.
  * WLAN am Router härten -> das ist Router-Sache; `wlan` gibt eine Checkliste.

Subcommands:
  audit      Read-only Inventur: Geräteinfo, klassifizierte Pakete, Settings.
  apps       Apps auflisten / deaktivieren / entfernen / zurückholen.
  privacy    Telemetrie-/Ads-Pakete markieren+deaktivieren, Datenschutz-Checkliste.
  services   Bluetooth/Cast & Co. prüfen und optional abschalten.
  wlan       Router-seitige WLAN-Härtungs-Checkliste (keine Geräteänderung).
  lockdown   Netzwerk-ADB am Ende wieder abschalten (Angriffsfläche zu).

Beispiele:
  python3 bravia_harden.py audit
  python3 bravia_harden.py apps --list
  python3 bravia_harden.py apps --uninstall com.netflix.ninja            # dry-run
  python3 bravia_harden.py apps --uninstall com.netflix.ninja --apply
  python3 bravia_harden.py privacy --disable-telemetry --apply
  python3 bravia_harden.py apps --restore com.netflix.ninja --apply
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys

# --------------------------------------------------------------------------- #
# Sperrliste: diese Pakete werden ohne --force NIE deaktiviert/entfernt.
# Konservativ gehalten -> lieber etwas zu viel schützen als den TV zerlegen.
# --------------------------------------------------------------------------- #

CRITICAL_EXACT = {
    "android",
    "com.android.systemui",
    "com.android.settings",
    "com.android.settings.intelligence",
    "com.android.tv.settings",
    "com.android.provision",
    "com.android.keychain",
    "com.android.packageinstaller",
    "com.google.android.packageinstaller",
    "com.android.vending",              # Play Store
    "com.google.android.gsf",           # Google Services Framework
    "com.google.android.gms",           # Play Services
    "com.google.android.webview",
    "com.android.webview",
    "com.google.android.tvlauncher",    # Launcher (Android TV)
    "com.google.android.leanbacklauncher",
    "com.sony.dtv.tvx",                 # Sony TV-Eingang / Live-TV
}

CRITICAL_PREFIX = (
    "com.android.internal",
    "com.android.providers.",           # settings/media/telephony provider
    "com.android.server.",
    "com.android.inputmethod",
    "com.android.bluetooth",
    "com.android.nfc",
    "com.android.certinstaller",
    "com.android.se",
    "com.google.android.gms",
    "com.google.android.gsf",
    "com.google.android.tv.frameworkpackagestubs",
    "com.mediatek",                     # SoC-Komponenten
    "com.sony.dtv.",                    # Sony-TV-Framework (breit geschützt)
    "com.sony.tvinput",
    "com.sony.dtv.hardware",
)

# Schlagworte, die ein Paket als Telemetrie/Tracking/Ads verdächtig machen.
TELEMETRY_KEYWORDS = (
    "analytics", "crashlytics", "telemetry", "metrics", "tracking",
    "adservice", "adservices", "advertising", ".ads.", "adsdk",
    "sambatv", "samba.", "acr", "usagent", "datacollect",
)

# Kuratierte Liste: verbreitete, gefahrlos für den Nutzer entfernbare
# (`pm uninstall --user 0`, reversibel) Android-TV-/Bravia-Extras. Es werden nur
# solche vorgeschlagen, die auch WIRKLICH installiert sind. Nutzt du eine davon,
# einfach nicht entfernen bzw. per --restore zurückholen.
RECOMMENDED_REMOVE = {
    # Google-Zusatz-Apps (kein System-Kern)
    "com.google.android.youtube.tvkids",   # YouTube Kids
    "com.google.android.videos",           # Google TV / Play Filme
    "com.google.android.music",            # Play Music (tot)
    "com.google.android.play.games",       # Play Games
    "com.google.android.apps.mediashell",  # Cast-Receiver-Extras
    # Vorinstallierte Streaming-Apps (Beispiele – nur wenn ungenutzt)
    "com.netflix.ninja",
    "com.amazon.avod",
    "com.amazon.amazonvideo.livingroom",
    "com.spotify.tv.android",
    "com.tubitv",
    "com.disney.disneyplus",
    "com.wbd.stream",
    "com.tcl.tv",
    "com.rakuten.tv.android",
    "com.dailymotion.dailymotion",
    # Sony-Promo/Demo (falls nicht per Sperrliste geschützt)
    "com.sony.dtv.smarthome",
}

# --------------------------------------------------------------------------- #
# Ausgabe-Helfer (Fortschritt nach stderr, Daten nach stdout)
# --------------------------------------------------------------------------- #

_TTY = sys.stderr.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def info(m: str) -> None:
    print(f"[*] {m}", file=sys.stderr)


def ok(m: str) -> None:
    print(_c(f"[+] {m}", "32"), file=sys.stderr)


def warn(m: str) -> None:
    print(_c(f"[!] {m}", "33"), file=sys.stderr)


def err(m: str) -> None:
    print(_c(f"[-] {m}", "31"), file=sys.stderr)


# --------------------------------------------------------------------------- #
# ADB-Anbindung
# --------------------------------------------------------------------------- #

class AdbError(RuntimeError):
    pass


def adb_available() -> bool:
    return shutil.which("adb") is not None


def _run_adb(serial: str | None, args: list[str], timeout: float = 20.0) -> str:
    if not adb_available():
        raise AdbError(
            "adb nicht gefunden. Android Platform Tools installieren "
            "(apt install adb / brew install android-platform-tools) und zuerst "
            "mit firetv_bravia_connect.py connect <ip> verbinden."
        )
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    try:
        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        raise AdbError(f"adb-Aufruf fehlgeschlagen: {e}") from e
    return res.stdout


def resolve_serial(explicit: str | None) -> str:
    """Liefert das anzusteuernde Gerät. Bei genau einem verbundenen Gerät
    automatisch, sonst muss --serial angegeben werden."""
    if explicit:
        return explicit
    out = _run_adb(None, ["devices"], timeout=10)
    devices = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "offline" in line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    if not devices:
        raise AdbError(
            "Kein autorisiertes ADB-Gerät verbunden. Zuerst "
            "`firetv_bravia_connect.py connect <ip>` ausführen und am TV "
            "das Debugging-Popup bestätigen."
        )
    if len(devices) > 1:
        raise AdbError(
            "Mehrere Geräte verbunden: " + ", ".join(devices) +
            " -> mit --serial <ip:port> auswählen."
        )
    return devices[0]


def shell(serial: str, cmd: str, timeout: float = 20.0) -> str:
    return _run_adb(serial, ["shell", cmd], timeout=timeout)


# --------------------------------------------------------------------------- #
# Geräte- und Paket-Inventur
# --------------------------------------------------------------------------- #

def ensure_android_tv(serial: str) -> None:
    """Bricht sauber ab, wenn das Gerät kein Android (TV) ist. Ältere Bravia
    (vor ~2015) laufen auf Linux/Opera und haben kein `pm`/`getprop`."""
    sdk = shell(serial, "getprop ro.build.version.sdk", timeout=10).strip()
    if not sdk.isdigit():
        raise AdbError(
            "Gerät antwortet nicht wie ein Android-TV (kein SDK-Level). "
            "Sehr wahrscheinlich ein Pre-2015-Bravia auf Linux/Opera – dieses "
            "Tool (ADB/pm) greift dort nicht. Härtung dann nur router-seitig "
            "möglich (siehe `wlan`)."
        )


def device_info(serial: str) -> dict:
    props = {
        "manufacturer": "ro.product.manufacturer",
        "model": "ro.product.model",
        "name": "ro.product.name",
        "android_release": "ro.build.version.release",
        "sdk": "ro.build.version.sdk",
        "build_date": "ro.build.date",
        "security_patch": "ro.build.version.security_patch",
        "fingerprint": "ro.build.fingerprint",
    }
    info_d: dict[str, str] = {}
    for key, prop in props.items():
        val = shell(serial, f"getprop {prop}", timeout=10).strip()
        info_d[key] = val
    return info_d


def is_critical(pkg: str) -> bool:
    if pkg in CRITICAL_EXACT:
        return True
    return any(pkg.startswith(p) for p in CRITICAL_PREFIX)


def looks_like_telemetry(pkg: str) -> bool:
    low = pkg.lower()
    return any(k in low for k in TELEMETRY_KEYWORDS)


def _pkg_list(serial: str, flag: str) -> set[str]:
    out = shell(serial, f"pm list packages {flag}".strip(), timeout=25)
    return {
        line.split("package:", 1)[1].strip()
        for line in out.splitlines()
        if line.startswith("package:")
    }


def classify_packages(serial: str) -> list[dict]:
    """Liest alle Pakete aus und ordnet sie ein."""
    all_pkgs = _pkg_list(serial, "")
    third_party = _pkg_list(serial, "-3")
    disabled = _pkg_list(serial, "-d")

    result = []
    for pkg in sorted(all_pkgs):
        if is_critical(pkg):
            category = "critical"
        elif looks_like_telemetry(pkg):
            category = "telemetry"
        elif pkg in third_party:
            category = "optional"        # vorinstallierte / installierte Apps
        else:
            category = "keep"            # System, nicht kritisch, unauffällig
        result.append({
            "package": pkg,
            "category": category,
            "third_party": pkg in third_party,
            "disabled": pkg in disabled,
            "recommended_remove": pkg in RECOMMENDED_REMOVE and not is_critical(pkg),
        })
    return result


def recommended_installed(pkgs: list[dict]) -> list[str]:
    """Installierte Pakete aus der kuratierten Entfernen-Empfehlung."""
    return [p["package"] for p in pkgs
            if p["recommended_remove"] and not p["disabled"]]


# --------------------------------------------------------------------------- #
# Aktionen auf Paketen (reversibel)
# --------------------------------------------------------------------------- #

ACTIONS = {
    # name -> (adb-shell-Kommando-Template, menschlicher Text)
    "disable": ("pm disable-user --user 0 {pkg}", "deaktivieren (reversibel)"),
    "enable":  ("pm enable {pkg}", "wieder aktivieren"),
    "uninstall": ("pm uninstall --user 0 {pkg}",
                  "für Nutzer entfernen (per install-existing zurückholbar)"),
    "restore": ("cmd package install-existing {pkg}", "wieder installieren"),
}


def _command_succeeded(out: str) -> bool:
    """Bewertet die Ausgabe eines pm/cmd/svc-Kommandos.

    Erkennt Fehler an deren Markern statt einer Erfolgs-Whitelist, weil die
    Erfolgs-Ausgaben je nach Kommando unterschiedlich sind:
      pm uninstall --user 0  -> "Success"
      pm disable-user         -> "Package X new state: disabled-user"
      pm enable               -> "Package X new state: enabled"
      install-existing        -> "Package X installed for user: 0"
      (manche Kommandos)      -> leere Ausgabe
    Fehler sehen z.B. so aus: "Failure [NOT_INSTALLED...]", "Error: ...",
    "java.lang...Exception", "Unknown package", "not installed for user 0".
    """
    low = out.strip().lower()
    if not low:
        return True
    if low.startswith(("failure", "error")):
        return False
    if any(m in low for m in ("exception", "unknown package", "not installed for")):
        return False
    return True


def apply_action(serial: str, action: str, packages: list[str],
                 apply: bool, force: bool) -> int:
    template, human = ACTIONS[action]
    protective = action in ("disable", "uninstall")
    header = "AUSFÜHREN" if apply else "DRY-RUN (nichts wird geändert)"
    info(f"Aktion: {action} -> {human}  [{header}]")
    rc = 0
    for pkg in packages:
        if protective and is_critical(pkg) and not force:
            warn(f"  ÜBERSPRUNGEN (Sperrliste): {pkg} — mit --force erzwingbar, "
                 "kann den TV lahmlegen.")
            rc = 1
            continue
        cmd = template.format(pkg=pkg)
        if not apply:
            print(f"  würde ausführen: adb shell {cmd}")
            continue
        out = shell(serial, cmd, timeout=30).strip()
        success = _command_succeeded(out)
        if success:
            ok(f"  {pkg}: {out or 'ok'}")
        else:
            err(f"  {pkg}: {out or 'fehlgeschlagen'}")
            rc = 1
    if not apply:
        warn("Nur Vorschau. Zum tatsächlichen Ausführen --apply anhängen.")
    return rc


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_audit(args) -> int:
    serial = resolve_serial(args.serial)
    info(f"Ziel: {serial}")
    ensure_android_tv(serial)
    dinfo = device_info(serial)
    pkgs = classify_packages(serial)

    # Netzwerk-/Entwickler-relevante Settings (read-only)
    settings_keys = {
        "adb_enabled": "settings get global adb_enabled",
        "adb_wifi_enabled": "settings get global adb_wifi_enabled",
        "development_settings_enabled": "settings get global development_settings_enabled",
        "location_mode": "settings get secure location_mode",
        "bluetooth_on": "settings get global bluetooth_on",
    }
    settings = {k: shell(serial, c, timeout=10).strip() for k, c in settings_keys.items()}

    counts: dict[str, int] = {}
    for p in pkgs:
        counts[p["category"]] = counts.get(p["category"], 0) + 1

    report = {"device": dinfo, "settings": settings,
              "package_counts": counts, "packages": pkgs}

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(_c("=== Gerät ===", "1"))
    for k, v in dinfo.items():
        print(f"  {k:20s}: {v}")
    # Warnung bei altem Security-Patch
    patch = dinfo.get("security_patch", "")
    if patch and patch < "2020":
        warn(f"Security-Patch-Level ist {patch} — sehr alt. Netzexposition "
             "minimieren (IoT-/Gäste-VLAN, siehe `wlan`).")
    print()
    print(_c("=== Sicherheits-/Netzwerk-Settings ===", "1"))
    for k, v in settings.items():
        print(f"  {k:30s}: {v}")
    print()
    print(_c("=== Paket-Übersicht ===", "1"))
    for cat in ("critical", "keep", "optional", "telemetry"):
        print(f"  {cat:10s}: {counts.get(cat, 0)}")
    tele = [p["package"] for p in pkgs if p["category"] == "telemetry"]
    if tele:
        print()
        warn("Verdächtige Telemetrie/Tracking/Ads-Pakete:")
        for t in tele:
            print(f"    - {t}")
    print()
    info("Nächste Schritte: `apps --list` (Kandidaten), "
         "`privacy --disable-telemetry`, `services`, `wlan`, zum Schluss `lockdown`.")
    return 0


def _ai_classify(serial: str) -> int:
    """Lässt die KI die mehrdeutigen 'keep'-Systempakete einordnen (beratend)."""
    try:
        import bravia_ai
    except ImportError:
        err("bravia_ai.py nicht im Pfad.")
        return 3

    pkgs = classify_packages(serial)
    # Nur die mehrdeutigen: System, nicht kritisch, unauffällig.
    unknown = [p["package"] for p in pkgs if p["category"] == "keep"]
    if not unknown:
        ok("Keine mehrdeutigen 'keep'-Pakete zum Klassifizieren.")
        return 0
    info(f"Sende {len(unknown)} unbekannte Paketnamen an Claude "
         f"({bravia_ai.AI_MODEL}) — nur Paketnamen, keine identifizierenden Daten.")
    try:
        results = bravia_ai.classify_packages(unknown)
    except bravia_ai.AiUnavailable as e:
        err(str(e))
        return 3
    except RuntimeError as e:
        err(str(e))
        return 1
    if not results:
        warn("KI lieferte keine verwertbare Klassifizierung.")
        return 1

    by_cat: dict[str, list[dict]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)
    for cat in ("telemetry", "optional", "keep", "critical"):
        items = by_cat.get(cat, [])
        if not items:
            continue
        print(_c(f"\n[{cat.upper()}] ({len(items)})", "1"))
        for r in items:
            print(f"  {r['package']}")
            if r.get("reason"):
                print(f"      → {r['reason']}")
    print()
    warn("Nur KI-Einschätzung, beratend. Nichts wurde geändert. Vor dem Entfernen "
         "prüfen; System-/Framework-Pakete im Zweifel behalten.")
    return 0


def cmd_apps(args) -> int:
    serial = resolve_serial(args.serial)
    ensure_android_tv(serial)

    if args.ai_classify:
        return _ai_classify(serial)

    if args.recommend:
        pkgs = classify_packages(serial)
        rec = recommended_installed(pkgs)
        if args.json:
            print(json.dumps(rec, indent=2, ensure_ascii=False))
            return 0
        if not rec:
            ok("Keine der kuratierten Bloatware-Apps installiert – sauber.")
            return 0
        info(f"Kuratierte Entfernen-Empfehlung ({len(rec)} installiert). "
             "Nur was du NICHT nutzt entfernen:")
        for pkg in rec:
            print(f"  - {pkg}")
        print()
        info("Vorschlag (dry-run):  apps --uninstall "
             + ",".join(rec))
        info("Ausführen: gleiches Kommando mit --apply anhängen.")
        return 0

    if args.list:
        pkgs = classify_packages(serial)
        if args.json:
            print(json.dumps(pkgs, indent=2, ensure_ascii=False))
            return 0
        shown = [p for p in pkgs if p["category"] in ("optional", "telemetry")]
        info(f"Deaktivierbare/entfernbare Kandidaten (optional + telemetry): "
             f"{len(shown)}")
        for p in shown:
            state = "deaktiviert" if p["disabled"] else "aktiv"
            mark = "TELEMETRIE" if p["category"] == "telemetry" else "optional "
            rec = " *empfohlen entfernbar*" if p["recommended_remove"] else ""
            print(f"  [{mark}] {p['package']:45s} ({state}){rec}")
        print()
        info("Empfehlung:              apps --recommend")
        info("Entfernen (reversibel):  apps --uninstall <pkg1,pkg2> --apply")
        info("Deaktivieren:            apps --disable   <pkg1,pkg2> --apply")
        info("Zurückholen:             apps --restore   <pkg> --apply  (oder --enable)")
        return 0

    for action in ("disable", "enable", "uninstall", "restore"):
        val = getattr(args, action)
        if val:
            packages = [p.strip() for p in val.split(",") if p.strip()]
            return apply_action(serial, action, packages, args.apply, args.force)

    err("Nichts zu tun. --list oder eine Aktion (--disable/--enable/"
        "--uninstall/--restore) angeben.")
    return 2


def cmd_privacy(args) -> int:
    serial = resolve_serial(args.serial)
    ensure_android_tv(serial)
    pkgs = classify_packages(serial)
    tele = [p for p in pkgs if p["category"] == "telemetry"]

    info(f"{len(tele)} verdächtige Telemetrie/Ads-Pakete gefunden.")
    for p in tele:
        state = "deaktiviert" if p["disabled"] else "aktiv"
        print(f"  - {p['package']:45s} ({state})", file=sys.stderr)

    rc = 0
    if args.disable_telemetry and tele:
        rc = apply_action(serial, "disable",
                          [p["package"] for p in tele if not p["disabled"]],
                          args.apply, args.force)

    print()
    print(_c("=== Datenschutz-Checkliste (manuell am TV) ===", "1"))
    for line in (
        "Einstellungen > Datenschutz: Nutzungs-/Diagnosedaten AUS.",
        "Einstellungen > Google > Anzeigen: personalisierte Werbung AUS, Werbe-ID zurücksetzen.",
        "Sony: 'Samba TV' / 'Interaktives TV' / ACR (Automatic Content Recognition) AUS.",
        "Assistant/Mikrofon deaktivieren, wenn nicht genutzt.",
        "Standort AUS (settings/secure location_mode -> 0).",
        "Nicht genutzte Konten aus dem TV entfernen.",
    ):
        print(f"  [ ] {line}")
    if not args.apply and args.disable_telemetry:
        warn("Nur Vorschau. Zum Deaktivieren --apply anhängen.")
    return rc


def cmd_services(args) -> int:
    serial = resolve_serial(args.serial)
    ensure_android_tv(serial)
    # svc ist reversibel und ändert keine Paketzustände.
    checks = {
        "Bluetooth (settings global bluetooth_on)":
            "settings get global bluetooth_on",
        "WLAN (settings global wifi_on)":
            "settings get global wifi_on",
    }
    print(_c("=== Dienste-Status ===", "1"))
    for label, cmd in checks.items():
        print(f"  {label}: {shell(serial, cmd, timeout=10).strip()}")

    actions = []
    if args.disable_bluetooth:
        actions.append(("Bluetooth aus", "svc bluetooth disable"))
    if args.enable_bluetooth:
        actions.append(("Bluetooth an", "svc bluetooth enable"))

    if not actions:
        print()
        info("Optionen: --disable-bluetooth / --enable-bluetooth (jeweils --apply). "
             "Bluetooth aus reduziert die Funk-Angriffsfläche, wenn keine BT-Geräte "
             "(Fernbedienung per BT!) genutzt werden — vorher prüfen.")
        return 0

    for label, cmd in actions:
        if not args.apply:
            print(f"  würde ausführen: adb shell {cmd}   # {label}", file=sys.stderr)
        else:
            shell(serial, cmd, timeout=15)
            ok(f"  {label}")
    if not args.apply:
        warn("Nur Vorschau. Zum Ausführen --apply anhängen.")
    return 0


def cmd_run(args) -> int:
    """Orchestrierter Schicht-für-Schicht-Durchlauf. Dry-run als Standard.

    Sichere Layer (Telemetrie aus, optional Bluetooth) werden mit --apply
    tatsächlich ausgeführt. App-Entfernung bleibt bewusst eine bewusste
    Entscheidung -> es werden nur Empfehlungen und fertige Kommandos angezeigt.
    Der finale Lockdown (kappt die ADB-Verbindung) läuft nur mit
    --include-lockdown.
    """
    serial = resolve_serial(args.serial)
    ensure_android_tv(serial)
    mode = "AUSFÜHREN (--apply)" if args.apply else "DRY-RUN"
    info(f"Orchestrierter Durchlauf auf {serial}  [{mode}]")

    dinfo = device_info(serial)
    pkgs = classify_packages(serial)

    print(_c("\n### Schicht 1 – Gerät & Zustand", "1"))
    for k in ("manufacturer", "model", "android_release", "sdk", "security_patch"):
        print(f"  {k:18s}: {dinfo.get(k, '')}")
    patch = dinfo.get("security_patch", "")
    if patch and patch < "2020":
        warn(f"Security-Patch {patch} sehr alt – Netzexposition minimieren.")

    print(_c("\n### Schicht 2 – Alte Apps (Entscheidung bei dir)", "1"))
    rec = recommended_installed(pkgs)
    optional = [p["package"] for p in pkgs
                if p["category"] == "optional" and not p["disabled"]]
    if rec:
        info(f"Empfohlen entfernbar ({len(rec)}): " + ", ".join(rec))
        print("  Kommando: apps --uninstall " + ",".join(rec) + " --apply")
    if optional:
        info(f"Weitere optionale Apps: {len(optional)} (siehe `apps --list`)")
    if not rec and not optional:
        ok("Keine offensichtliche Bloatware installiert.")

    print(_c("\n### Schicht 3 – Telemetrie/Tracking", "1"))
    tele = [p["package"] for p in pkgs
            if p["category"] == "telemetry" and not p["disabled"]]
    if tele:
        apply_action(serial, "disable", tele, args.apply, force=False)
    else:
        ok("Keine verdächtigen Telemetrie-Pakete aktiv.")

    print(_c("\n### Schicht 4 – Funkdienste", "1"))
    if args.disable_bluetooth:
        if args.apply:
            shell(serial, "svc bluetooth disable", timeout=15)
            ok("Bluetooth deaktiviert.")
        else:
            print("  würde ausführen: adb shell svc bluetooth disable")
    else:
        info("Bluetooth unangetastet (--disable-bluetooth zum Abschalten). "
             "Achtung: BT-Fernbedienungen brauchen Bluetooth.")

    print(_c("\n### Schicht 5 – WLAN/Netzwerk (Router)", "1"))
    cmd_wlan(args)

    print(_c("\n### Schicht 6 – Lockdown (ADB zu)", "1"))
    if args.include_lockdown:
        cmd_lockdown(argparse.Namespace(serial=serial, apply=args.apply))
    else:
        info("Übersprungen. Wenn fertig: `lockdown --apply` "
             "(kappt danach diese ADB-Verbindung).")

    print()
    if not args.apply:
        warn("Kompletter DRY-RUN. Mit --apply werden die sicheren Layer "
             "(Telemetrie, optional Bluetooth) real ausgeführt. App-Entfernung "
             "läuft absichtlich nur über die angezeigten `apps --uninstall`-Kommandos.")
    return 0


def cmd_wlan(args) -> int:
    print(_c("=== WLAN / Netzwerk härten (Router-Seite) ===", "1"))
    print("Ein 10 Jahre nicht aktualisierter TV gehört netzwerkseitig eingezäunt.\n")
    items = [
        "WPA3 (oder mind. WPA2-AES/CCMP) erzwingen, WPA/TKIP und WEP abschalten.",
        "WPS komplett deaktivieren (PIN-Angriff).",
        "Eigenes IoT-/Gäste-WLAN für den TV, mit AP-/Client-Isolation, getrennt vom Hauptnetz.",
        "TV per Firewall-Regel vom Internet trennen, wenn Streaming-Apps nicht mehr genutzt werden (nur LAN).",
        "UPnP am Router deaktivieren; keine Portfreigaben zum TV.",
        "DNS über den Router filtern (z.B. Telemetrie-/Ads-Domains sperren).",
        "Feste IP/Reservierung für den TV, damit Firewall-Regeln greifen.",
        "FRITZ!Box: 'Gerät darf nicht ins Internet' im Kindersicherungs-/Zugangsprofil, "
        "wenn der TV offline betrieben werden soll.",
        "Router-Firmware aktuell halten — das ist die Schutzschicht vor dem alten TV.",
    ]
    for it in items:
        print(f"  [ ] {it}")
    print()
    info("Diese Punkte macht man am Router, nicht am TV — das Tool ändert hier nichts.")
    return 0


def cmd_lockdown(args) -> int:
    serial = resolve_serial(args.serial)
    warn("Lockdown schaltet Netzwerk-ADB ab — DANACH ist genau diese Verbindung "
         "weg und ein erneutes `connect` erst nach Re-Aktivierung am TV möglich.")
    cmds = [
        ("Netzwerk-ADB aus", "settings put global adb_wifi_enabled 0"),
        ("ADB gesamt aus",   "settings put global adb_enabled 0"),
    ]
    for label, cmd in cmds:
        if not args.apply:
            print(f"  würde ausführen: adb shell {cmd}   # {label}", file=sys.stderr)
        else:
            shell(serial, cmd, timeout=15)
            ok(f"  {label}")
    print()
    info("Zusätzlich am TV: Entwickleroptionen > Netzwerk-Debugging AUS. "
         "So bleibt Port 5555 dauerhaft zu.")
    if not args.apply:
        warn("Nur Vorschau. Zum Ausführen --apply anhängen.")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bravia_harden",
        description="Optimierungs-/Härtungs-Tool für ältere Sony-Bravia-Android-TVs "
                    "(über ADB). Standard ist DRY-RUN; echte Änderungen brauchen --apply.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--serial", help="ADB-Ziel (ip:port). Standard: einziges "
                                    "verbundenes Gerät automatisch.")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("audit", help="Read-only Inventur (Gerät, Pakete, Settings)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("apps", help="Apps auflisten/deaktivieren/entfernen/zurückholen")
    sp.add_argument("--list", action="store_true", help="Kandidaten klassifiziert listen")
    sp.add_argument("--recommend", action="store_true",
                    help="Kuratierte Entfernen-Empfehlung (nur installierte)")
    sp.add_argument("--ai-classify", action="store_true",
                    help="KI ordnet mehrdeutige Systempakete ein (beratend; "
                         "braucht anthropic-SDK + API-Key)")
    sp.add_argument("--disable", help="Komma-Liste: per Nutzer deaktivieren (reversibel)")
    sp.add_argument("--enable", help="Komma-Liste: wieder aktivieren")
    sp.add_argument("--uninstall", help="Komma-Liste: für Nutzer entfernen (reversibel)")
    sp.add_argument("--restore", help="Komma-Liste: wieder installieren")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--apply", action="store_true", help="Änderungen wirklich ausführen")
    sp.add_argument("--force", action="store_true",
                    help="Sperrliste übergehen (GEFÄHRLICH, kann TV lahmlegen)")
    sp.set_defaults(func=cmd_apps)

    sp = sub.add_parser("privacy", help="Telemetrie/Ads-Pakete + Datenschutz-Checkliste")
    sp.add_argument("--disable-telemetry", action="store_true",
                    help="Erkannte Telemetrie/Ads-Pakete deaktivieren")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_privacy)

    sp = sub.add_parser("services", help="Bluetooth/WLAN-Status, optional Bluetooth aus")
    sp.add_argument("--disable-bluetooth", action="store_true")
    sp.add_argument("--enable-bluetooth", action="store_true")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_services)

    sp = sub.add_parser("run", help="Orchestrierter Schicht-für-Schicht-Durchlauf")
    sp.add_argument("--apply", action="store_true",
                    help="Sichere Layer (Telemetrie, opt. Bluetooth) real ausführen")
    sp.add_argument("--disable-bluetooth", action="store_true")
    sp.add_argument("--include-lockdown", action="store_true",
                    help="Am Ende Netzwerk-ADB abschalten (kappt die Verbindung)")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("wlan", help="Router-seitige WLAN-Härtungs-Checkliste")
    sp.set_defaults(func=cmd_wlan)

    sp = sub.add_parser("lockdown", help="Netzwerk-ADB am Ende abschalten")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_lockdown)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AdbError as e:
        err(str(e))
        return 3
    except KeyboardInterrupt:
        err("Abgebrochen.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
