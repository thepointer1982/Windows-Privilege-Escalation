#!/usr/bin/env python3
"""
firetv_bravia_connect.py

Netzwerk- und Diagnose-Tool, um über einen Fire-TV-Stick am Sony-Bravia-
Fernseher die Verbindung im Heimnetz herzustellen.

Fire TV und moderne Sony-Bravia-Geraete laufen auf Android TV. Der saubere
Steuerungsweg ist deshalb ADB over Network (TCP 5555). Zusaetzlich bietet die
Bravia eine eigene Steuerschnittstelle (Simple IP Control auf TCP 20060 und die
Scalar Web API auf Port 80).

Das Tool arbeitet in Stufen:
  1. scan      - lokales Subnetz ermitteln, Ping-Sweep + Port-Probing
  2. identify  - gefundene Hosts als Fire TV / Bravia / sonstiges einordnen
  3. connect   - per `adb connect <ip>:5555` verbinden und verifizieren
  4. diagnose  - kompletter Durchlauf mit Klartext- (oder JSON-) Report

Es kommt mit der Python-Standardbibliothek aus. `adb` und `ping` werden - falls
vorhanden - ueber subprocess genutzt; `zeroconf` wird optional fuer mDNS-
Discovery verwendet, ist aber nicht erforderlich.

Beispiele:
    python3 firetv_bravia_connect.py diagnose
    python3 firetv_bravia_connect.py scan --subnet 192.168.178.0/24
    python3 firetv_bravia_connect.py connect 192.168.178.42
    python3 firetv_bravia_connect.py diagnose --json > report.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Iterable

# --------------------------------------------------------------------------- #
# Bekannte Ports der beteiligten Geraeteklassen
# --------------------------------------------------------------------------- #

# Port -> (Kurzname, welche Geraeteklasse er nahelegt)
KNOWN_PORTS: dict[int, tuple[str, str]] = {
    5555: ("adb", "androidtv"),        # ADB over network (Fire TV / Bravia)
    5556: ("adb-tls", "androidtv"),    # ADB-over-TLS pairing (Android 11+)
    8009: ("googlecast", "androidtv"), # Google Cast
    8008: ("firetv-companion", "firetv"),
    7000: ("firetv-companion", "firetv"),
    20060: ("sony-simple-ip", "bravia"),  # Sony Simple IP Control
    80: ("http-scalar", "bravia"),        # Bravia Scalar Web API / DIAL
    443: ("https", "generic"),
}

# Ports, die beim schnellen Scan geprobt werden (Reihenfolge = Aussagekraft)
DEFAULT_PROBE_PORTS = [5555, 20060, 8009, 8008, 7000, 80, 5556, 443]

ADB_PORT = 5555


# --------------------------------------------------------------------------- #
# Datenmodelle
# --------------------------------------------------------------------------- #

@dataclass
class Host:
    ip: str
    hostname: str | None = None
    reachable: bool = False
    open_ports: list[int] = field(default_factory=list)
    device_type: str = "unknown"   # firetv | bravia | androidtv | generic | unknown
    confidence: str = "low"        # low | medium | high
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Hilfsfunktionen: Ausgabe
# --------------------------------------------------------------------------- #

_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(text: str, color: str) -> str:
    if not _USE_COLOR:
        return text
    codes = {"green": "32", "yellow": "33", "red": "31", "cyan": "36", "bold": "1"}
    return f"\033[{codes.get(color, '0')}m{text}\033[0m"


def info(msg: str) -> None:
    print(f"[*] {msg}", file=sys.stderr)


def ok(msg: str) -> None:
    print(_c(f"[+] {msg}", "green"), file=sys.stderr)


def warn(msg: str) -> None:
    print(_c(f"[!] {msg}", "yellow"), file=sys.stderr)


def err(msg: str) -> None:
    print(_c(f"[-] {msg}", "red"), file=sys.stderr)


# --------------------------------------------------------------------------- #
# Netz-Ermittlung
# --------------------------------------------------------------------------- #

def local_ipv4() -> str | None:
    """Ermittelt die primaere lokale IPv4-Adresse (ohne Traffic zu senden)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Ziel muss nicht erreichbar sein; das OS waehlt nur das Interface.
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    finally:
        s.close()


