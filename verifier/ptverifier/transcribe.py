"""Speech to timed words, locally with faster-whisper. The model downloads once on first use."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Transcriber(Protocol):
    def words(self, video: Path) -> list[tuple[float, str]]:
        """(start_seconds, word) for every word spoken in the clip."""


class WhisperTranscriber:
    def __init__(self, model: str = "small.en"):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model, device="cpu", compute_type="int8")

    def words(self, video: Path) -> list[tuple[float, str]]:
        segments, _ = self.model.transcribe(str(video), language="en", word_timestamps=True, vad_filter=True)
        return [(float(w.start), w.word) for seg in segments for w in (seg.words or [])]
