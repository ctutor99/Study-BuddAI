# backend/app/main.py
from dotenv import load_dotenv
load_dotenv()

import os
import time
import tempfile
import uuid
import threading
import json
from pathlib import Path
from typing import Optional, Any

from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse

# Local wrappers you already have
from .stt import transcribe_audio
from .chat import summarize_text, call_chat  # call_chat expected to return text

app = FastAPI(title="Study BuddAI Backend")

# Data directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# In-memory sessions store (simple)
sessions: dict = {}

# -----------------------------
# Helpers
# -----------------------------
def extract_json_from_text(text: str) -> Optional[Any]:
    """
    Try to extract JSON from model output robustly.
    Returns parsed Python object or None on failure.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    # Try direct JSON parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try to find fenced block ```json ... ```
    import re, ast
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    payload = None
    if m:
        payload = m.group(1).strip()
    else:
        # Try first JSON array/object in text
        m2 = re.search(r"(\{[\s\S]*?\}|\[[\s\S]*?\])", text)
        if m2:
            payload = m2.group(1).strip()

    if not payload:
        return None

    # Try JSON loads, then ast literal_eval
    try:
        return json.loads(payload)
    except Exception:
        try:
            return ast.literal_eval(payload)
        except Exception:
            return None

# -----------------------------
# API endpoints
# -----------------------------
@app.post("/start_lecture")
def start_lecture(title: str = "untitled"):
    """
    Create a new lecture session and return session_id.
    """
    session_id = str(uuid.uuid4())
    file_path = DATA_DIR / f"{session_id}.webm"
    sessions[session_id] = {
        "file": str(file_path),
        "status": "recording",
        "title": title,
        "chunks": 0,
        "transcript": None,
        "summary": None,
        "engagement_questions": None,
        "prof_questions": None,
        "error": None,
    }
    return {"session_id": session_id}

@app.post("/upload_chunk/{session_id}")
async def upload_chunk(session_id: str, file: UploadFile = File(...)):
    """
    Append an uploaded chunk to the session's assembled file.
    This endpoint treats each POST as an append; the client uploads blobs (webm).
    """
    s = sessions.get(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "session not found"})

    chunk_bytes = await file.read()
    if not chunk_bytes:
        return JSONResponse(status_code=400, content={"error": "empty chunk"})

    target = Path(s["file"])
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(target, "ab") as fh:
            fh.write(chunk_bytes)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": f"failed to write chunk: {str(exc)}"})

    s["chunks"] = s.get("chunks", 0) + 1
    return {"ok": True, "chunks": s["chunks"]}

def _generate_postlecture_materials(session_id: str):
    """
    Runs in background (thread). Transcribes, summarizes, and generates questions.
    """
    s = sessions.get(session_id)
    if not s:
        return

    assembled_path = Path(s["file"])
    if not assembled_path.exists() or assembled_path.stat().st_size == 0:
        s["status"] = "error"
        s["error"] = "No audio uploaded."
        return

    s["status"] = "transcribing"
    try:
        transcript = transcribe_audio(str(assembled_path)) or ""
        s["transcript"] = transcript
    except Exception as exc:
        s["status"] = "error"
        s["error"] = f"Transcription failed: {str(exc)}"
        return

    s["status"] = "summarizing"
    try:
        summary = summarize_text(s["transcript"])
        s["summary"] = summary if isinstance(summary, str) else str(summary)
    except Exception as exc:
        s["status"] = "error"
        s["error"] = f"Summarization failed: {str(exc)}"
        return

    # Generate engagement and professor questions using call_chat; parse strictly
    try:
        # Engagement Qs
        model = os.getenv("OPENAI_QUICK_MODEL", "gpt-3.5-turbo")
        prompt_e = (
            "Return ONLY a JSON array of 6 objects with fields {question, answer, difficulty}. "
            "Create short comprehension questions based on the transcript. Transcript:\n\n"
            + s["transcript"]
        )
        messages_e = [
            {"role": "system", "content": "You are a teacher assistant. Output valid JSON only."},
            {"role": "user", "content": prompt_e}
        ]
        gen_e = call_chat(model, messages_e, max_tokens=400, temperature=0.0)
        parsed_e = extract_json_from_text(gen_e)
        s["engagement_questions"] = parsed_e if parsed_e is not None else [{"raw": gen_e}]
    except Exception:
        s["engagement_questions"] = None

    try:
        # Prof questions
        prompt_p = (
            "Return ONLY a JSON array of 6 objects with fields {question, intent}. "
            "Generate questions a student could ask the professor to probe deeper. Transcript:\n\n"
            + s["transcript"]
        )
        messages_p = [
            {"role": "system", "content": "You are a teacher assistant. Output valid JSON only."},
            {"role": "user", "content": prompt_p}
        ]
        gen_p = call_chat(model, messages_p, max_tokens=400, temperature=0.0)
        parsed_p = extract_json_from_text(gen_p)
        s["prof_questions"] = parsed_p if parsed_p is not None else [{"raw": gen_p}]
    except Exception:
        s["prof_questions"] = None

    s["status"] = "done"

@app.post("/end_lecture/{session_id}")
def end_lecture(session_id: str, background_tasks: BackgroundTasks):
    """
    Signal that recording is finished; trigger background processing.
    """
    s = sessions.get(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "session not found"})

    s["status"] = "processing"
    # Process in a background thread to avoid blocking uvicorn loop
    def run_process():
        _generate_postlecture_materials(session_id)

    thread = threading.Thread(target=run_process, daemon=True)
    thread.start()
    return {"ok": True, "status": "processing"}

@app.get("/results/{session_id}")
def results(session_id: str):
    s = sessions.get(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "session not found"})
    return {
        "status": s.get("status"),
        "transcript": s.get("transcript"),
        "summary": s.get("summary"),
        "engagement_questions": s.get("engagement_questions"),
        "prof_questions": s.get("prof_questions"),
        "error": s.get("error"),
    }

# Optional: export flashcards endpoint (CSV)
@app.get("/export_flashcards/{session_id}")
def export_flashcards(session_id: str):
    s = sessions.get(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "session not found"})
    if s.get("status") != "done" or not s.get("transcript"):
        return JSONResponse(status_code=400, content={"error": "transcript not ready; wait until status=done"})

    transcript = s["transcript"]
    prompt = (
        "Return ONLY a JSON array (max 40) of flashcards with fields: question, answer, difficulty (easy/medium/hard), topic. "
        "Transcript:\n\n" + transcript
    )
    try:
        model = os.getenv("OPENAI_QUICK_MODEL", "gpt-3.5-turbo")
        messages = [
            {"role": "system", "content": "You are a helpful assistant that returns valid JSON only."},
            {"role": "user", "content": prompt}
        ]
        gen = call_chat(model, messages, max_tokens=800, temperature=0.0)
        cards = extract_json_from_text(gen)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": f"model call failed: {str(exc)}"})

    if not isinstance(cards, list):
        return JSONResponse(status_code=500, content={"error": "failed to parse flashcards JSON", "raw": gen})

    # Build CSV
    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["question", "answer", "difficulty", "topic"])
    for c in cards:
        if isinstance(c, dict):
            q = c.get("question", "").replace("\n", " ").strip()
            a = c.get("answer", "").replace("\n", " ").strip()
            d = c.get("difficulty", "medium")
            t = c.get("topic", "")
            writer.writerow([q, a, d, t])
    output.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="flashcards_{session_id}.csv"'}
    return StreamingResponse(output, media_type="text/csv", headers=headers)