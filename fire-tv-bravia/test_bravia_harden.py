#!/usr/bin/env python3
"""
Tests für bravia_harden.py – laufen ohne echtes Gerät und ohne adb.

Der ADB-Layer (`shell`) wird durch ein Fake ersetzt, das kanned Ausgaben eines
fiktiven Sony-Bravia-Android-TV liefert. Damit werden Klassifizierung,
Sperrliste, Empfehlungen und die (reversible) Dry-run/Apply-Logik geprüft.

Ausführen:  python3 test_bravia_harden.py        (stdlib unittest)
       oder  pytest test_bravia_harden.py
"""

import unittest

import bravia_harden as bh


# Fiktives Gerät: Paketlisten + getprop/settings-Antworten.
FAKE_PACKAGES_ALL = [
    "android",
    "com.android.systemui",
    "com.google.android.gms",
    "com.google.android.gms.analytics",   # von gms-Prefix geschützt -> critical
    "com.sony.dtv.tvx",                    # Sony-Framework -> critical
    "com.sony.dtv.acr",                    # Sony-Prefix -> critical (geschützt)
    "com.netflix.ninja",                   # optional + empfohlen entfernbar
    "com.spotify.tv.android",              # optional + empfohlen entfernbar
    "com.amazon.avod",                     # optional + empfohlen entfernbar
    "com.somevendor.customapp",            # optional (nicht empfohlen)
    "com.example.trackingsdk",             # telemetry
]
FAKE_THIRD_PARTY = [
    "com.netflix.ninja", "com.spotify.tv.android", "com.amazon.avod",
    "com.somevendor.customapp", "com.example.trackingsdk",
]
FAKE_DISABLED: list[str] = []


class FakeShell:
    """Ersetzt bh.shell. Zeichnet ausgeführte Kommandos auf."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, serial, cmd, timeout=20.0):
        self.calls.append(cmd)
        if cmd.startswith("getprop"):
            prop = cmd.split()[1]
            return {
                "ro.build.version.sdk": "25",
                "ro.product.manufacturer": "Sony",
                "ro.product.model": "BRAVIA 4K GB",
                "ro.build.version.release": "7.0",
                "ro.build.version.security_patch": "2018-05-01",
            }.get(prop, "unknown") + "\n"
        if cmd.startswith("pm list packages -3"):
            return "".join(f"package:{p}\n" for p in FAKE_THIRD_PARTY)
        if cmd.startswith("pm list packages -d"):
            return "".join(f"package:{p}\n" for p in FAKE_DISABLED)
        if cmd.startswith("pm list packages"):
            return "".join(f"package:{p}\n" for p in FAKE_PACKAGES_ALL)
        if cmd.startswith("settings get"):
            return "1\n"
        if cmd.startswith(("pm ", "cmd ", "svc ")):
            return "Success\n"
        return "\n"


class ClassificationTests(unittest.TestCase):
    def test_is_critical(self):
        self.assertTrue(bh.is_critical("com.google.android.gms"))
        self.assertTrue(bh.is_critical("com.google.android.gms.analytics"))
        self.assertTrue(bh.is_critical("com.sony.dtv.tvx"))
        self.assertTrue(bh.is_critical("com.android.systemui"))
        self.assertTrue(bh.is_critical("android"))
        self.assertFalse(bh.is_critical("com.netflix.ninja"))
        self.assertFalse(bh.is_critical("com.example.trackingsdk"))

    def test_looks_like_telemetry(self):
        self.assertTrue(bh.looks_like_telemetry("com.example.trackingsdk"))
        self.assertTrue(bh.looks_like_telemetry("com.foo.analytics"))
        self.assertFalse(bh.looks_like_telemetry("com.netflix.ninja"))

    def test_critical_beats_telemetry(self):
        # gms.analytics enthält 'analytics', ist aber per Sperrliste geschützt.
        pkg = "com.google.android.gms.analytics"
        self.assertTrue(bh.is_critical(pkg))


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self._orig = bh.shell
        bh.shell = FakeShell()

    def tearDown(self):
        bh.shell = self._orig

    def test_ensure_android_tv_ok(self):
        bh.ensure_android_tv("dev")  # SDK=25 -> kein Fehler

    def test_ensure_android_tv_non_android(self):
        def non_android(serial, cmd, timeout=20.0):
            return "\n"  # kein numerisches SDK
        bh.shell = non_android
        with self.assertRaises(bh.AdbError):
            bh.ensure_android_tv("dev")

    def test_classify(self):
        pkgs = bh.classify_packages("dev")
        by = {p["package"]: p for p in pkgs}
        self.assertEqual(by["com.google.android.gms"]["category"], "critical")
        self.assertEqual(by["com.sony.dtv.acr"]["category"], "critical")
        self.assertEqual(by["com.netflix.ninja"]["category"], "optional")
        self.assertEqual(by["com.example.trackingsdk"]["category"], "telemetry")
        self.assertTrue(by["com.netflix.ninja"]["recommended_remove"])
        self.assertFalse(by["com.somevendor.customapp"]["recommended_remove"])
        # Kritische sind nie 'recommended_remove'
        self.assertFalse(by["com.google.android.gms"]["recommended_remove"])

    def test_recommended_installed(self):
        pkgs = bh.classify_packages("dev")
        rec = set(bh.recommended_installed(pkgs))
        self.assertEqual(
            rec, {"com.netflix.ninja", "com.spotify.tv.android", "com.amazon.avod"}
        )


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeShell()
        self._orig = bh.shell
        bh.shell = self.fake

    def tearDown(self):
        bh.shell = self._orig

    def test_dryrun_executes_nothing(self):
        rc = bh.apply_action("dev", "uninstall", ["com.netflix.ninja"],
                             apply=False, force=False)
        self.assertEqual(rc, 0)
        # Im Dry-run darf kein pm-Kommando abgesetzt worden sein.
        self.assertFalse(any(c.startswith("pm uninstall") for c in self.fake.calls))

    def test_critical_guarded(self):
        rc = bh.apply_action("dev", "uninstall", ["com.google.android.gms"],
                             apply=True, force=False)
        self.assertEqual(rc, 1)  # übersprungen -> rc != 0
        self.assertFalse(any("com.google.android.gms" in c for c in self.fake.calls))

    def test_force_overrides_guard(self):
        rc = bh.apply_action("dev", "disable", ["com.google.android.gms"],
                             apply=True, force=True)
        self.assertEqual(rc, 0)
        self.assertTrue(any("com.google.android.gms" in c for c in self.fake.calls))

    def test_apply_runs_command(self):
        rc = bh.apply_action("dev", "uninstall", ["com.netflix.ninja"],
                             apply=True, force=False)
        self.assertEqual(rc, 0)
        self.assertIn("pm uninstall --user 0 com.netflix.ninja", self.fake.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
