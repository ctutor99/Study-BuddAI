import io
import os
import threading
from typing import Optional

from faster_whisper import WhisperModel

_MODEL_NAME = os.getenv("WHISPER_MODEL", "large-v3-turbo")
_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

_model: Optional[WhisperModel] = None
_model_lock = threading.Lock()


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = WhisperModel(
                    _MODEL_NAME, device=_DEVICE, compute_type=_COMPUTE_TYPE
                )
    return _model


def warm_up() -> None:
    _get_model()


def transcribe_audio_bytes(data: bytes, mime_type: str = "audio/webm") -> str:
    if not data:
        return ""

    model = _get_model()
    segments, _info = model.transcribe(
        io.BytesIO(data),
        language="en",
        vad_filter=True,
        condition_on_previous_text=False,
    )
    return " ".join(seg.text.strip() for seg in segments).strip()
