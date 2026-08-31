from dotenv import load_dotenv

load_dotenv()

import asyncio
import contextlib
import json
import os
import uuid

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .llm import generate_json, summarize_text
from .stt import transcribe_audio_bytes, warm_up


def _drain_warm_up(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    warm = asyncio.create_task(asyncio.to_thread(warm_up))
    warm.add_done_callback(_drain_warm_up)
    yield
    warm.cancel()


app = FastAPI(title="Study BuddAI Backend", lifespan=lifespan)

_frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_origin, "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_CHUNK_BYTES = 8 * 1024 * 1024

MAX_LIVE_QUESTIONS = 40

QUESTION_WINDOW_CHARS = 300

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
        "subscribers": [],
        "append_lock": asyncio.Lock(),
        "chunks": 0,
        "task": None,
        "questions_task": None,
        "q_cursor": 0,
    }
    return session_id


def _snapshot_event(s: dict) -> dict:
    return {
        "type": "snapshot",
        "status": s["status"],
        "transcript": s["transcript"],
        "questions": s["live_questions"],
        "summary": s["summary"],
        "error": s["error"],
    }


def _emit(s: dict, event: dict) -> None:
    for q in list(s["subscribers"]):
        q.put_nowait(event)


_bg_tasks: set = set()


def _spawn_questions(s: dict) -> None:
    pending = s.get("questions_task")
    if pending is not None and not pending.done():
        return

    transcript = s["transcript"]
    cursor = s["q_cursor"]
    new_text = transcript[cursor:].strip()
    if len(new_text) < QUESTION_WINDOW_CHARS:
        return
    context = transcript[:cursor]
    s["q_cursor"] = len(transcript)

    async def _run() -> None:
        try:
            items = await _questions_for(new_text, context)
        except Exception as exc:
            s["q_cursor"] = min(s["q_cursor"], cursor)
            _emit(s, {"type": "warning", "message": f"question generation failed: {exc}"})
            return
        if items:
            s["live_questions"].extend(items)
            del s["live_questions"][:-MAX_LIVE_QUESTIONS]
            _emit(s, {"type": "questions", "items": items})

    task = asyncio.create_task(_run())
    s["questions_task"] = task
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def _questions_for(new_text: str, context: str) -> list:
    prompt = (
        "A lecture is in progress. Earlier context is given for reference; write "
        "questions about the LATEST passage only.\n\n"
        f"Earlier context:\n{context[-1500:]}\n\n"
        f"Latest passage:\n{new_text}\n\n"
        "Return a JSON object {\"questions\": [...]} whose list holds 1-2 objects "
        "with fields {question, answer, difficulty}. If the latest passage has no "
        "substantive content, return {\"questions\": []}."
    )
    parsed = await asyncio.to_thread(generate_json, prompt, system=SYSTEM_TA)
    if isinstance(parsed, dict):
        parsed = parsed.get("questions", [])
    return parsed if isinstance(parsed, list) else []


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/start_lecture")
async def start_lecture(body: StartLecture):
    return {"session_id": _new_session(body.title)}


@app.post("/upload_chunk/{session_id}")
async def upload_chunk(session_id: str, file: UploadFile = File(...)):
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
        _emit(s, {"type": "warning", "message": f"a chunk failed to transcribe: {exc}"})
        return JSONResponse(status_code=502, content={"error": str(exc)})

    if not text:
        return {"ok": True, "transcribed": ""}

    async with s["append_lock"]:
        prior = s["transcript"]
        s["transcript"] = f"{prior} {text}".strip() if prior else text
        _emit(s, {"type": "transcript", "delta": text, "full": s["transcript"]})

    _spawn_questions(s)

    return {"ok": True, "transcribed": text}


@app.get("/events/{session_id}")
async def events(session_id: str):
    s = sessions.get(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "session not found"})

    async def stream():
        q: asyncio.Queue = asyncio.Queue()
        s["subscribers"].append(q)
        try:
            yield f"data: {json.dumps(_snapshot_event(s))}\n\n"
            if s["status"] == "done":
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return
            if s["status"] == "error":
                err = s["error"] or "the lecture ended with an error"
                yield f"data: {json.dumps({'type': 'error', 'error': err})}\n\n"
                return
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("type") in ("done", "error"):
                    break
        finally:
            with contextlib.suppress(ValueError):
                s["subscribers"].remove(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/end_lecture/{session_id}")
async def end_lecture(session_id: str):
    s = sessions.get(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "session not found"})
    if s["status"] in ("summarizing", "done"):
        return {"ok": True, "status": s["status"]}

    s["status"] = "summarizing"
    _emit(s, {"type": "status", "status": "summarizing"})
    s["task"] = asyncio.create_task(_finalize(session_id))
    return {"ok": True, "status": "summarizing"}


async def _finalize(session_id: str) -> None:
    s = sessions.get(session_id)
    if not s:
        return

    for task in list(_bg_tasks):
        task.cancel()

    async with s["append_lock"]:
        transcript = s["transcript"]

    if not transcript.strip():
        s["status"] = "error"
        s["error"] = "nothing was transcribed, so there is nothing to summarize"
        _emit(s, {"type": "error", "error": s["error"]})
        return

    try:
        summary = await asyncio.to_thread(summarize_text, transcript)
    except Exception as exc:
        s["status"] = "error"
        s["error"] = str(exc)
        _emit(s, {"type": "error", "error": f"summary failed: {exc}"})
        return
    if not summary.get("text"):
        s["status"] = "error"
        s["error"] = "the model returned an empty summary"
        _emit(s, {"type": "error", "error": s["error"]})
        return
    s["summary"] = summary
    s["status"] = "done"
    _emit(s, {"type": "summary", "summary": summary})
    _emit(s, {"type": "done"})


@app.get("/results/{session_id}")
def results(session_id: str):
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
