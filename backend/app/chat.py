# backend/app/chat.py
import os
import json
from openai import OpenAI

# Use the same OpenAI client style used in your stt.py
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def call_chat(model: str, messages: list, max_tokens: int = 300, temperature: float = 0.0) -> str:
    """
    Call a chat completion and return the text output (string).
    Uses synchronous OpenAI client call. Keep temperature low for deterministic results.
    """
    # The client.chat.completions.create interface (OpenAI python SDK) returns a dict-like object
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature
        )
        # Response structure may vary — try common access patterns
        # New SDK: resp.choices[0].message.content
        if hasattr(resp, "choices") and resp.choices:
            first = resp.choices[0]
            msg = first.message
            if isinstance(msg, dict):
                return msg.get("content", "")
            # some wrappers return objects with .content
            if hasattr(msg, "content"):
                return msg.content
        # fallback: str(resp)
        return str(resp)
    except Exception as e:
        # bubble up error as string
        raise

def summarize_text(transcript_text: str) -> dict:
    """
    Produce a concise summary + bullet points in a structured dict.
    Returns a dict { "summary": "3-5 sentences", "bullets": ["..",".."] }
    This uses the chat model deterministically (temperature=0).
    """
    # Trim transcript to reasonable size for a single request. If huge, summarize in chunks externally.
    if not transcript_text:
        return {"summary": "", "bullets": []}

    prompt = (
        "You are a helpful assistant that MUST return a JSON object with two fields:\n"
        " - summary: a concise 3-5 sentence summary of the lecture text\n"
        " - bullets: an array of 4-8 short bullet points (each 3-12 words) listing the main points\n\n"
        "Respond ONLY with valid JSON (no surrounding backticks, no explanation).\n\n"
        "Lecture transcript:\n\n"
        f"{transcript_text}\n\n"
        "Produce the JSON now."
    )

    messages = [
        {"role": "system", "content": "You are a precise summarization assistant."},
        {"role": "user", "content": prompt}
    ]

    model = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-3.5-turbo")
    temperature = float(os.getenv("OPENAI_SUMMARY_TEMPERATURE", "0.0"))

    resp_text = call_chat(model, messages, max_tokens=600, temperature=temperature)

    # Clean fenced code if present
    resp_text = resp_text.strip()
    resp_text = resp_text.strip("`")
    # attempt JSON parse
    try:
        parsed = json.loads(resp_text)
        # Validate basic shape
        if isinstance(parsed, dict) and "summary" in parsed:
            return parsed
    except Exception:
        # try to extract JSON substring
        import re
        m = re.search(r"(\{.*\})", resp_text, flags=re.DOTALL)
        if m:
            snippet = m.group(1)
            try:
                parsed = json.loads(snippet)
                if isinstance(parsed, dict) and "summary" in parsed:
                    return parsed
            except Exception:
                pass

    # If parsing failed, create a safe fallback by requesting a short summary without JSON
    # But keep it deterministic and short to avoid hallucination: use the first ~800 chars as content
    fallback_summary = resp_text
    # As a last resort return raw text in 'summary' key
    return {"summary": fallback_summary[:1000], "bullets": []}