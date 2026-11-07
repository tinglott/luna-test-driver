"""Groq text-to-speech — Canopy Orpheus (Vercel build).

Replaces the Kokoro sidecar. The bench driver synthesizes with a local
Kokoro worker; a Vercel function cannot run torch, so speech comes from
Groq's ``/audio/speech`` endpoint instead. **The voice CHANGES: Kokoro
``af_heart`` is gone in this build.** The test voice is Orpheus
``hannah`` (female) — a different voice from the production one, so
this test validates the call flow, the gates and the booking, NOT the
real voice. Say that plainly wherever this build is reported.

Facts (Groq docs, read Nov 7, 2026 — console.groq.com/docs/
text-to-speech/orpheus, /text-to-speech, /rate-limits, /models):
  * Endpoint: POST https://api.groq.com/openai/v1/audio/speech
    (OpenAI-compatible). Body: {"model", "input", "voice",
    "response_format": "wav"}. Key from GROQ_API_KEY env (Ting pastes
    it in the Vercel dashboard himself).
  * Model: ``canopylabs/orpheus-v1-english``. English voices: autumn,
    diana, hannah (female); austin, daniel, troy (male).
  * **200 characters max per request** — long text is sentence-chunked
    here, client-side. **WAV is the only supported format** for
    Orpheus (no MP3). The response body is raw binary WAV, not JSON.
  * Free-tier limits: 10 RPM / 100 RPD / 1.2K TPM / 3.6K TPD (the
    rate-limits table as rendered; Groq's own prose is ambiguous about
    which tier the table shows — the models page lists Developer
    Orpheus limits as 250 RPM / 50K TPM. Exact per-org limits live at
    console.groq.com/settings/limits). Over-limit returns 429 — a
    chunk that 429s is retried with backoff, then the audio is dropped
    (the TEXT reply still goes out; a TTS failure never kills a call).
  * **Preview model**: Groq's models page puts Orpheus under Preview —
    "intended for evaluation purposes only… may be discontinued at
    short notice". If Groq retires it, this module's model id is the
    one line to change.
  * Sample rate / channel count are NOT documented by Groq. The code
    parses the WAV header of the first chunk at runtime (``wave``)
    and stitches PCM from all chunks under that same format — it never
    hard-codes a sample rate. Naive byte-concatenation of whole WAV
    files would produce a corrupt file (each chunk carries its own
    header), so headers are stripped and only PCM is joined.
  * Vercel response-size guard: a function response is capped at
    4.5 MB. Audio is returned as a base64 data URI inside the turn
    JSON, so a very long reply could breach it. Synthesis is capped
    at MAX_CHUNKS chunks (800 chars ≈ 60–80 s of speech); the TEXT
    reply is unaffected — only the audio is capped. The test driver's
    replies are short (greetings, lookups, confirmations), so the cap
    is a safety rail, not a normal path.
"""

from __future__ import annotations

import base64
import io
import os
import re
import time
import wave

import requests

API_URL = "https://api.groq.com/openai/v1/audio/speech"
MODEL = "canopylabs/orpheus-v1-english"
VOICE = "hannah"  # female Orpheus voice; NOT Kokoro af_heart (see header)
MAX_CHARS_PER_REQUEST = 200
MAX_CHUNKS = 4  # response-size guard: 4 x 200 chars of audio per turn
MAX_RETRIES_429 = 3
RETRY_BACKOFF_SECONDS = 6  # ~10 RPM free tier: one request per 6 seconds


def _api_key() -> str:
    return os.environ.get("GROQ_API_KEY", "").strip()


def _chunk_text(text: str) -> list[str]:
    """Split into sentence chunks of at most 200 characters (Groq's
    per-request cap). Sentences are never split mid-word; a single
    sentence longer than 200 chars is hard-split at 200."""
    sentences = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        while len(sentence) > MAX_CHARS_PER_REQUEST:
            chunks.append(sentence[:MAX_CHARS_PER_REQUEST])
            sentence = sentence[MAX_CHARS_PER_REQUEST:].strip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > MAX_CHARS_PER_REQUEST:
            chunks.append(current)
            current = sentence
        else:
            current = (current + " " + sentence).strip() if current else sentence
    if current:
        chunks.append(current)
    return [c for c in chunks if c]


def _synthesize_chunk(text: str, api_key: str) -> bytes | None:
    """One Groq TTS request. Returns raw WAV bytes, or None on failure
    (429s are retried with backoff; anything else fails fast — the
    caller drops the audio, never the turn)."""
    for attempt in range(MAX_RETRIES_429 + 1):
        try:
            resp = requests.post(
                API_URL,
                json={"model": MODEL, "input": text, "voice": VOICE,
                      "response_format": "wav"},
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                    # Cloudflare in front of api.groq.com bans the
                    # default Python-requests signature (error 1010);
                    # a plain app UA passes (same as brain.py).
                    "User-Agent": "Mozilla/5.0 (compatible; ARTISY-FreeStack/1.0)",
                },
                timeout=60,
            )
        except Exception:  # noqa: BLE001 - network failure is a TTS failure
            return None
        if resp.status_code == 429 and attempt < MAX_RETRIES_429:
            time.sleep(RETRY_BACKOFF_SECONDS)
            continue
        if resp.status_code != 200 or not resp.content:
            return None
        return resp.content
    return None


def _stitch_wavs(chunks: list[bytes]) -> bytes | None:
    """Join standalone WAV chunks into one WAV: parse each chunk's
    header with ``wave``, concatenate only the PCM payloads under the
    first chunk's format, and write one header. Returns None when a
    chunk is not a readable WAV or the formats disagree."""
    try:
        frames: list[bytes] = []
        params = None
        for raw in chunks:
            with wave.open(io.BytesIO(raw), "rb") as wav:
                if params is None:
                    params = wav.getparams()
                elif (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
                      ) != (params.nchannels, params.sampwidth, params.framerate):
                    return None
                frames.append(wav.readframes(wav.getnframes()))
        if params is None:
            return None
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(params.nchannels)
            wav.setsampwidth(params.sampwidth)
            wav.setframerate(params.framerate)
            wav.writeframes(b"".join(frames))
        return out.getvalue()
    except Exception:  # noqa: BLE001 - a corrupt chunk drops the audio, not the call
        return None


def synthesize(text: str) -> bytes | None:
    """Synthesize `text` -> stitched WAV bytes, or None when TTS is
    unavailable (no key, Groq error, bad audio). Never raises."""
    api_key = _api_key()
    if not api_key:
        return None
    chunks = _chunk_text(text)[:MAX_CHUNKS]
    if not chunks:
        return None
    wavs: list[bytes] = []
    for chunk in chunks:
        data = _synthesize_chunk(chunk, api_key)
        if data is None:
            return None
        wavs.append(data)
    return _stitch_wavs(wavs)


def synthesize_data_uri(text: str) -> str | None:
    """Synthesize `text` -> ``data:audio/wav;base64,...`` URI for the
    turn JSON (the browser plays it from memory; there is no
    /audio/<name> route on this build), or None."""
    audio = synthesize(text)
    if audio is None:
        return None
    return "data:audio/wav;base64," + base64.b64encode(audio).decode("ascii")
