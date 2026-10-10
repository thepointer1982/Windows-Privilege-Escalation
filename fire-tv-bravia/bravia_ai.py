#!/usr/bin/env python3
"""
bravia_ai.py

Gemeinsame, optionale KI-Anbindung für die Bravia-Tools (Claude via anthropic-SDK).

Alles hier ist OPTIONAL und lazy: die anderen Tools bleiben abhängigkeitsfrei,
solange kein --ai/--ai-classify genutzt wird. Erst dann wird `anthropic`
importiert und ein API-Key (ANTHROPIC_API_KEY oder `ant auth login`-Profil)
benötigt.

Datensparsamkeit: die Aufrufer schicken nur nicht-identifizierende Daten
(Paketnamen, Modell/Version/Patch) - keine Hostnames, IPs oder Fingerprints.
"""

from __future__ import annotations

import json
import re

# Modell für einmalige Analyse-/Klassifizierungs-Anfragen.
AI_MODEL = "claude-opus-4-8"


class AiUnavailable(RuntimeError):
    """anthropic-SDK fehlt oder kein API-Key konfiguriert."""


def available() -> bool:
    """True, wenn das anthropic-SDK importierbar ist."""
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def ask(prompt: str, max_tokens: int = 1200, system: str | None = None) -> str:
    """Einmalige Anfrage an Claude. Gibt den Antworttext zurück.

    Wirft AiUnavailable, wenn das SDK fehlt. Andere Fehler (Auth, Netz) werden
    als RuntimeError mit der Ursache durchgereicht, damit der Aufrufer sie
    dem Nutzer zeigen kann.
    """
    try:
        import anthropic
    except ImportError as e:
        raise AiUnavailable(
            "anthropic-SDK nicht installiert. `pip install anthropic` und "
            "ANTHROPIC_API_KEY setzen (oder `ant auth login`)."
        ) from e

    kwargs = {
        "model": AI_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(**kwargs)
    except Exception as e:  # noqa: BLE001 - Ursache an den Nutzer weiterreichen
        raise RuntimeError(f"KI-Aufruf fehlgeschlagen: {e}") from e

    return "".join(
        b.text for b in resp.content if getattr(b, "type", "") == "text"
    ).strip()


# --------------------------------------------------------------------------- #
# Paket-Klassifizierung (für bravia_harden apps --ai-classify)
# --------------------------------------------------------------------------- #

CLASSIFY_SYSTEM = (
    "Du bist Experte für Android-TV- und Sony-Bravia-Systempakete. Du "
    "klassifizierst Paketnamen konservativ, ohne den Fernseher zu gefährden."
)


def build_classify_prompt(packages: list[str]) -> str:
    """Baut den Prompt zur Klassifizierung unbekannter Paketnamen."""
    listing = "\n".join(f"- {p}" for p in packages)
    return (
        "Klassifiziere die folgenden Android-TV-/Sony-Bravia-Paketnamen. "
        "Für jedes Paket eines von:\n"
        "  \"critical\"  = Systemkern/Framework, niemals entfernen (bricht den TV)\n"
        "  \"keep\"      = Systemfunktion, unauffällig, sollte bleiben\n"
        "  \"optional\"  = App/Funktion, für den Nutzer entfernbar wenn ungenutzt\n"
        "  \"telemetry\" = Tracking/Analytics/Ads/ACR (Datensammlung)\n\n"
        "Antworte AUSSCHLIESSLICH mit einem JSON-Array von Objekten mit den "
        "Feldern \"package\", \"category\", \"reason\" (kurz, deutsch). Keine "
        "Erklärung davor/danach, kein Markdown.\n\n"
        "Pakete:\n" + listing
    )


def parse_classify_response(text: str) -> list[dict]:
    """Extrahiert das JSON-Array robust (toleriert Code-Fences/Rauschen)."""
    if not text:
        return []
    # Code-Fences entfernen.
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    # Erst direkter Parse-Versuch.
    for candidate in (cleaned, _extract_json_array(cleaned)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, list):
            out = []
            for item in data:
                if isinstance(item, dict) and "package" in item:
                    out.append({
                        "package": str(item.get("package", "")),
                        "category": str(item.get("category", "keep")),
                        "reason": str(item.get("reason", "")),
                    })
            return out
    return []


def _extract_json_array(text: str) -> str | None:
    """Schneidet vom ersten '[' bis zum letzten ']' heraus."""
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return None


def classify_packages(packages: list[str]) -> list[dict]:
    """Fragt Claude nach der Klassifizierung. Kann AiUnavailable/RuntimeError werfen."""
    if not packages:
        return []
    text = ask(build_classify_prompt(packages), max_tokens=2000, system=CLASSIFY_SYSTEM)
    return parse_classify_response(text)
