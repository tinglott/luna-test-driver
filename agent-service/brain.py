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
              temperature: float = 0.7, timeout: int = 60) -> dict:
    """One chat completion via Groq directly. Returns the raw JSON."""
    body = json.dumps({
        "model": model, "messages": messages, "temperature": temperature,
    }).encode("utf-8")
    request = urllib.request.Request(
        API_URL, data=body, method="POST",
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
        "Verified facts — use these and nothing else for facts, prices, hours:\n"
        f"{facts or '(none listed)'}\n"
        "You must NEVER do any of these:\n"
        f"{never or '(none listed)'}\n"
        "If you do not know the answer from the verified facts, say so and "
        "offer to take the caller's details for a callback. "
        "Keep replies short — one or two sentences, spoken aloud. "
        "Never invent a booking, a price, or a callback time."
    )
