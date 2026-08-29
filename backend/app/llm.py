# backend/app/llm.py
"""
Gemini wrappers for Study BuddAI.

One module handles everything the app asks of an LLM:
  - transcribe_audio: speech-to-text for a recorded lecture
  - generate_json:    a chat call constrained to return valid JSON
  - summarize_text:   a lecture summary as {"text": ..., "bullets": [...]}

All calls use google-genai and a single model (GEMINI_MODEL, default
gemini-3.6-flash), which handles both audio and text.
"""
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from google import genai
from google.genai import types
from google.genai.errors import ServerError

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# Gemini occasionally returns 503 (high demand); retry a few times with backoff.
_MAX_RETRIES = 4

_client_instance: Optional[genai.Client] = None


def _client() -> genai.Client:
    """Create the Gemini client lazily so a missing key fails loudly, not at import."""
    global _client_instance
    if _client_instance is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to backend/.env "
                "(see backend/.env.example)."
            )
        _client_instance = genai.Client(api_key=api_key)
    return _client_instance


def _generate(contents: Any, config: Optional[types.GenerateContentConfig] = None):
    """generate_content with retry on transient 503s."""
    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            return _client().models.generate_content(
                model=MODEL, contents=contents, config=config
            )
        except ServerError as exc:
            last_exc = exc
            time.sleep(2 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


# -----------------------------
# Transcription
# -----------------------------
_AUDIO_MIME = {
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
}


def transcribe_audio(filepath: str) -> str:
    """Transcribe a recorded lecture with Gemini. Returns the transcript text."""
    mime_type = _AUDIO_MIME.get(Path(filepath).suffix.lower(), "audio/webm")

    uploaded = _client().files.upload(
        file=filepath, config=types.UploadFileConfig(mime_type=mime_type)
    )

    # Wait for the upload to finish processing before referencing it.
    deadline = time.monotonic() + 120
    while str(getattr(uploaded.state, "name", uploaded.state)) == "PROCESSING":
        if time.monotonic() > deadline:
            raise TimeoutError("Gemini took too long to process the audio upload.")
        time.sleep(1)
        uploaded = _client().files.get(name=uploaded.name)

    if str(getattr(uploaded.state, "name", uploaded.state)) == "FAILED":
        raise RuntimeError("Gemini failed to process the audio upload.")

    try:
        resp = _generate(
            [
                "Transcribe this lecture audio verbatim. "
                "Output only the transcript text, with no commentary.",
                uploaded,
            ]
        )
    finally:
        try:
            _client().files.delete(name=uploaded.name)
        except Exception:
            pass

    return (resp.text or "").strip()


def transcribe_audio_bytes(data: bytes, mime_type: str = "audio/webm") -> str:
    """
    Transcribe a short in-memory audio chunk (a few seconds) inline, without the
    Files API upload/poll/delete cycle. Used for the live, per-chunk transcript.
    """
    resp = _generate(
        [
            "Transcribe this audio verbatim. Output only the transcript text, with "
            "no commentary. If there is no intelligible speech, output nothing.",
            types.Part.from_bytes(data=data, mime_type=mime_type),
        ]
    )
    return (resp.text or "").strip()


# -----------------------------
# JSON chat calls
# -----------------------------
def _loads_lenient(text: str) -> Any:
    """json.loads, with one fallback that digs a JSON value out of prose/fences."""
    try:
        return json.loads(text)
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    candidate = fenced.group(1) if fenced else text
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", candidate)
    if not match:
        raise ValueError("model response did not contain JSON")
    return json.loads(match.group(1))


def generate_json(
    prompt: str,
    *,
    system: str,
    temperature: float = 0.0,
    max_output_tokens: int = 2048,
) -> Any:
    """
    Run a single-turn Gemini call that must return JSON.

    response_mime_type="application/json" makes Gemini emit parseable JSON, so the
    caller gets a real list/dict instead of scraping text.
    """
    resp = _generate(
        prompt,
        types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
        ),
    )
    return _loads_lenient((resp.text or "").strip())


# -----------------------------
# Summary
# -----------------------------
def summarize_text(transcript_text: str) -> dict:
    """
    Summarize a lecture transcript.

    Returns {"text": "<3-5 sentences>", "bullets": ["...", ...]}.
    """
    if not transcript_text or not transcript_text.strip():
        return {"text": "", "bullets": []}

    prompt = (
        "Summarize the following lecture transcript. Return a JSON object with:\n"
        '  - "text": a concise 3-5 sentence summary\n'
        '  - "bullets": an array of 4-8 short bullet points (3-12 words each) '
        "covering the main points\n\n"
        "Transcript:\n\n"
        f"{transcript_text}"
    )
    system = "You are a precise summarization assistant. Output valid JSON only."

    try:
        parsed = generate_json(prompt, system=system)
    except Exception:
        return {"text": "", "bullets": []}

    if isinstance(parsed, dict):
        text = parsed.get("text") or parsed.get("summary") or ""
        bullets = parsed.get("bullets") or []
        if not isinstance(bullets, list):
            bullets = []
        return {"text": str(text), "bullets": [str(b) for b in bullets]}

    return {"text": str(parsed), "bullets": []}
