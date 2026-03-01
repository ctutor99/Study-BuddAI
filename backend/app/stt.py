import os
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def transcribe_audio(filepath: str) -> str:
    """
    Uses OpenAI Speech-to-Text (Whisper model)
    """
    with open(filepath, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",   # OpenAI speech-to-text model
            file=audio_file
        )

    return transcript.text