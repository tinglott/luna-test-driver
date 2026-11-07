"""Role-config loader + validator (DESIGN.md section 9.2).

Loads ``roles/clients/<client_id>/<role_id>.json``, validates it against
``roles/schema/role-config.schema.json`` (jsonschema, draft-07), and runs the
extra checks the schema alone cannot express (crisis-line country check).

Fail closed: any problem raises RoleConfigInvalid. The caller must then run
in message-taking-only mode and log ``role_config_invalid`` — it must never
improvise and never call the LLM with a bad config.

No secrets ever belong in a role config. This module reads files only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

KNOWN_TOOLS = {
    "calendar.freeBusy",
    "calendar.createEvent",
    "message.take",
    "knowledge.lookup",
}

CRISIS_KEYWORDS_DEFAULT = [
    "hurt myself", "kill myself", "suicide", "self-harm",
    # FIX #5 (bench battery, Nov 7 — same class as the guardrail
    # suite's CR-05): phrases callers actually use.
    "better off without me", "better off dead", "end my life",
    "don't want to live", "want to die", "end it all",
]


class RoleConfigInvalid(Exception):
    """Raised when a role config is missing, unparseable, or fails validation."""


def schema_path(base_dir: str | Path) -> Path:
    return Path(base_dir) / "roles" / "schema" / "role-config.schema.json"


def config_path(base_dir: str | Path, client_id: str, role_id: str) -> Path:
    return Path(base_dir) / "roles" / "clients" / client_id / f"{role_id}.json"


def load_schema(base_dir: str | Path) -> dict:
    path = schema_path(base_dir)
    if not path.exists():
        raise RoleConfigInvalid(f"role-config schema not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def extra_checks(config: dict) -> list[str]:
    """Checks the JSON Schema cannot express. Returns a list of problems."""
    problems: list[str] = []
    tools = config.get("allowedTools") or []
    unknown = [t for t in tools if t not in KNOWN_TOOLS]
    if unknown:
        problems.append(f"unknown allowedTools entries: {unknown}")
    if "calendar.createEvent" in tools:
        rules = config.get("bookingRules") or {}
        if rules.get("requireCallerConfirmation") is not True:
            problems.append(
                "bookingRules.requireCallerConfirmation must be true "
                "for any role that books"
            )
    crisis = (config.get("hardGates") or {}).get("crisis") or {}
    response = crisis.get("response") or ""
    language = (config.get("language") or "").lower()
    phone = ((config.get("business") or {}).get("phone") or "")
    non_us = language.startswith(("en-gb", "pt", "es", "fr", "de")) or phone.startswith("+44")
    if "988" in response and non_us:
        problems.append(
            "crisis response contains US line 988 but the client is not US "
            f"(language={language!r}, phone={phone!r}) — fill in the local "
            "crisis number for the client's country"
        )
    if not response:
        problems.append("hardGates.crisis.response is missing")
    return problems


def validate_config(config: dict, base_dir: str | Path) -> list[str]:
    """Return a list of validation problems (empty list = valid)."""
    schema = load_schema(base_dir)
    validator = jsonschema.Draft7Validator(schema)
    problems = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(config), key=lambda e: e.path)
    ]
    problems.extend(extra_checks(config))
    return problems


def load_role_config(base_dir: str | Path, client_id: str, role_id: str) -> dict:
    """Load and validate one role config. Raises RoleConfigInvalid on any failure."""
    path = config_path(base_dir, client_id, role_id)
    if not path.exists():
        raise RoleConfigInvalid(f"role config not found: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RoleConfigInvalid(f"role config is not valid JSON ({path}): {exc}") from exc
    problems = validate_config(config, base_dir)
    if problems:
        raise RoleConfigInvalid(
            f"role config failed validation ({path}):\n- " + "\n- ".join(problems)
        )
    return config


def safe_fallback_config(client_id: str, role_id: str) -> dict:
    """Built-in safe default used when a real config fails: message-taking only.

    No LLM call, no booking, no invented answers. This is the fail-closed
    behaviour from DESIGN.md section 9.2 step 3.
    """
    return {
        "roleId": role_id,
        "clientId": client_id,
        "version": "0.0.0-fallback",
        "department": "Reception",
        "employee": "Aria",
        "sop": "company/sops/aria.md",
        "tone": "Warm, unhurried, kind and calm.",
        "greeting": (
            "Hello — I'm sorry, our assistant can't take your call in full "
            "right now. May I take your name, number and what you need, "
            "and the practice will call you back today?"
        ),
        "allowedTools": ["message.take"],
        "escalationPaths": [
            {"trigger": "any request", "action": "take_message", "notify": "owner"}
        ],
        "hardGates": {
            "never": ["confirm a booking", "quote a price", "give advice"],
            "crisis": {
                "keywords": CRISIS_KEYWORDS_DEFAULT,
                "response": (
                    "If you are in immediate danger, please call your local "
                    "emergency number now."
                ),
            },
            "unknownAnswer": (
                "I don't have that answer — let me take your details and "
                "the practice will call you back today."
            ),
            "fallback": {
                "messageTaking": (
                    "Let me take your name, number and what you need, and "
                    "the practice will call you back today."
                ),
            },
        },
        "business": {"name": "the practice", "hours": "", "services": []},
        "knowledge": [],
        "knowledgeSources": [],
    }


def load_role_config_or_fallback(base_dir: str | Path, client_id: str, role_id: str):
    """Returns (config, was_valid, problems). Never raises for a bad config."""
    try:
        return load_role_config(base_dir, client_id, role_id), True, []
    except RoleConfigInvalid as exc:
        return safe_fallback_config(client_id, role_id), False, [str(exc)]
