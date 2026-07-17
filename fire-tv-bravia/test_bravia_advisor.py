#!/usr/bin/env python3
"""
Tests für die Offline-Engine von bravia_advisor.py (kein Gerät, keine KI nötig).

Ausführen:  python3 test_bravia_advisor.py    (stdlib unittest)
       oder  pytest test_bravia_advisor.py
"""

import unittest

import bravia_advisor as adv


def make_audit(**over):
    base = {
        "device": {
            "model": "BRAVIA 4K GB", "android_release": "7.0", "sdk": "25",
            "security_patch": "2018-05-01",
        },
        "settings": {
            "adb_enabled": "1", "adb_wifi_enabled": "1",
            "development_settings_enabled": "1", "location_mode": "1",
            "bluetooth_on": "1",
        },
        "packages": [
            {"package": "com.example.trackingsdk", "category": "telemetry",
             "disabled": False, "recommended_remove": False},
            {"package": "com.netflix.ninja", "category": "optional",
             "disabled": False, "recommended_remove": True},
        ],
    }
    base.update(over)
    return base


class AnalyzeTests(unittest.TestCase):
    def ids(self, findings):
        return {f.id for f in findings}

    def test_ancient_patch_is_critical(self):
        findings, score = adv.analyze(make_audit())
        by = {f.id: f for f in findings}
        self.assertIn("patch-ancient", by)
        self.assertEqual(by["patch-ancient"].severity, "critical")
        self.assertLess(score, 60)  # viele Befunde -> Score gedrückt

    def test_clean_device_scores_high(self):
        audit = make_audit(
            device={"model": "X", "android_release": "12", "sdk": "31",
                    "security_patch": "2024-01-01"},
            settings={"adb_enabled": "0", "adb_wifi_enabled": "0",
                      "development_settings_enabled": "0", "location_mode": "0",
                      "bluetooth_on": "0"},
            packages=[],
        )
        findings, score = adv.analyze(audit)
        self.assertEqual(findings, [])
        self.assertEqual(score, 100)

    def test_telemetry_and_bloatware_detected(self):
        findings, _ = adv.analyze(make_audit())
        ids = self.ids(findings)
        self.assertIn("telemetry-active", ids)
        self.assertIn("bloatware-recommended", ids)

    def test_disabled_telemetry_not_flagged(self):
        audit = make_audit(packages=[
            {"package": "com.example.trackingsdk", "category": "telemetry",
             "disabled": True, "recommended_remove": False},
        ])
        findings, _ = adv.analyze(audit)
        self.assertNotIn("telemetry-active", self.ids(findings))

    def test_findings_sorted_by_severity(self):
        findings, _ = adv.analyze(make_audit())
        order = [adv.SEVERITY_ORDER[f.severity] for f in findings]
        self.assertEqual(order, sorted(order))

    def test_flag_helper(self):
        self.assertTrue(adv._flag_on("1"))
        self.assertTrue(adv._flag_on(1))
        self.assertFalse(adv._flag_on("0"))
        self.assertFalse(adv._flag_on(""))

    def test_redacted_summary_excludes_hostnames(self):
        audit = make_audit()
        audit["device"]["fingerprint"] = "secret-fingerprint-xyz"
        findings, score = adv.analyze(audit)
        summary = adv._redacted_summary(audit, findings, score)
        # Nur Modell/Version/SDK/Patch dürfen im Summary auftauchen.
        self.assertNotIn("secret-fingerprint-xyz", summary)
        self.assertIn("BRAVIA 4K GB", summary)

    def test_score_bounds(self):
        # Sehr viele kritische Befunde -> Score bei 0 gekappt, nie negativ.
        audit = make_audit(device={"security_patch": "2015-01-01"})
        _, score = adv.analyze(audit)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
