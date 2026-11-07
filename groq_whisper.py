#!/usr/bin/env python3
"""Groq Whisper speech-to-text — SPACE version (env bearer key).

Same CLI as the bench ``voice-driver/groq_whisper.py`` (audio file,
optional language; prints {"text": ..., "model": ...} as JSON).
The bench version reaches Groq through the vault-routed connector
helper, which does not exist on a cloud host. This version posts
directly to Groq's OpenAI-compatible audio-transcriptions endpoint
with the key from the GROQ_API_KEY environment variable — a Space
secret Ting pastes himself on the Space secrets page. The key is
never printed, never logged, never written to a file, and never
leaves this process except in the Authorization header to
api.groq.com. Model: whisper-large-v3-turbo (Groq free tier).
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid

API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3-turbo"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused, verify the endpoint")


def _multipart_body(path: str, model: str, language: str | None) -> tuple[bytes, str]:
    boundary = "----artisy-whisper-" + uuid.uuid4().hex
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )

    field("model", model)
    field("response_format", "json")
    if language:
        field("language", language)
    filename = os.path.basename(path)
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        data = fh.read()
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n".encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def transcribe_file(path: str, language: str = "en") -> dict:
    """Transcribe an audio file in-process (Vercel: no subprocess, no
    CLI round-trip). Returns {"text", "model"} or {"error"}. The key is
    never printed or logged."""
    if not os.path.exists(path):
        return {"error": f"no such file: {path}"}
    body, boundary = _multipart_body(path, MODEL, language)
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return {"error": "GROQ_API_KEY is not set"}
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            # Cloudflare in front of api.groq.com bans the default
            # Python-urllib signature (error 1010); a plain app UA passes.
            "User-Agent": "Mozilla/5.0 (compatible; ARTISY-FreeStack/1.0)",
        },
        method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - the caller reports the failure, never the key
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"text": result.get("text", ""), "model": MODEL}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: groq_whisper.py <audio-file> [language]", file=sys.stderr)
        return 2
    audio_path = sys.argv[1]
    language = sys.argv[2] if len(sys.argv) > 2 else "en"
    result = transcribe_file(audio_path, language)
    if "error" in result:
        print(json.dumps({"error": result["error"]}))
        return 1
    print(json.dumps({"text": result.get("text", ""), "model": MODEL}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
