Welcome!

This is my lecture transcription and study assistant web application that records audio, transcribes lectures, summarizes content, and generates study questions and flashcards.

Frontend: React, Vite, JavaScript, MediaRecorder API  
Backend: Python, FastAPI, OpenAI API (Whisper for speech-to-text, Chat models for summaries/questions)

How to run: Right now, it only runs locally. Here are the command line commands to run in different instances of the console.

Backend:
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

Frontend:
cd frontend
npm install
npm run dev

Version 1.0 - Includes:
- Lecture audio recording
- Speech-to-text transcription
- Automatic lecture summary
- Engagement questions
- Exportable flashcards (CSV)

Thanks!