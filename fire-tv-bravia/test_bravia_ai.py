#!/usr/bin/env python3
"""
Tests für bravia_ai.py - nur reine Logik (Prompt-Bau, robustes Parsen).
Es werden KEINE Netz-/API-Aufrufe gemacht.

Ausführen:  python3 test_bravia_ai.py    (stdlib unittest)
       oder  pytest test_bravia_ai.py
"""

import unittest

import bravia_ai as ai


class PromptTests(unittest.TestCase):
    def test_prompt_contains_all_packages(self):
        pkgs = ["com.sony.dtv.foo", "com.example.bar"]
        prompt = ai.build_classify_prompt(pkgs)
        for p in pkgs:
            self.assertIn(p, prompt)
        # Kategorien müssen erklärt sein.
        for cat in ("critical", "keep", "optional", "telemetry"):
            self.assertIn(cat, prompt)


class ParseTests(unittest.TestCase):
    def test_plain_json_array(self):
        text = '[{"package":"a","category":"keep","reason":"x"}]'
        out = ai.parse_classify_response(text)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["package"], "a")
        self.assertEqual(out[0]["category"], "keep")

    def test_json_with_code_fence(self):
        text = '```json\n[{"package":"a","category":"telemetry"}]\n```'
        out = ai.parse_classify_response(text)
        self.assertEqual(out[0]["category"], "telemetry")
        self.assertEqual(out[0]["reason"], "")  # fehlendes Feld -> leer

    def test_json_with_surrounding_prose(self):
        text = 'Hier die Klassifizierung:\n[{"package":"a","category":"optional"}]\nFertig.'
        out = ai.parse_classify_response(text)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["package"], "a")

    def test_garbage_returns_empty(self):
        self.assertEqual(ai.parse_classify_response("kein json hier"), [])
        self.assertEqual(ai.parse_classify_response(""), [])

    def test_items_without_package_skipped(self):
        text = '[{"category":"keep"},{"package":"b","category":"keep"}]'
        out = ai.parse_classify_response(text)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["package"], "b")

    def test_non_list_returns_empty(self):
        self.assertEqual(ai.parse_classify_response('{"package":"a"}'), [])


class AvailabilityTests(unittest.TestCase):
    def test_available_is_bool(self):
        self.assertIsInstance(ai.available(), bool)

    def test_ask_without_sdk_raises_aiunavailable(self):
        # Wenn das SDK nicht installiert ist, muss ask() sauber AiUnavailable werfen.
        if ai.available():
            self.skipTest("anthropic-SDK ist installiert - Negativfall nicht prüfbar")
        with self.assertRaises(ai.AiUnavailable):
            ai.ask("hallo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
