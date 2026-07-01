from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def parse_time_to_seconds(value: str) -> float:
    value = (value or "").strip()
    if not value:
        return 0.0
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return float(value)
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Invalid time format: {value}")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"Invalid time format: {value}")
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def format_seconds(seconds: float) -> str:
    seconds = max(0, float(seconds))
    whole = int(seconds)
    ms = int(round((seconds - whole) * 1000))
    h = whole // 3600
    m = (whole % 3600) // 60
    s = whole % 60
    if ms:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def safe_folder_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120] or "video_clips"


def run_command(command: list[str]) -> str:
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or "Command failed")
    return process.stdout.strip()


def open_path(path: Path) -> None:
    path = Path(path)
    if os.name == "nt":
        os.startfile(str(path))
    elif os.name == "posix":
        subprocess.Popen(["open" if os.uname().sysname == "Darwin" else "xdg-open", str(path)])
    else:
        raise RuntimeError("Opening paths is not supported on this OS yet.")
