from __future__ import annotations

import random
from pathlib import Path

from .config import CANVAS_H, CANVAS_W, PART_TEXT_Y, VIDEO_BOX_H, VIDEO_TOP_Y
from .utils import run_command


def check_ffmpeg() -> None:
    try:
        run_command(["ffmpeg", "-version"])
        run_command(["ffprobe", "-version"])
    except Exception:
        raise RuntimeError(
            "FFmpeg was not found.\n\n"
            "Install FFmpeg and make sure ffmpeg and ffprobe are available in PATH."
        )


def get_video_duration(video_path: Path) -> float:
    output = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ])
    try:
        return float(output)
    except ValueError:
        raise RuntimeError("Could not read video duration with ffprobe.")


def generate_random_segments(start: float, end: float, min_len: int, max_len: int) -> list[tuple[float, float]]:
    if end <= start:
        raise ValueError("End time must be after start time.")
    if min_len <= 0 or max_len <= 0:
        raise ValueError("Clip lengths must be positive.")
    if min_len > max_len:
        raise ValueError("Minimum clip length cannot be bigger than maximum clip length.")

    segments: list[tuple[float, float]] = []
    current = start
    while current < end:
        remaining = end - current
        if remaining <= max_len:
            segments.append((current, end))
            break
        latest_allowed = min(max_len, int(remaining - min_len))
        if latest_allowed < min_len:
            segments.append((current, end))
            break
        duration = random.randint(min_len, latest_allowed)
        next_time = current + duration
        segments.append((current, next_time))
        current = next_time
    return segments


def chunk_list(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def escape_drawtext_text(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace(":", r"\:")
    text = text.replace("'", r"\'")
    return text


def build_tiktok_filter(part_label: str) -> str:
    label = escape_drawtext_text(part_label)
    return (
        f"scale={CANVAS_W}:{VIDEO_BOX_H}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={CANVAS_W}:{CANVAS_H}:(ow-iw)/2:{VIDEO_TOP_Y}:color=black,"
        "setsar=1,"
        f"drawtext=text='{label}':fontcolor=white:fontsize=96:"
        "borderw=5:bordercolor=black:x=(w-text_w)/2:"
        f"y={PART_TEXT_Y}"
    )


def create_tiktok_part(input_video: Path, output_video: Path, start: float, duration: float, part_number: int) -> None:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    label = f"PART {part_number}"
    vf = build_tiktok_filter(label)
    command = [
        "ffmpeg",
        "-y",
        "-ss", str(start),
        "-i", str(input_video),
        "-t", str(duration),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_video),
    ]
    run_command(command)
