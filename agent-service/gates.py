"""Deterministic gates — DESIGN.md section 9.4, steps 2, 3 and 5.

The gates run BEFORE any LLM call, on every turn, in this fixed order.
They are plain code and keyword/lookup checks: no LLM, no guessing.

Pipeline order (do not reorder):
  1. load config -> validate (role_loader; fail closed)
  2. HARD GATES (this module: hard_gates)
  3. DETERMINISTIC LOOKUPS (this module: deterministic_lookup)
  4. LLM (brain.py) — only for phrasing / questions gates + lookups did not resolve
  5. POST-LLM GATE (this module: post_llm_gate)
  6. EXECUTE the allowed tool -> read back confirmation -> LOG
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MONEY_WORDS = [
    "refund", "payment", "pay ", "card details", "credit card", "charge",
    "invoice", "account number", "password", "login", "bank",
]
LEGAL_WORDS = ["legal", "lawsuit", "sue", "compliance", "contract dispute"]
MEDICAL_WORDS = [
    "diagnos", "prescri", "medication", "should i take", "is it safe to",
    "symptom", "treatment for",
    # Symptom words (FIX #6, found by the bench battery Nov 7): a caller
    # describing a symptom is asking for medical advice, and the
    # receptionist must escalate, never advise. "I have chest pain, what
    # should I do?" matched NONE of the old words and reached the brain.
    # Stems, so "hurts"/"bleeding"/"dizzy" match too. "sleep"/"anxiety" are
    # deliberately NOT here — they also name products and support topics,
    # and the crisis gate is the right tool for the severe ones.
    "pain", "hurt", "ache", "bleed", "fever", "dizz", "chest", "breath",
    "sick", "injur", "wound", "rash", "swell", "nausea", "vomit",
    "headache", "blood", "dose", "dosage", "pill", "drug", "allerg",
    "asthma",
    # FIX #11 (guardrail gate fix, Nov 7): the suite's 12 medical
    # failures, same class as the Aria core's. "do i have" is
    # condition-anchored, never bare — a bare "do i have" would fire
    # on "Do I have an appointment tomorrow?" (a calendar question).
    # "remedies"/"remedy" are whole words, not the "remed" stem —
    # the stem would fire on "remedial massage", a real service.
    # "treats" is the word form ("which service treats anxiety?");
    # bare "cure" is deliberately NOT here (a product question like
    # "does this cure insomnia?" is not a medical-advice request).
    "do i have depression", "do i have anxiety", "do i have diabetes",
    "do i have schizophrenia", "do i have adhd", "do i have bipolar",
    "do i have ptsd", "do i have cancer", "do i have insomnia",
    "do i have asthma", "do i have dementia", "do i have flu",
    "do i have covid", "stop taking", "safe to take", "treats",
    "treatment plan", "remedies", "remedy", "can't breathe",
    "cant breathe", "overdose",
]
# FIX #10 (bench battery, Nov 7): the old pattern treated a bare "yes"
# — and a bare "hello" — as spam. "Yes" is the answer the agent's own
# confirmation question asks for ("Is that right?"), so the spam gate
# closed the call at the exact moment confirmation-by-repetition needs
# the caller to speak; it also made the "hello"/"hi" allow rule below
# unreachable (the spam check runs first). A bare "test"/"testing" is
# still a probe, not a request, and still closes the call.
SPAM_PATTERNS = [r"^(test|testing)\s*$"]

# Opt-out words (DISCLAIMER.md rule 5). A caller who says any of these
# on ANY call — inbound or call-back — is opting out of being called.
# "Say stop at any time" is the line the call-back greeting itself gives
# the caller, so bare "stop" counts. The gate fires before the brain,
# like every gate, and the call ends with the opt-out close.
OPT_OUT_PHRASES = [
    "stop calling", "don't call", "do not call", "never call",
    "remove me", "take me off", "opt out", "stop calling me",
    "don't call me", "do not call me", "never call me",
    "remove my number", "take my number off", "stop contacting",
]
OPT_OUT_REPLY = "Understood — I won't call you again. Goodbye."

# Multi-language auto-detection (DESIGN.md Appendix F, borrowable mechanic).
# Word-list heuristic only — it is a GATE INPUT, not an answer source.
# Detection never changes where answers come from: verified knowledge only.
# If the caller speaks another language, the agent notes it (flag) and
# answers in the config language unless the config carries verified
# knowledge in the caller's language. A production build swaps this for a
# proper detector; the contract (detect -> flag -> verified-only answers)
# stays the same.
_LANGUAGE_MARKERS = {
    "es": {"hola", "gracias", "por favor", "quiero", "cita", "necesito", "buenos"},
    "pt": {"olá", "obrigado", "obrigada", "por favor", "quero", "preciso", "consulta"},
    "fr": {"bonjour", "merci", "s'il vous plaît", "je veux", "rendez-vous"},
    "de": {"hallo", "danke", "bitte", "ich möchte", "termin"},
}


def detect_language(text: str, config_language: str = "en") -> str:
    """Return a BCP-47-ish language tag guess ('en', 'es', 'pt', 'fr', 'de').

    Defaults to the config language when nothing matches — detection is a
    hint, never a claim about the caller.
    """
    low = text.lower()
    base = (config_language or "en").split("-")[0].lower()
    best, best_hits = base, 0
    for lang, markers in _LANGUAGE_MARKERS.items():
        hits = sum(1 for m in markers if m in low)
        if hits > best_hits:
            best, best_hits = lang, hits
    return best


@dataclass
class GateResult:
    """One gate decision. ``action`` is what the agent must do next."""

    action: str  # allow | crisis | escalate | refuse | spam_close | message_take | opt_out
    reason: str
    reply: str = ""  # exact words to speak when the gate fires (from the config)
    escalate_to: str = "owner"


def _contains(text: str, words) -> str | None:
    low = text.lower()
    for word in words:
        if word in low:
            return word
    return None


# Crisis matching is stem-aware (FIX #5, found by the bench battery
# Nov 7 — same failure class as the guardrail suite's CR-05): exact
# substring matching missed "I feel like hurting myself" because the
# config keyword is "hurt myself" and "hurting" != "hurt". The map below
# normalises the inflections that matter for crisis language, on BOTH
# the caller text and the keyword phrases, before the substring check.
_CRISIS_INFLECTIONS = {
    "hurting": "hurt", "hurts": "hurt",
    "killing": "kill", "kills": "kill",
    "dying": "die", "dies": "die", "dead": "die",
    "ending": "end", "ends": "end",
}


def _normalize_crisis_text(text: str) -> str:
    # Curly apostrophes become straight ones too (FIX #11, Nov 7):
    # a phone caller dictating "I don't want to live anymore" can
    # arrive with U+2019, which would otherwise slip past "don't".
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    # Hyphens become spaces too, so "self-harm" and "self harm" match.
    words = text.lower().replace("-", " ").split()
    return " ".join(_CRISIS_INFLECTIONS.get(w.strip("?.!,"), w.strip("?.!,"))
                    for w in words)


def hard_gates(text: str, config: dict) -> GateResult:
    """Step 2 — hard gates. Runs before any LLM call."""
    # Curly apostrophes straightened (FIX #11, Nov 7) so the money /
    # legal / medical substring checks below see the same text the
    # crisis normalizer sees.
    low = text.lower().replace("\u2019", "'").replace("\u2018", "'").strip()
    crisis = (config.get("hardGates") or {}).get("crisis") or {}
    keywords = crisis.get("keywords") or []
    norm_low = _normalize_crisis_text(low)
    if any(_normalize_crisis_text(k) in norm_low for k in keywords):
        return GateResult(
            action="crisis",
            reason="crisis language matched",
            reply=crisis.get("response", ""),
        )

    # Opt-out (DISCLAIMER.md rule 5): runs right after the crisis gate —
    # a caller who says stop is opting out, whatever else they say.
    # Bare "stop" counts: it is the word the call-back greeting gives them.
    if low == "stop" or any(p in low for p in OPT_OUT_PHRASES):
        return GateResult(
            action="opt_out",
            reason="caller opted out of callbacks",
            reply=OPT_OUT_REPLY,
        )

    hit = _contains(low, MONEY_WORDS)
    if hit:
        return GateResult(
            action="escalate",
            reason=f"money/payment/account/credential request ({hit!r}) — never handle alone",
            reply=(
                "That's something the practice handles directly — let me take "
                "your details and they will call you back today."
            ),
        )

    hit = _contains(low, LEGAL_WORDS)
    if hit:
        return GateResult(
            action="escalate",
            reason=f"legal/compliance question ({hit!r}) — never answer",
            reply=(
                "I can't help with legal questions — let me take your details "
                "and the practice will call you back today."
            ),
        )

    hit = _contains(low, MEDICAL_WORDS)
    if hit:
        return GateResult(
            action="escalate",
            reason=f"medical advice request ({hit!r}) — never give medical advice",
            reply=(
                "I can't give medical advice — let me take your details and "
                "the practice will call you back today."
            ),
        )

    never = (config.get("hardGates") or {}).get("never") or []
    banned_phrases = {
        "take a payment": ["take my payment", "take my card", "charge my card"],
        "give medical, legal, or financial advice": MEDICAL_WORDS + LEGAL_WORDS,
    }
    for rule in never:
        for phrase in banned_phrases.get(rule, []):
            if phrase in low:
                return GateResult(
                    action="refuse",
                    reason=f"banned action in this role: {rule}",
                    reply=(
                        "I can't do that — let me take your details and the "
                        "practice will call you back today."
                    ),
                )

    for pattern in SPAM_PATTERNS:
        if re.match(pattern, low):
            return GateResult(
                action="spam_close",
                reason="no intelligible request (spam pattern)",
                reply="I can't help with that — goodbye.",
            )

    if low in {"", "hello", "hi"}:
        return GateResult(action="allow", reason="greeting only")

    return GateResult(action="allow", reason="no hard gate fired")


def deterministic_lookup(text: str, config: dict) -> str | None:
    """Step 3 — answer from verified knowledge, verbatim. No LLM.

    Matches the caller's words against the config's verified Q&A (and the
    business hours) by word overlap. Returns the verified answer, or None
    when nothing in verified knowledge answers the question. Never invents.
    """
    low = text.lower()
    stop = {"how", "much", "is", "a", "an", "the", "do", "you", "are", "what",
            "where", "when", "can", "i", "does", "it", "cost", "your"}
    # Hyphen-insensitive words: Groq Whisper transcribes "check-up" as
    # "checkup", so the verified Q&A must match either way. Without this
    # the deterministic lookup missed spoken price questions and the LLM
    # answered instead (still correct, but the verified-facts-first path
    # was bypassed). FIX #2, found by the bench voice driver, Nov 7.
    def _words(s: str) -> set[str]:
        return {w.strip("?.!,").replace("-", "") for w in s.split()} - stop

    words = _words(low)

    best: tuple[int, str] | None = None
    for item in config.get("knowledge") or []:
        q_words = _words(item.get("q", "").lower())
        overlap = len(words & q_words)
        if overlap and (best is None or overlap > best[0]):
            best = (overlap, item["a"])
    if best:
        return best[1]

    if "hour" in low or "open" in low or "close" in low:
        hours = (config.get("business") or {}).get("hours") or ""
        if hours:
            return hours
    if "where" in low and ("located" in low or "address" in low or "find you" in low):
        for item in config.get("knowledge") or []:
            if "where are you" in item.get("q", "").lower():
                return item["a"]
    return None


def post_llm_gate(proposed_tool: str | None, tool_args: dict, config: dict,
                  caller_confirmed: bool, booking_details: dict | None = None) -> GateResult:
    """Step 5 — check the LLM's proposed tool call before anything executes."""
    allowed = config.get("allowedTools") or []
    if proposed_tool is None:
        return GateResult(action="allow", reason="reply only, no tool call")

    if proposed_tool not in allowed:
        return GateResult(
            action="message_take",
            reason=f"proposed tool {proposed_tool!r} is not in allowedTools {allowed}",
            reply=(config.get("hardGates") or {}).get(
                "unknownAnswer",
                "Let me take your details and the practice will call you back today.",
            ),
        )

    if proposed_tool == "calendar.createEvent":
        missing = [
            k for k in ("caller_name", "service", "start", "end")
            if not (booking_details or {}).get(k)
        ]
        if missing:
            return GateResult(
                action="message_take",
                reason=f"booking details incomplete, missing: {missing}",
                reply=(
                    "Let me take your details and the practice will confirm "
                    "your time today."
                ),
            )
        if not caller_confirmed and (config.get("bookingRules") or {}).get(
            "requireCallerConfirmation", True
        ):
            return GateResult(
                action="message_take",
                reason="booking not yet confirmed by the caller (read-back required)",
                reply=(
                    "Just to confirm before I book it — please say the date, "
                    "time and service back to me."
                ),
            )
    return GateResult(action="allow", reason=f"tool {proposed_tool!r} allowed")
