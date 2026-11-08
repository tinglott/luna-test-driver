"""Groq brain call — SPACE version (env bearer key).

Same public API as the bench ``agent-service/brain.py`` (call_groq /
brain_reply / build_system_prompt + BrainUnavailable + the same
models and failover order). The difference is the transport: the
bench reaches Groq through the vault-routed connector helper, which
does not exist on a cloud host. This version posts directly to
Groq's OpenAI-compatible chat-completions endpoint with the key from
the GROQ_API_KEY environment variable — a Space secret Ting pastes
himself on the Space secrets page. The key is never printed, never
logged, never written to a file, and never leaves this process except
in the Authorization header to api.groq.com.

Failover (DESIGN.md section 6.3): if the primary model is
rate-limited or errors, try ``openai/gpt-oss-20b`` then
``qwen/qwen3-8b`` — each has its own free-tier cap. If every model
fails, raise BrainUnavailable and the caller must fall back to
deterministic lookups + message-taking. Never invent.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_HOST = "api.groq.com"

PRIMARY_MODEL = "openai/gpt-oss-120b"
FAILOVER_MODELS = ["openai/gpt-oss-20b", "qwen/qwen3-8b"]
ALL_MODELS = [PRIMARY_MODEL, *FAILOVER_MODELS]


class BrainUnavailable(Exception):
    """Raised when no Groq model answers. Caller falls back to message-taking."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused, verify the endpoint")


def _api_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise BrainUnavailable(
            "GROQ_API_KEY is not set on this Space — the brain cannot "
            "answer; the call falls back to message-taking")
    return key


def call_groq(messages: list[dict], model: str = PRIMARY_MODEL,
              temperature: float = 0.7, timeout: int = 60,
              tools: list[dict] | None = None,
              tool_choice: str | dict | None = None,
              response_format: dict | None = None) -> dict:
    """One chat completion via Groq directly. Returns the raw JSON.

    ``tools`` / ``tool_choice`` enable Groq native function calling
    (gpt-oss-120b returns ``tool_calls`` in the reply); ``response_format``
    enables structured output. The two must NEVER be combined in one
    request — gpt-oss refuses tools + json_schema together. Callers use
    the two-call pattern: tools call first, then a separate
    response_format call with no tools (see ``brain_reply_with_tools``).
    """
    body: dict = {"model": model, "messages": messages, "temperature": temperature}
    if tools is not None:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    if response_format is not None:
        body["response_format"] = response_format
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        API_URL, data=payload, method="POST",
        headers={
            "Authorization": "Bearer " + _api_key(),
            "Content-Type": "application/json",
            # Cloudflare in front of api.groq.com bans the default
            # Python-urllib signature (error 1010); a plain app UA passes.
            # Plain browser UA: Cloudflare 1010-blocks custom UAs on the bench egress proxy (Nov 7).
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        },
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise BrainUnavailable(
            f"Groq {model} refused (HTTP {exc.code})") from exc
    except Exception as exc:  # noqa: BLE001 - network errors are brain outages, never key leaks
        raise BrainUnavailable(f"Groq {model} unreachable: {exc}") from exc


