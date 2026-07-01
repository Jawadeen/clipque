from __future__ import annotations

import json
import os
from pathlib import Path

APP_TITLE = "ClipQue Desktop v2.0"
APP_NAME = "ClipQue"
APP_DIR = Path(os.environ.get("APPDATA", Path.home() / ".clipque")) / "ClipQue"
SETTINGS_PATH = APP_DIR / "settings.json"

CANVAS_W = 1080
CANVAS_H = 1920
VIDEO_BOX_H = 850
VIDEO_TOP_Y = 620
PART_TEXT_Y = 330

GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

DEFAULT_OPENROUTER_MODELS = [
    "deepseek/deepseek-chat:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "meta-llama/llama-3.1-8b-instruct:free",
]

DEFAULT_HASHTAGS = ["#fyp", "#viral", "#gaming", "#storytime", "#part1"]

DEFAULT_SETTINGS = {
    "whisper_model": "base",
    "min_clip_seconds": "30",
    "max_clip_seconds": "90",
    "videos_per_folder": "3",
    "gemini_model": GEMINI_MODELS[0],
    "openrouter_models": ", ".join(DEFAULT_OPENROUTER_MODELS),
    "base_hashtags": " ".join(DEFAULT_HASHTAGS),
    "copy_original": False,
    "last_output_folder": "",
}


def load_settings() -> dict:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_PATH.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data if isinstance(data, dict) else {})
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    clean = dict(DEFAULT_SETTINGS)
    clean.update(settings or {})
    SETTINGS_PATH.write_text(json.dumps(clean, indent=2), encoding="utf-8")
