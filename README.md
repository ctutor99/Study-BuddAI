Welcome!

This is my lecture transcription and study assistant web application. It records audio in short chunks, transcribes each chunk live, generates comprehension questions from the transcript as it grows, and produces a full summary of the whole lecture when you press End.

Frontend: React, Vite, JavaScript, MediaRecorder API
Backend: Python, FastAPI, Google Gemini API (`google-genai`, `gemini-3.6-flash` for both speech-to-text and summaries/questions)

How to run: Right now, it only runs locally. Here are the command line commands to run in different instances of the console.

Backend:
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then add your GEMINI_API_KEY (https://aistudio.google.com/apikey)
uvicorn app.main:app --reload

Frontend:
cd frontend
npm install
npm run dev

The Vite dev server proxies `/api/*` to the backend on port 8000.

Version 1.0 - Includes:
- Chunked lecture audio recording (~5s standalone webm chunks)
- Live per-chunk speech-to-text transcription
- Live comprehension questions generated from the transcript as it grows
- Full lecture summary generated on End
- Transcript, questions, and summary streamed to the browser over SSE

Flow: `POST /start_lecture` -> repeated `POST /upload_chunk/{id}` while recording,
with updates pushed over `GET /events/{id}` (SSE) -> `POST /end_lecture/{id}` to
summarize the full transcript.

Thanks!
