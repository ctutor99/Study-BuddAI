import json
import os
import re
import time
from typing import Any, Optional

import httpx

_RAW_BASE = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
HOST = _RAW_BASE.rstrip("/")
if HOST.endswith("/v1"):
    HOST = HOST[: -len("/v1")]

MODEL = os.getenv("LLM_MODEL", "qwen3.6:35b-a3b")

_MAX_RETRIES = 3
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


def _chat_json(messages: list, *, temperature: float, max_tokens: int) -> str:
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }

    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = httpx.post(f"{HOST}/api/chat", json=payload, timeout=_TIMEOUT)
            if resp.status_code >= 500:
                last_exc = RuntimeError(f"LLM server {resp.status_code}: {resp.text[:200]}")
            else:
                resp.raise_for_status()
                return (resp.json().get("message") or {}).get("content") or ""
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exc = exc
        if attempt < _MAX_RETRIES - 1:
            time.sleep(2 * (attempt + 1))
    raise last_exc


_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


def _loads_lenient(text: str) -> Any:
    text = _THINK_RE.sub("", text).strip()
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
    raw = _chat_json(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_output_tokens,
    )
    return _loads_lenient(raw.strip())


def summarize_text(transcript_text: str) -> dict:
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

    parsed = generate_json(prompt, system=system)

    if isinstance(parsed, dict):
        text = parsed.get("text") or parsed.get("summary") or ""
        bullets = parsed.get("bullets") or []
        if not isinstance(bullets, list):
            bullets = []
        return {"text": str(text), "bullets": [str(b) for b in bullets]}

    return {"text": str(parsed), "bullets": []}