def guess_subnet(prefix: int = 24) -> ipaddress.IPv4Network | None:
    """Leitet aus der lokalen IP ein /24-Subnetz ab."""
    ip = local_ipv4()
    if not ip:
        return None
    try:
        return ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Diagnose: Ping und Port-Probes
# --------------------------------------------------------------------------- #

def ping(ip: str, timeout: float = 1.0) -> bool:
    """ICMP-Ping ueber das System-`ping`, plattformabhaengig."""
    if shutil.which("ping") is None:
        return False
    if sys.platform.startswith("win"):
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), ip]
    try:
        res = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout + 2
        )
        return res.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def probe_port(ip: str, port: int, timeout: float = 0.6) -> bool:
    """TCP-Connect-Probe. Offen = True."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def reverse_dns(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, OSError):
        return None


def scan_host(ip: str, ports: Iterable[int], timeout: float) -> Host | None:
    """Prueft einen Host: erst offene Ports, sonst Ping. Nur 'interessante'
    Hosts (erreichbar oder mit offenem Port) werden zurueckgegeben."""
    host = Host(ip=ip)
    for port in ports:
        if probe_port(ip, port, timeout):
            host.open_ports.append(port)
    if host.open_ports:
        host.reachable = True
    else:
        host.reachable = ping(ip, timeout)
    if not host.reachable:
        return None
    host.hostname = reverse_dns(ip)
    classify(host)
    return host


def scan_subnet(
    network: ipaddress.IPv4Network,
    ports: list[int],
    timeout: float,
    workers: int,
) -> list[Host]:
    """Parallel-Scan ueber alle Hosts eines Subnetzes."""
    hosts_to_scan = [str(h) for h in network.hosts()]
    info(f"Scanne {len(hosts_to_scan)} Adressen in {network} "
         f"(Ports: {', '.join(map(str, ports))}) ...")
    found: list[Host] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(scan_host, ip, ports, timeout): ip for ip in hosts_to_scan
        }
        for fut in concurrent.futures.as_completed(futures):
            host = fut.result()
            if host:
                found.append(host)
    found.sort(key=lambda h: tuple(int(o) for o in h.ip.split(".")))
    return found


# --------------------------------------------------------------------------- #
# Geraete-Klassifizierung
# --------------------------------------------------------------------------- #

def classify(host: Host) -> None:
    """Ordnet einen Host anhand offener Ports + Hostname ein."""
    portset = set(host.open_ports)
    hostname = (host.hostname or "").lower()

    has_adb = ADB_PORT in portset or 5556 in portset
    has_sony_ip = 20060 in portset
    has_scalar = 80 in portset
    has_firetv_companion = bool(portset & {7000, 8008})

    # Sony Bravia: Simple IP Control ist ein starkes Signal.
    if has_sony_ip:
        host.device_type = "bravia"
        host.confidence = "high"
        host.notes.append("Sony Simple IP Control (20060) offen")
        if has_adb:
            host.notes.append("ADB (5555) offen - Android-TV-Bravia, per adb steuerbar")
        return

    # Hostname-Heuristiken
    if any(tag in hostname for tag in ("bravia", "sony")):
        host.device_type = "bravia"
        host.confidence = "medium"
        host.notes.append(f"Hostname deutet auf Bravia hin: {host.hostname}")
        return
    if any(tag in hostname for tag in ("amazon", "firetv", "fire-tv", "aftv", "aft")):
        host.device_type = "firetv"
        host.confidence = "medium"
        host.notes.append(f"Hostname deutet auf Fire TV hin: {host.hostname}")
        if has_adb:
            host.notes.append("ADB (5555) offen - per adb verbindbar")
        return

    if has_firetv_companion and has_adb:
        host.device_type = "firetv"
        host.confidence = "medium"
        host.notes.append("Fire-TV-Companion-Port + ADB offen")
        return

    if has_adb:
        host.device_type = "androidtv"
        host.confidence = "medium"
        host.notes.append("ADB (5555) offen - Android-TV-Geraet (Fire TV oder Bravia)")
        return

    if 8009 in portset:
        host.device_type = "androidtv"
        host.confidence = "low"
        host.notes.append("Google Cast (8009) offen")
        return

    if has_scalar:
        host.device_type = "generic"
        host.confidence = "low"
        host.notes.append("HTTP/Scalar (80) offen - koennte Bravia Web API sein")


# --------------------------------------------------------------------------- #
# Optional: mDNS-Discovery via zeroconf
# --------------------------------------------------------------------------- #

def mdns_discover(duration: float = 4.0) -> list[Host]:
    """Sucht per mDNS nach Android-TV-/Cast-Diensten. Braucht `zeroconf`."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf  # type: ignore
    except ImportError:
        warn("zeroconf nicht installiert - mDNS-Discovery uebersprungen "
             "(pip install zeroconf)")
        return []

    services = [
        "_androidtvremote2._tcp.local.",
        "_googlecast._tcp.local.",
        "_amzn-wplay._tcp.local.",   # Amazon Fire TV
    ]
    results: dict[str, Host] = {}

    class _Listener:
        def add_service(self, zc, type_, name):
            inf = zc.get_service_info(type_, name, timeout=2000)
            if not inf or not inf.addresses:
                return
            for raw in inf.addresses:
                ip = socket.inet_ntoa(raw)
                host = results.setdefault(ip, Host(ip=ip, reachable=True))
                host.notes.append(f"mDNS: {name}")
                if "amzn" in type_ or "amzn" in name.lower():
                    host.device_type = "firetv"
                    host.confidence = "high"
                elif "androidtvremote2" in type_:
                    if host.device_type == "unknown":
                        host.device_type = "androidtv"
                        host.confidence = "medium"

        def update_service(self, *a):  # noqa: D401 - Zeroconf-API
            pass

        def remove_service(self, *a):
            pass

    info(f"mDNS-Discovery laeuft {duration:.0f}s ...")
    zc = Zeroconf()
    try:
        for svc in services:
            ServiceBrowser(zc, svc, _Listener())
        time.sleep(duration)
    finally:
        zc.close()
    return list(results.values())


