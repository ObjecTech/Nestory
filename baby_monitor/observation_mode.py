from __future__ import annotations

"""Durable observation subjects; absence of evidence is not an infant label."""
import json

MODES = {"infant", "adult_test", "legacy_unknown"}


def mode_name(person_test_mode: bool) -> str:
    return "adult_test" if person_test_mode else "infant"


def validate_mode(value: str) -> str:
    if value not in MODES:
        raise ValueError("unsupported observation mode")
    return value


def mode_from_metadata(raw: str | None) -> str:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return "legacy_unknown"
    if not isinstance(data, dict):
        return "legacy_unknown"
    explicit = data.get("observation_mode")
    boolean = data.get("person_test_mode")
    if isinstance(explicit, str) and explicit in MODES:
        if type(boolean) is bool and explicit != mode_name(boolean):
            return "legacy_unknown"
        return explicit
    if type(boolean) is bool:
        return mode_name(boolean)
    if "original_metadata" in data:
        return mode_from_metadata(json.dumps(data["original_metadata"]))
    return "legacy_unknown"