def brain_reply(system_prompt: str, user_text: str, temperature: float = 0.7) -> tuple[str, str, dict]:
    """Ask the brain for a reply. Returns (reply_text, model_used, usage).

    Tries the primary model, then each failover model once. Raises
    BrainUnavailable only when every model fails.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
    errors: list[str] = []
    for model in ALL_MODELS:
        try:
            result = call_groq(messages, model=model, temperature=temperature)
            content = (result["choices"][0]["message"]["content"] or "").strip()
            if not content:
                # An empty completion is not a reply — try the next model.
                errors.append(f"{model}: empty completion")
                continue
            return content, model, result.get("usage") or {}
        except (BrainUnavailable, KeyError, IndexError) as exc:
            errors.append(f"{model}: {exc}")
    raise BrainUnavailable("all Groq models failed: " + " | ".join(errors))


def brain_reply_with_tools(system_prompt: str, user_text: str, tools: list[dict],
                           execute_tool, temperature: float = 0.7,
                           max_rounds: int = 3,
                           structured_schema: dict | None = None
                           ) -> tuple[str, str, dict, list[dict]]:
    """Ask the brain for a reply, letting it call real tools.

    The tool loop (the ARIA Nexus fill for gap 4 — the pastes only
    *guessed* tool use from keywords):
      1. Call the model WITH ``tools``. If the reply has no ``tool_calls``,
         it is the final reply — same path as ``brain_reply``.
      2. Each ``tool_call`` is executed through ``execute_tool(name, args)``
         — the executor enforces the role config's allow-list, so a tool
         the model invents or the config does not allow is refused, never
         run. The tool result goes back to the model as a ``role: "tool"``
         message, and the model is asked again (up to ``max_rounds``).
      3. Two-call pattern for structured output: gpt-oss refuses
         ``tools`` + ``response_format: json_schema`` in one request. When
         ``structured_schema`` is given, the tools loop runs first with NO
         response_format, then ONE final call runs with the schema and NO
         tools. The schema call only shapes the reply; it never re-runs tools.

    Failover safety: if a model fails mid-loop, the next model restarts
    the loop — but a tool call already executed is NEVER re-executed:
    ``_executed`` is keyed by (tool, arguments) and replays the stored
    result. A booking/email/task therefore cannot fire twice because a
    model timed out.

    Returns (reply_text, model_used, usage, executed) where ``executed``
    is a list of {tool, arguments, result, status}. Raises
    BrainUnavailable only when every model fails before any reply.
    """
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
    executed: list[dict] = []
    done: dict[tuple[str, str], dict] = {}

    def _execute_one(call: dict) -> dict:
        """Execute one tool call with dedupe + bad-JSON handling. Returns
        the (tool, arguments, result) record for the executed list."""
        fn = (call.get("function") or {})
        name = fn.get("name") or ""
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args)
            if not isinstance(args, dict):
                raise ValueError("arguments are not a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            tool_result: dict = {
                "ok": False, "status": "bad_arguments",
                "error": f"arguments were not valid JSON: {exc}",
            }
            args = {}
        else:
            key = (name, json.dumps(args, sort_keys=True))
            if key in done:
                tool_result = done[key]
            else:
                tool_result = execute_tool(name, args)
                done[key] = tool_result
        return {"tool": name, "arguments": args,
                "result": tool_result,
                "status": tool_result.get("status", ""),
                "tool_call_id": call.get("id") or ""}

    def _run_loop(model: str) -> tuple[str, dict]:
        convo = list(messages)
        usage: dict = {}
        for _round in range(max_rounds):
            result = call_groq(convo, model=model, temperature=temperature,
                               tools=tools)
            usage = result.get("usage") or {}
            message = (result.get("choices") or [{}])[0].get("message") or {}
            tool_calls = message.get("tool_calls") or []
            content = (message.get("content") or "").strip()
            if not tool_calls:
                return content, usage
            convo.append(message)
            for call in tool_calls:
                record = _execute_one(call)
                executed.append({k: v for k, v in record.items()
                                 if k != "tool_call_id"})
                convo.append({
                    "role": "tool",
                    "tool_call_id": record["tool_call_id"],
                    "content": json.dumps(record["result"]),
                })
        # Rounds exhausted: ask once more for a plain reply, no tools.
        result = call_groq(convo, model=model, temperature=temperature)
        message = (result.get("choices") or [{}])[0].get("message") or {}
        return (message.get("content") or "").strip(), result.get("usage") or {}

    errors: list[str] = []
    for model in ALL_MODELS:
        try:
            reply, usage = _run_loop(model)
            if not reply:
                errors.append(f"{model}: empty completion")
                continue
            if structured_schema is not None:
                # Two-call pattern: structured JSON in a SEPARATE call with
                # no tools — gpt-oss refuses tools + json_schema together.
                structured = call_groq(
                    messages + [{"role": "assistant", "content": reply}],
                    model=model, temperature=temperature,
                    response_format={
                        "type": "json_schema",
                        "json_schema": structured_schema,
                    },
                )
                content = ((structured.get("choices") or [{}])[0]
                           .get("message", {}).get("content") or "").strip()
                if content:
                    reply = content
                    usage = structured.get("usage") or usage
            return reply, model, usage, executed
        except (BrainUnavailable, KeyError, IndexError) as exc:
            errors.append(f"{model}: {exc}")
    raise BrainUnavailable("all Groq models failed: " + " | ".join(errors))


def build_system_prompt(config: dict) -> str:
    """Constrain the brain to the role config: tone, greeting style,
    allowed tools, and verified knowledge only. The brain proposes words
    and tool calls; it never executes anything and never invents facts."""
    business = config.get("business") or {}
    knowledge = config.get("knowledge") or []
    facts = "\n".join(f"- {item['q']} -> {item['a']}" for item in knowledge)
    never = "\n".join(f"- {rule}" for rule in (config.get("hardGates") or {}).get("never") or [])
    return (
        f"You are the AI receptionist for {business.get('name', 'the business')}. "
        f"Tone: {config.get('tone', '')}\n"
        f"Greeting style: {config.get('greeting', '')}\n"
        f"Allowed tools only: {', '.join(config.get('allowedTools') or [])}. "
        "You may only propose a tool call from that list.\n"
        "Never write out a tool call as words \u2014 no 'Action:' lines, no "
        "backticks, no function names in your reply. If a booking needs "
        "checking or making, ask the caller to confirm the details in "
        "plain words.\n"
        "Verified facts — use these and nothing else for facts, prices, hours:\n"
        f"{facts or '(none listed)'}\n"
        "You must NEVER do any of these:\n"
        f"{never or '(none listed)'}\n"
        "If you do not know the answer from the verified facts, say so and "
        "offer to take the caller's details for a callback. "
        "Keep replies short — one or two sentences, spoken aloud. "
        "Never invent a booking, a price, or a callback time."
    )