# --------------------------------------------------------------------------- #
# ADB-Verbindung
# --------------------------------------------------------------------------- #

def adb_available() -> bool:
    return shutil.which("adb") is not None


def _adb(*args: str, timeout: float = 15.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )


def adb_connect(ip: str, port: int = ADB_PORT) -> tuple[bool, str]:
    """Fuehrt `adb connect ip:port` aus. Gibt (erfolg, meldung) zurueck."""
    if not adb_available():
        return False, ("adb nicht gefunden. Installiere die Android Platform Tools "
                       "(z.B. `apt install adb`, `brew install android-platform-tools`, "
                       "oder Download von developer.android.com).")
    target = f"{ip}:{port}"
    try:
        # ADB-Server sicherstellen
        _adb("start-server", timeout=10)
        res = _adb("connect", target, timeout=20)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"adb-Aufruf fehlgeschlagen: {e}"
    out = res.stdout.strip()
    success = "connected to" in out.lower() or "already connected" in out.lower()
    if "unauthorized" in out.lower():
        return False, (out + "\n    -> Am Fernseher den RSA-Fingerprint bestaetigen "
                       "(ADB-Debugging-Popup 'Zulassen').")
    return success, out


def adb_devices() -> str:
    if not adb_available():
        return "(adb nicht verfuegbar)"
    try:
        return _adb("devices", "-l", timeout=10).stdout.strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"(adb devices fehlgeschlagen: {e})"


def adb_device_model(ip: str, port: int = ADB_PORT) -> str | None:
    """Liest ro.product.model aus, wenn autorisiert verbunden."""
    if not adb_available():
        return None
    try:
        res = _adb("-s", f"{ip}:{port}", "shell", "getprop", "ro.product.model",
                   timeout=10)
        model = res.stdout.strip()
        return model or None
    except (subprocess.TimeoutExpired, OSError):
        return None


# --------------------------------------------------------------------------- #
# Report-Ausgabe
# --------------------------------------------------------------------------- #

