#!/usr/bin/env python3
"""Tests fuer firetv_bravia_connect.py (Scan, Klassifizierung, ADB-Erkennung)."""

import ipaddress
import unittest
from unittest import mock

import firetv_bravia_connect as fc


class ClassifyTests(unittest.TestCase):
    def _host(self, ports, hostname=None):
        h = fc.Host(ip="192.168.178.10", open_ports=list(ports), hostname=hostname)
        fc.classify(h)
        return h

    def test_sony_simple_ip_is_bravia_high(self):
        h = self._host([20060])
        self.assertEqual(h.device_type, "bravia")
        self.assertEqual(h.confidence, "high")

    def test_sony_ip_plus_adb_notes_android_tv(self):
        h = self._host([20060, 5555])
        self.assertEqual(h.device_type, "bravia")
        self.assertTrue(any("ADB" in n for n in h.notes))

    def test_hostname_bravia(self):
        h = self._host([80], hostname="BRAVIA-4K.fritz.box")
        self.assertEqual(h.device_type, "bravia")
        self.assertEqual(h.confidence, "medium")

    def test_hostname_firetv(self):
        h = self._host([5555], hostname="amazon-aftv.fritz.box")
        self.assertEqual(h.device_type, "firetv")

    def test_adb_only_is_androidtv(self):
        h = self._host([5555])
        self.assertEqual(h.device_type, "androidtv")
        self.assertEqual(h.confidence, "medium")

    def test_firetv_companion_plus_adb(self):
        h = self._host([5555, 8008])
        self.assertEqual(h.device_type, "firetv")

    def test_cast_only_low(self):
        h = self._host([8009])
        self.assertEqual(h.device_type, "androidtv")
        self.assertEqual(h.confidence, "low")

    def test_scalar_only_generic(self):
        h = self._host([80])
        self.assertEqual(h.device_type, "generic")

    def test_nothing_stays_unknown(self):
        h = self._host([443])
        self.assertEqual(h.device_type, "unknown")


class ScanSubnetTests(unittest.TestCase):
    """scan_subnet wurde flach ueber (IP, Port) parallelisiert – diese Tests
    sichern Reihenfolge, Ping-Fallback und das Ueberspringen toter Hosts."""

    def setUp(self):
        self.net = ipaddress.ip_network("192.168.178.0/30")  # .1 und .2 als Hosts
        # .1 = Bravia (20060 + 5555 offen), .2 = nur per Ping erreichbar

    def test_open_ports_and_ping_fallback(self):
        open_map = {("192.168.178.1", 20060): True, ("192.168.178.1", 5555): True}

        def fake_probe(ip, port, timeout):
            return open_map.get((ip, port), False)

        def fake_ping(ip, timeout):
            return ip == "192.168.178.2"

        with mock.patch.object(fc, "probe_port", side_effect=fake_probe), \
             mock.patch.object(fc, "ping", side_effect=fake_ping), \
             mock.patch.object(fc, "reverse_dns", return_value=None):
            hosts = fc.scan_subnet(self.net, fc.DEFAULT_PROBE_PORTS, 0.1, 10)

        by_ip = {h.ip: h for h in hosts}
        self.assertIn("192.168.178.1", by_ip)
        self.assertIn("192.168.178.2", by_ip)
        # Offene Ports folgen der Eingabereihenfolge (5555 vor 20060 laut DEFAULT).
        self.assertEqual(by_ip["192.168.178.1"].open_ports, [5555, 20060])
        self.assertEqual(by_ip["192.168.178.1"].device_type, "bravia")
        # Nur per Ping erreichbar -> keine offenen Ports, trotzdem gelistet.
        self.assertEqual(by_ip["192.168.178.2"].open_ports, [])

    def test_dead_hosts_are_skipped(self):
        with mock.patch.object(fc, "probe_port", return_value=False), \
             mock.patch.object(fc, "ping", return_value=False), \
             mock.patch.object(fc, "reverse_dns", return_value=None):
            hosts = fc.scan_subnet(self.net, fc.DEFAULT_PROBE_PORTS, 0.1, 10)
        self.assertEqual(hosts, [])

    def test_results_sorted_by_ip(self):
        with mock.patch.object(fc, "probe_port", return_value=True), \
             mock.patch.object(fc, "ping", return_value=False), \
             mock.patch.object(fc, "reverse_dns", return_value=None):
            hosts = fc.scan_subnet(
                ipaddress.ip_network("192.168.178.0/29"), [5555], 0.1, 10
            )
        ips = [h.ip for h in hosts]
        self.assertEqual(ips, sorted(ips, key=lambda s: tuple(map(int, s.split(".")))))


class AdbConnectTests(unittest.TestCase):
    def _run(self, stdout):
        cp = mock.Mock()
        cp.stdout = stdout
        with mock.patch.object(fc, "adb_available", return_value=True), \
             mock.patch.object(fc, "_adb", return_value=cp):
            return fc.adb_connect("192.168.178.1")

    def test_connected(self):
        ok, msg = self._run("connected to 192.168.178.1:5555")
        self.assertTrue(ok)

    def test_already_connected(self):
        ok, msg = self._run("already connected to 192.168.178.1:5555")
        self.assertTrue(ok)

    def test_unauthorized_is_failure_with_hint(self):
        ok, msg = self._run("failed to authenticate to 192.168.178.1:5555\nunauthorized")
        self.assertFalse(ok)
        self.assertIn("Fingerprint", msg)

    def test_missing_adb(self):
        with mock.patch.object(fc, "adb_available", return_value=False):
            ok, msg = fc.adb_connect("192.168.178.1")
        self.assertFalse(ok)
        self.assertIn("adb nicht gefunden", msg)


class SubnetResolveTests(unittest.TestCase):
    def test_explicit_subnet(self):
        net = fc.resolve_subnet("192.168.178.0/24")
        self.assertEqual(str(net), "192.168.178.0/24")

    def test_invalid_subnet_returns_none(self):
        self.assertIsNone(fc.resolve_subnet("not-a-subnet"))

    def test_guess_subnet_uses_local_ip(self):
        with mock.patch.object(fc, "local_ipv4", return_value="192.168.178.55"):
            net = fc.guess_subnet()
        self.assertEqual(str(net), "192.168.178.0/24")


if __name__ == "__main__":
    unittest.main()
