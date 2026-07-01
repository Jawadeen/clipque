from __future__ import annotations

from pathlib import Path


class LocalWhisperTranscriber:
    def __init__(self, model_name: str = "base"):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError(
                "Missing Python package: faster-whisper\n\n"
                "Install it with:\n"
                "pip install faster-whisper"
            )
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe_plain(self, video_path: Path) -> str:
        segments, _info = self.model.transcribe(
            str(video_path),
            vad_filter=True,
            beam_size=5,
        )
        texts: list[str] = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                texts.append(text)
        return " ".join(texts).strip()