def print_host(host: Host) -> None:
    label = {
        "firetv": _c("Fire TV", "cyan"),
        "bravia": _c("Sony Bravia", "cyan"),
        "androidtv": _c("Android TV", "cyan"),
        "generic": "generisch",
        "unknown": "unbekannt",
    }.get(host.device_type, host.device_type)
    name = f" ({host.hostname})" if host.hostname else ""
    ports = ", ".join(
        f"{p}/{KNOWN_PORTS.get(p, ('?', ''))[0]}" for p in host.open_ports
    ) or "-"
    print(f"  {_c(host.ip, 'bold')}{name}")
    print(f"      Typ       : {label}  [Konfidenz: {host.confidence}]")
    print(f"      Ports     : {ports}")
    for note in host.notes:
        print(f"      Hinweis   : {note}")


def candidates(hosts: list[Host]) -> list[Host]:
    """Hosts, die als Fire TV / Bravia / Android TV in Frage kommen."""
    return [h for h in hosts if h.device_type in ("firetv", "bravia", "androidtv")]


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def resolve_subnet(arg: str | None) -> ipaddress.IPv4Network | None:
    if arg:
        try:
            return ipaddress.ip_network(arg, strict=False)
        except ValueError:
            err(f"Ungueltiges Subnetz: {arg}")
            return None
    return guess_subnet()


def cmd_scan(args) -> int:
    net = resolve_subnet(args.subnet)
    if not net:
        err("Konnte kein Subnetz ermitteln. Bitte mit --subnet angeben, "
            "z.B. --subnet 192.168.178.0/24")
        return 2
    ports = [int(p) for p in args.ports.split(",")] if args.ports else DEFAULT_PROBE_PORTS
    hosts = scan_subnet(net, ports, args.timeout, args.workers)

    if args.mdns:
        for mh in mdns_discover(args.mdns_time):
            if not any(h.ip == mh.ip for h in hosts):
                hosts.append(mh)

    if args.json:
        print(json.dumps([h.as_dict() for h in hosts], indent=2))
        return 0

    if not hosts:
        warn("Keine erreichbaren Hosts gefunden.")
        return 1
    print()
    ok(f"{len(hosts)} erreichbare Hosts:")
    for h in hosts:
        print_host(h)
    cands = candidates(hosts)
    print()
    if cands:
        ok(f"{len(cands)} TV-Kandidat(en): "
           + ", ".join(f"{h.ip} ({h.device_type})" for h in cands))
    else:
        warn("Keine eindeutigen Fire-TV-/Bravia-Kandidaten. Ggf. am TV "
             "'ADB-Debugging' / 'Vom PC ueber Netzwerk' aktivieren.")
    return 0


def cmd_connect(args) -> int:
    ip = args.ip
    port = args.port
    info(f"Pruefe ADB-Port {ip}:{port} ...")
    if not probe_port(ip, port, timeout=1.5):
        warn(f"Port {port} auf {ip} ist nicht offen. Am Geraet ADB-Debugging "
             "aktivieren (Fire TV: Einstellungen > My Fire TV > Entwickleroptionen "
             "> ADB-Debugging; Bravia: Einstellungen > Geraetevorlieben > "
             "Entwickleroptionen).")
        # Trotzdem Verbindungsversuch, falls Probe nur geblockt war.
    ok(f"Verbinde per adb -> {ip}:{port}")
    success, msg = adb_connect(ip, port)
    print(f"    {msg}")
    if not success:
        err("ADB-Verbindung nicht bestaetigt.")
        print()
        info("Aktueller adb-Status:")
        print(adb_devices())
        return 1
    ok("ADB-Verbindung steht.")
    model = adb_device_model(ip, port)
    if model:
        ok(f"Geraetemodell: {model}")
    print()
    info("adb devices:")
    print(adb_devices())
    return 0


