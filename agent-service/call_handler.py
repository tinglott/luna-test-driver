"""Call handler — the 6-step per-turn pipeline, DESIGN.md section 9.4.

Fixed order (do not reorder):
  1. LOAD config -> validate (fail closed to message-taking if invalid)
  2. HARD GATES (deterministic, no LLM)
  3. DETERMINISTIC LOOKUPS (hours, prices, services from verified knowledge)
  4. LLM (Groq brain) — only for phrasing / questions gates + lookups did not resolve
  5. POST-LLM GATE (tool call allowed? booking details complete? caller confirmed?)
  6. EXECUTE the allowed tool -> read back confirmation -> LOG (call-end payload)

The call-end payload is what n8n workflow W1 sends to Supabase
``activity_log``. Its columns follow DESIGN.md Appendix B (which adds
``client_id``, ``role_id``, ``call_id``, ``outcome``, ``booking_event_id``,
``duration_seconds`` and ``flags`` to the live table — see
``supabase/migrations/001-activity-log-call-columns.sql``). The remaining
detail rides in the ``detail`` jsonb column, which the live schema already has.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
import uuid
from dataclasses import dataclass, field

from brain import BrainUnavailable, brain_reply, build_system_prompt
from callback_queue import add_to_dnc_list
from calendar_booking import CalendarError, book_appointment
from gates import GateResult, detect_language, deterministic_lookup, hard_gates, post_llm_gate
from role_loader import load_role_config_or_fallback
import sales_lead


@dataclass
class TurnResult:
    reply: str
    action: str
    flags: list[str] = field(default_factory=list)


@dataclass
class CallSession:
    client_id: str
    role_id: str
    call_id: str = field(default_factory=lambda: "call-" + uuid.uuid4().hex[:12])
    caller_phone: str = ""
    caller_name: str = ""
    base_dir: str = "."
    transcript: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    outcome: str = "message"  # booked | message | spam | failed | pending_approval
    booking_event_id: str | None = None
    detected_language: str = ""
    caller_is_new: bool = False  # True for a first-time caller (approval queue)
    approval_queue_file: str = "/tmp/approval_queue.jsonl"
    # Do-not-call list file (prototype stand-in for the Supabase
    # do_not_call_list table, migration 003). When set, an opt-out on
    # this call writes the caller's number to it before anything else.
    # When None, the opt-out is flagged dnc_write_pending — recorded,
    # never silently dropped.
    dnc_file: str | None = None
    started_at: float = field(default_factory=time.time)
    config_valid: bool = True
    config: dict = field(default_factory=dict)

    def __post_init__(self):
        self.config, self.config_valid, problems = load_role_config_or_fallback(
            self.base_dir, self.client_id, self.role_id
        )
        if not self.config_valid:
            self.flags.append("role_config_invalid")
            self.flags.append("fallback_message_taking_only")

    # -- one caller turn -------------------------------------------------
    def handle_turn(self, caller_text: str) -> TurnResult:
        self.transcript.append({"speaker": "caller", "text": caller_text})

        # Language detection is a gate input only: it flags a mismatch for
        # Ting's review. Answers still come only from verified knowledge.
        detected = detect_language(caller_text, self.config.get("language", "en"))
        config_lang = (self.config.get("language") or "en").split("-")[0].lower()
        self.detected_language = detected
        if detected != config_lang:
            self.flags.append(f"language_mismatch:caller={detected},config={config_lang}")

        # Step 2 — hard gates, before any LLM call.
        gate = hard_gates(caller_text, self.config)
        if gate.action == "crisis":
            self.flags.append("crisis_routing")
            return self._reply(gate.reply, "escalate_crisis")
        if gate.action == "opt_out":
            # DISCLAIMER.md rule 5: the caller said stop. End the call
            # with the opt-out close, write the number to the client's
            # do-not-call list, and log it — before anything else.
            self.flags.append("opt_out:caller_asked_not_to_be_called")
            if self.dnc_file and self.caller_phone:
                try:
                    add_to_dnc_list(
                        self.dnc_file, client_id=self.client_id,
                        caller_phone=self.caller_phone, source="opt_out_on_call",
                        source_call_id=self.call_id,
                    )
                    self.flags.append("dnc_added")
                except Exception as exc:  # noqa: BLE001 - a failed DNC write must be visible, not silent
                    self.flags.append(f"dnc_write_failed:{exc}")
            elif not self.dnc_file:
                self.flags.append("dnc_write_pending:no_dnc_file")
            return self._reply(gate.reply, "opt_out")
        if gate.action in ("escalate", "refuse"):
            self.flags.append(f"gate_{gate.action}:{gate.reason}")
            return self._reply(gate.reply, "escalate")
        if gate.action == "spam_close":
            self.outcome = "spam"
            self.flags.append("spam_close")
            return self._reply(gate.reply, "spam_close")

        # Fail closed (DESIGN.md section 9.2 step 3): an invalid config never
        # reaches the LLM. Message-taking only — the crisis gate above still
        # ran first, so a caller in danger is never ignored.
        if not self.config_valid:
            return self._reply(
                (self.config.get("hardGates") or {}).get(
                    "unknownAnswer",
                    "Let me take your details and the practice will call you back today.",
                ),
                "message_take",
            )

        # Step 3 — deterministic lookup from verified knowledge.
        # Booking intent takes precedence over a price lookup: "I'd like to
        # book a check-up" must reach the booking flow, not come back as
        # "A check-up is $45." (word overlap alone would match the price Q&A.)
        wants_booking = re.search(
            r"\b(book|booking|schedule|appointment)\b", caller_text.lower()
        ) and not re.search(
            r"\b(how much|price|cost|how expensive)\b", caller_text.lower()
        )
        answer = None if wants_booking else deterministic_lookup(caller_text, self.config)
        if answer is not None:
            return self._reply(answer, "lookup")

        # Step 4 — LLM, constrained by the config. Brain failure is not fatal.
        try:
            reply, model, _usage = brain_reply(
                build_system_prompt(self.config), caller_text
            )
        except BrainUnavailable:
            self.flags.append("brain_unavailable_message_taking")
            # Distinct wording from the calendar fallback: the diary may be
            # fine — it is the BRAIN that is unreachable. Say so honestly.
            return self._reply(
                "Sorry — I'm having trouble just now. Let me take your "
                "details and the practice will call you back today.",
                "message_take",
            )

        # Step 5 — post-LLM gate on any tool the brain proposed.
        tool = self._proposed_tool(reply)
        gate2 = post_llm_gate(tool, {}, self.config, caller_confirmed=False)
        if gate2.action != "allow":
            self.flags.append(f"post_llm_gate:{gate2.reason}")
            return self._reply(gate2.reply, "message_take")
        return self._reply(reply, "brain")

    def _proposed_tool(self, reply: str) -> str | None:
        """The prototype brain replies in words, not tool calls. A reply that
        asserts a booking is DONE is treated as a proposed
        calendar.createEvent, which the post-LLM gate then checks (details
        complete? caller confirmed?). A reply that only asks the caller to
        confirm a time is not a tool call — confirmation questions are how
        the agent earns the right to book. A production Pipecat agent parses
        real tool calls here instead."""
        if re.search(
            r"\b(you'?re booked|i'?ve booked|booked you|confirmed your booking|"
            r"booking is confirmed)\b",
            reply.lower(),
        ):
            return "calendar.createEvent"
        return None

    def _reply(self, text: str, action: str) -> TurnResult:
        self.transcript.append({"speaker": "agent", "text": text})
        return TurnResult(reply=text, action=action)

    # -- booking (step 6) -------------------------------------------------
    def book(self, service: str, start: str, end: str) -> TurnResult:
        """Book only after the caller confirmed date/time/service. The
        caller confirms in the simulation by calling confirm_booking first."""
        if not self._confirmed:
            return self._reply(
                f"Just to confirm before I book it — {service} on {start}. Is that right?",
                "confirm",
            )
        from datetime import datetime

        readable = datetime.fromisoformat(start).strftime("%A %-d %B %Y at %-I:%M %p")
        rules = self.config.get("bookingRules") or {}
        calendar_id = rules.get("calendarId", "primary")
        buffer_minutes = rules.get("bufferMinutes", 0)

        # Owner-approval queue (Appendix H): a first-time caller is booked
        # provisionally only. No calendar write here — the owner approves
        # from the queue, and approval performs the real calendar write.
        if rules.get("newClientBookingsRequireApproval") and self.caller_is_new:
            from approval_queue import request_approval

            item = request_approval(
                self.approval_queue_file, client_id=self.client_id,
                call_id=self.call_id, caller_name=self.caller_name or "not given",
                caller_phone=self.caller_phone or "not given", service=service,
                start=start, end=end, calendar_id=calendar_id,
            )
            self.outcome = "pending_approval"
            self.flags.append("booking_pending_approval")
            return self._reply(
                f"I've put in a request for {service} on {readable}. "
                "The practice will confirm it with you today.",
                "pending_approval",
            )

        business = (self.config.get("business") or {}).get("name", "the practice")
        summary = f"TEST — AI Receptionist prototype booking: {service} ({business})"
        description = (
            f"Prototype test booking.\nCaller: {self.caller_name or 'not given'} "
            f"({self.caller_phone or 'not given'})\nService: {service}\n"
            f"Call ID: {self.call_id}\nClient: {self.client_id} / {self.role_id}"
        )
        try:
            event = book_appointment(calendar_id, summary, start, end, description,
                                     buffer_minutes=buffer_minutes)
        except CalendarError as exc:
            self.flags.append(f"booking_failed_calendar:{exc}")
            fallback = (self.config.get("hardGates") or {}).get("fallback", {}).get(
                "messageTaking",
                "Let me take your details and the practice will confirm your time today.",
            )
            return self._reply(fallback, "message_take")
        self.booking_event_id = event["id"]
        self.outcome = "booked"
        reply = (
            f"You're booked for {service} on {readable}. "
            f"{business} will see you then — is there anything else I can help with?"
        )
        return self._reply(reply, "booked")

    _confirmed: bool = False

    def confirm_booking(self) -> None:
        self._confirmed = True

    # -- Serialization (no shared process memory) ---------------------------
    # The bench keeps CallSession objects in process memory. A hosted
    # function cannot: each request may land on a fresh instance. The
    # session is therefore serialized to the KV store between turns and
    # restored here. `config` is NOT serialized — it is reloaded from
    # the role configs on restore (configs are static; reloading also
    # re-validates them, so a restored session never trusts a stale
    # config). Everything that changes during a call is serialized:
    # transcript, flags, outcome, booking_event_id, detected_language,
    # caller_is_new, started_at, config_valid and _confirmed.
    def to_state(self) -> dict:
        return {
            "client_id": self.client_id,
            "role_id": self.role_id,
            "call_id": self.call_id,
            "caller_phone": self.caller_phone,
            "caller_name": self.caller_name,
            "base_dir": self.base_dir,
            "transcript": self.transcript,
            "flags": self.flags,
            "outcome": self.outcome,
            "booking_event_id": self.booking_event_id,
            "detected_language": self.detected_language,
            "caller_is_new": self.caller_is_new,
            "approval_queue_file": self.approval_queue_file,
            "dnc_file": self.dnc_file,
            "started_at": self.started_at,
            "config_valid": self.config_valid,
            "_confirmed": self._confirmed,
        }

    @classmethod
    def from_state(cls, state: dict) -> "CallSession":
        session = cls(
            client_id=state["client_id"],
            role_id=state["role_id"],
            call_id=state["call_id"],
            caller_phone=state.get("caller_phone") or "",
            caller_name=state.get("caller_name") or "",
            base_dir=state.get("base_dir") or ".",
            transcript=list(state.get("transcript") or []),
            flags=list(state.get("flags") or []),
            outcome=state.get("outcome") or "message",
            booking_event_id=state.get("booking_event_id"),
            detected_language=state.get("detected_language") or "",
            caller_is_new=bool(state.get("caller_is_new")),
            approval_queue_file=state.get("approval_queue_file") or "",
            dnc_file=state.get("dnc_file"),
            started_at=float(state.get("started_at") or time.time()),
        )
        # The constructor reloaded the config; restore the recorded
        # validity (same inputs give the same result, but keep the
        # recorded value as the source of truth for this call).
        session.config_valid = bool(state.get("config_valid", True))
        session._confirmed = bool(state.get("_confirmed"))
        return session

    # -- sales door (DESIGN.md section 10) --------------------------------
    def sales_qualification(self) -> dict:
        """Deterministic sales-lead qualification for this call (see
        sales_lead.qualify_lead). Only sales roles can qualify; a
        front-desk price question never qualifies."""
        caller_texts = [t["text"] for t in self.transcript
                        if t.get("speaker") == "caller"]
        return sales_lead.qualify_lead(
            self.role_id, self.caller_name, self.caller_phone,
            caller_texts, self.flags)

    def sales_follow_up_due(self) -> str | None:
        """Due date for the follow-up task when this call qualified as a
        sales lead. None when it did not qualify or the config carries no
        followUp block (then a flag records that no schedule exists)."""
        from datetime import datetime, timezone

        sq = self.sales_qualification()
        if not sq["sales_qualified"]:
            return None
        follow_up = self.config.get("followUp") or {}
        if not follow_up.get("dueNextBusinessDay"):
            if "sales_followup_no_schedule" not in self.flags:
                self.flags.append("sales_followup_no_schedule")
            return None
        return sales_lead.follow_up_due_iso(
            datetime.now(timezone.utc),
            follow_up.get("dueTime", "10:00"),
            follow_up.get("timezone", "Europe/London"),
        )

    # -- call-end handoff --------------------------------------------------
    @property
    def duration_seconds(self) -> int:
        return int(time.time() - self.started_at)

    def call_end_payload(self) -> dict:
        """Exact payload W1 posts to Supabase activity_log (PostgREST).

        Column names/types follow DESIGN.md Appendix B + the migrations in
        supabase/migrations/001-activity-log-call-columns.sql (call columns)
        and 002-callback-and-sales-columns.sql (callback outcomes,
        sales_qualified, follow_up_due). ``detail`` carries the rest
        (caller, intent, transcript) as jsonb.
        """
        return {
            "employee_id": "aria",
            "action": f"call_{self.outcome}",
            "client_id": self.client_id,
            "role_id": self.role_id,
            "call_id": self.call_id,
            "outcome": self.outcome,
            "booking_event_id": self.booking_event_id,
            "duration_seconds": self.duration_seconds,
            "flags": self.flags,
            "escalated": any(f.startswith("gate_escalate") or f.startswith("crisis") for f in self.flags),
            "error_caught": any("invalid" in f or "failed" in f for f in self.flags),
            "sales_qualified": self.sales_qualification()["sales_qualified"],
            "follow_up_due": self.sales_follow_up_due(),
            "detail": {
                "caller_name": self.caller_name,
                "caller_phone": self.caller_phone,
                "detected_language": self.detected_language or None,
                "intent": "book_appointment" if self.outcome == "booked" else "message",
                "booking_or_message": (
                    f"booked, event {self.booking_event_id}"
                    if self.booking_event_id
                    else "message taken"
                ),
                "transcript": self.transcript,
            },
        }

    def post_call_end(self, endpoint_url: str, timeout: int = 15) -> tuple[int, str]:
        """POST the call-end payload to the W1 / Supabase endpoint.

        In production the voice agent POSTs to the n8n W1 webhook, which
        writes Supabase. In this prototype the test posts to a local stub
        that captures the exact POST W1 would send. Returns (status, body).
        """
        data = json.dumps(self.call_end_payload()).encode("utf-8")
        req = urllib.request.Request(
            endpoint_url, data=data,
            headers={"Content-Type": "application/json", "Prefer": "return=representation"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
