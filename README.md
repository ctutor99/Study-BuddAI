Welcome!

This is my lecture transcription and study assistant web application. It records audio in short chunks, transcribes each chunk live, generates comprehension questions from the transcript as it grows, and produces a full summary of the whole lecture when you press End.

Everything runs locally — no API keys, no data leaving the machine.

Frontend: React, Vite, JavaScript, MediaRecorder API
Backend: Python, FastAPI
- Speech-to-text: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`large-v3-turbo`), in-process
- Questions & summaries: a local LLM over any OpenAI-compatible endpoint — [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) (Q4_K_M) served by [Ollama](https://ollama.com) by default

## Prerequisites

**Ollama** (or llama.cpp / LM Studio) running the LLM:
```
ollama pull qwen3.6:35b-a3b
ollama serve
```
The 35B-A3B MoE model is ~20 GB at Q4_K_M. It runs on an 8 GB GPU + system RAM
because only ~3B parameters are active per token; Ollama offloads the experts to
CPU automatically. If it's too heavy, set `LLM_MODEL=qwen3:14b` in `backend/.env`
— it fits an 8 GB card almost entirely.

Python 3.10+ and Node are also needed. faster-whisper's dependencies (PyAV)
bundle the codecs needed to decode the browser's webm/opus chunks, so no separate
ffmpeg install is required. On first run it downloads the Whisper weights
(~1.5 GB for `large-v3-turbo`).

## How to run

Right now it only runs locally. Run these in separate terminals.

Backend:
```
cd backend
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # defaults assume local Ollama; edit if needed
uvicorn app.main:app --reload
```
The first request loads the Whisper model (a one-time download on first run).

Frontend:
```
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api/*` to the backend on port 8000.

## Configuration (`backend/.env`)

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | Any OpenAI-compatible endpoint |
| `LLM_MODEL` | `qwen3.6:35b-a3b` | e.g. `qwen3:14b` for a lighter model |
| `WHISPER_MODEL` | `large-v3-turbo` | any faster-whisper model name |
| `WHISPER_DEVICE` | `cpu` | `cuda` to run STT on the GPU |
| `WHISPER_COMPUTE_TYPE` | `int8` | `float16` / `int8_float16` for `cuda` |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | CORS allow-origin |

## Flow

`POST /start_lecture` -> repeated `POST /upload_chunk/{id}` while recording,
with updates pushed over `GET /events/{id}` (SSE) -> `POST /end_lecture/{id}` to
summarize the full transcript. `GET /results/{id}` returns a plain snapshot.

Version 2.0 — Includes:
- Chunked lecture audio recording (~5s standalone webm chunks, recorded back-to-back)
- Live per-chunk speech-to-text with a local Whisper model
- Live comprehension questions generated from the transcript by a local LLM
- Full lecture summary generated on End
- Transcript, questions, and summary streamed to the browser over SSE, with a
  state snapshot on every (re)connect so a dropped stream resyncs cleanly

Thanks!
