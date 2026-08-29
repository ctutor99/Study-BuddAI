# backend/app/main.py
from dotenv import load_dotenv

load_dotenv()

import asyncio
import json
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .llm import generate_json, summarize_text, transcribe_audio_bytes

app = FastAPI(title="Study BuddAI Backend")

# CORS so the app works with or without the Vite dev proxy (and in production).
_frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_origin, "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data directory (kept for parity with the rest of the app; the live flow keeps
# audio in memory rather than on disk).
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# A single ~5s audio chunk is tiny; cap well above that to reject junk.
MAX_CHUNK_BYTES = 8 * 1024 * 1024

# In-memory session store. Single-process demo; sessions are lost on reload.
sessions: dict = {}

SYSTEM_TA = "You are a teaching assistant. Output valid JSON only."


class StartLecture(BaseModel):
    title: str = "untitled"


def _new_session(title: str) -> str:
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "status": "recording",
        "title": title,
        "transcript": "",
        "live_questions": [],
        "summary": None,
        "error": None,
        "queue": None,
        "chunks": 0,
        "task": None,
    }
    return session_id


def _queue(s: dict) -> asyncio.Queue:
    """The SSE fan-out queue for a session, created on first use inside the loop."""
    q = s.get("queue")
    if q is None:
        q = asyncio.Queue()
        s["queue"] = q
    return q


async def _emit(s: dict, event: dict) -> None:
    await _queue(s).put(event)


async def _questions_for(new_text: str, context: str) -> list:
    """Generate 1-2 comprehension questions about the newest passage of transcript."""
    prompt = (
        "A lecture is in progress. Earlier context is given for reference; write "
        "questions about the LATEST passage only.\n\n"
        f"Earlier context:\n{context[-1500:]}\n\n"
        f"Latest passage:\n{new_text}\n\n"
        "Return a JSON array of 1-2 objects with fields {question, answer, "
        "difficulty}. If the latest passage has no substantive content, return []."
    )
    try:
        items = await asyncio.to_thread(generate_json, prompt, system=SYSTEM_TA)
    except Exception:
        return []
    return items if isinstance(items, list) else []


# -----------------------------
# API endpoints
# -----------------------------
@app.get("/health")
def health():
    return {"ok": True}


@app.post("/start_lecture")
def start_lecture(body: StartLecture):
    """Create a new lecture session and return its id."""
    return {"session_id": _new_session(body.title)}


@app.post("/upload_chunk/{session_id}")
async def upload_chunk(session_id: str, file: UploadFile = File(...)):
    """
    Accept one standalone audio chunk, transcribe it, append it to the running
    transcript, and generate questions about it. Emits SSE events as it goes.

    The response is returned only after transcription completes, so the client can
    await the final chunk before calling /end_lecture.
    """
    s = sessions.get(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "session not found"})

    data = await file.read()
    if not data:
        return {"ok": True, "transcribed": ""}
    if len(data) > MAX_CHUNK_BYTES:
        return JSONResponse(status_code=413, content={"error": "chunk too large"})

    s["chunks"] += 1
    mime = file.content_type or "audio/webm"

    try:
        text = (await asyncio.to_thread(transcribe_audio_bytes, data, mime) or "").strip()
    except Exception as exc:
        await _emit(s, {"type": "error", "error": f"transcription failed: {exc}"})
        return JSONResponse(status_code=502, content={"error": str(exc)})

    if not text:
        return {"ok": True, "transcribed": ""}

    prior = s["transcript"]
    s["transcript"] = f"{prior} {text}".strip() if prior else text
    await _emit(s, {"type": "transcript", "delta": text, "full": s["transcript"]})

    items = await _questions_for(text, prior)
    if items:
        s["live_questions"].extend(items)
        await _emit(s, {"type": "questions", "items": items})

    return {"ok": True, "transcribed": text}


@app.get("/events/{session_id}")
async def events(session_id: str):
    """Server-Sent Events stream of transcript deltas, questions, and the summary."""
    s = sessions.get(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "session not found"})

    async def stream():
        q = _queue(s)
        # Replay current state so a late or reconnecting subscriber catches up.
        yield f"data: {json.dumps({'type': 'transcript', 'delta': '', 'full': s['transcript']})}\n\n"
        if s["live_questions"]:
            yield f"data: {json.dumps({'type': 'questions', 'items': s['live_questions']})}\n\n"
        if s["summary"]:
            yield f"data: {json.dumps({'type': 'summary', 'summary': s['summary']})}\n\n"
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(item)}\n\n"
            if item.get("type") in ("done", "error"):
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/end_lecture/{session_id}")
async def end_lecture(session_id: str):
    """Stop the lecture and summarize the full transcript in the background."""
    s = sessions.get(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "session not found"})
    if s["status"] in ("summarizing", "done"):
        return {"ok": True, "status": s["status"]}

    s["status"] = "summarizing"
    await _emit(s, {"type": "status", "status": "summarizing"})
    s["task"] = asyncio.create_task(_finalize(session_id))
    return {"ok": True, "status": "summarizing"}


async def _finalize(session_id: str) -> None:
    s = sessions.get(session_id)
    if not s:
        return
    try:
        summary = await asyncio.to_thread(summarize_text, s["transcript"])
    except Exception as exc:
        s["status"] = "error"
        s["error"] = str(exc)
        await _emit(s, {"type": "error", "error": f"summary failed: {exc}"})
        return
    s["summary"] = summary
    s["status"] = "done"
    await _emit(s, {"type": "summary", "summary": summary})
    await _emit(s, {"type": "done"})


@app.get("/results/{session_id}")
def results(session_id: str):
    """Snapshot of a session, for a fresh load or as an SSE fallback."""
    s = sessions.get(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "session not found"})
    return {
        "status": s.get("status"),
        "transcript": s.get("transcript"),
        "summary": s.get("summary"),
        "live_questions": s.get("live_questions"),
        "error": s.get("error"),
    }