def cmd_diagnose(args) -> int:
    """Kompletter Durchlauf: scannen, klassifizieren, besten Kandidaten verbinden."""
    net = resolve_subnet(args.subnet)
    report: dict = {"subnet": str(net) if net else None, "hosts": [], "connection": None}

    if not net:
        err("Kein Subnetz ermittelbar - bitte --subnet angeben.")
        return 2

    lip = local_ipv4()
    if not args.json:
        info(f"Lokale IP: {lip}   Subnetz: {net}")
        info(f"adb verfuegbar: {'ja' if adb_available() else 'nein'}   "
             f"ping verfuegbar: {'ja' if shutil.which('ping') else 'nein'}")

    hosts = scan_subnet(net, DEFAULT_PROBE_PORTS, args.timeout, args.workers)
    if args.mdns:
        for mh in mdns_discover(args.mdns_time):
            if not any(h.ip == mh.ip for h in hosts):
                hosts.append(mh)
    report["hosts"] = [h.as_dict() for h in hosts]

    cands = candidates(hosts)
    # Priorisierung: Geraete mit offenem ADB-Port zuerst, dann Bravia/FireTV.
    def rank(h: Host) -> tuple:
        return (
            0 if ADB_PORT in h.open_ports else 1,
            {"high": 0, "medium": 1, "low": 2}.get(h.confidence, 3),
        )
    cands.sort(key=rank)

    connected = None
    if cands and not args.no_connect:
        best = cands[0]
        if ADB_PORT in best.open_ports:
            if not args.json:
                print()
                info(f"Bester Kandidat: {best.ip} ({best.device_type}) - "
                     f"versuche ADB-Verbindung.")
            success, msg = adb_connect(best.ip)
            connected = {"ip": best.ip, "success": success, "message": msg}
            if success:
                model = adb_device_model(best.ip)
                if model:
                    connected["model"] = model
        else:
            if not args.json:
                warn(f"Bester Kandidat {best.ip} hat keinen offenen ADB-Port (5555). "
                     "Am Geraet ADB-Debugging aktivieren, dann `connect` erneut.")
    report["connection"] = connected

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    # Klartext-Report
    print()
    if hosts:
        ok(f"{len(hosts)} erreichbare Hosts:")
        for h in hosts:
            print_host(h)
    else:
        warn("Keine erreichbaren Hosts gefunden.")

    print()
    if cands:
        ok("TV-Kandidaten (priorisiert):")
        for h in cands:
            print(f"  - {h.ip:<15} {h.device_type:<10} [{h.confidence}]")
    else:
        warn("Keine Fire-TV-/Bravia-Kandidaten erkannt.")

    print()
    if connected and connected["success"]:
        ok(f"Verbindung hergestellt zu {connected['ip']}"
           + (f" ({connected['model']})" if connected.get("model") else ""))
        print()
        info("adb devices:")
        print(adb_devices())
    elif connected:
        warn(f"Verbindungsversuch zu {connected['ip']} nicht bestaetigt:")
        print(f"    {connected['message']}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="firetv_bravia_connect",
        description="Netzwerk-/Diagnose-Tool: ueber Fire TV am Sony Bravia die "
                    "Verbindung im Heimnetz herstellen.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--subnet", help="Subnetz in CIDR, z.B. 192.168.178.0/24 "
                                         "(Standard: automatisch ermitteln)")
        sp.add_argument("--timeout", type=float, default=0.6,
                        help="Timeout pro Port-Probe in Sekunden (Standard 0.6)")
        sp.add_argument("--workers", type=int, default=100,
                        help="Parallele Scan-Threads (Standard 100)")
        sp.add_argument("--mdns", action="store_true",
                        help="Zusaetzlich mDNS-Discovery (braucht zeroconf)")
        sp.add_argument("--mdns-time", type=float, default=4.0,
                        help="Dauer der mDNS-Discovery in Sekunden")
        sp.add_argument("--json", action="store_true", help="Ausgabe als JSON")

    sp_scan = sub.add_parser("scan", help="Subnetz scannen und Geraete auflisten")
    add_common(sp_scan)
    sp_scan.add_argument("--ports", help="Komma-Liste zu probender Ports "
                                        "(Standard: relevante TV-Ports)")
    sp_scan.set_defaults(func=cmd_scan)

    sp_conn = sub.add_parser("connect", help="Per adb mit einer IP verbinden")
    sp_conn.add_argument("ip", help="IP-Adresse des Fire TV / der Bravia")
    sp_conn.add_argument("--port", type=int, default=ADB_PORT,
                         help=f"ADB-Port (Standard {ADB_PORT})")
    sp_conn.set_defaults(func=cmd_connect)

    sp_diag = sub.add_parser("diagnose", help="Kompletter Durchlauf: scannen + "
                                             "besten Kandidaten verbinden")
    add_common(sp_diag)
    sp_diag.add_argument("--no-connect", action="store_true",
                         help="Nur diagnostizieren, nicht automatisch verbinden")
    sp_diag.set_defaults(func=cmd_diagnose)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        err("Abgebrochen.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
