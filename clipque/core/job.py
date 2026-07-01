from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .captioner import CaptionCycler, clean_ai_caption
from .queue_db import ClipQueueDB
from .transcriber import LocalWhisperTranscriber
from .utils import format_seconds, parse_time_to_seconds, safe_folder_name
from .video import check_ffmpeg, chunk_list, create_tiktok_part, generate_random_segments, get_video_duration


@dataclass
class ClipJobConfig:
    video_path: Path
    output_folder: Path
    start_time: str
    end_time: str
    min_clip_seconds: int
    max_clip_seconds: int
    videos_per_folder: int
    whisper_model: str
    copy_original: bool
    gemini_api_key: str
    gemini_model: str
    openrouter_api_key: str
    openrouter_models: list[str]
    base_hashtags: str


@dataclass
class ClipJobResult:
    parent_folder: Path
    db_path: Path
    csv_path: Path
    total_clips: int
    total_groups: int


class ClipJobRunner:
    def __init__(self, config: ClipJobConfig, log_callback=None):
        self.config = config
        self.log_callback = log_callback

    def log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def run(self) -> ClipJobResult:
        cfg = self.config
        video = Path(cfg.video_path)
        if not video.exists():
            raise FileNotFoundError("Please pick a valid video file.")

        output_base = Path(cfg.output_folder) if str(cfg.output_folder).strip() else video.parent
        output_base.mkdir(parents=True, exist_ok=True)

        check_ffmpeg()
        video_duration = get_video_duration(video)
        start = parse_time_to_seconds(cfg.start_time)
        end = parse_time_to_seconds(cfg.end_time) if str(cfg.end_time).strip() else video_duration
        start = max(0, start)
        end = min(video_duration, end)

        if start >= end:
            raise ValueError("Start time must be before end time.")
        if cfg.min_clip_seconds < 1 or cfg.max_clip_seconds < 1:
            raise ValueError("Clip lengths must be at least 1 second.")
        if cfg.min_clip_seconds > cfg.max_clip_seconds:
            raise ValueError("Min clip length cannot be bigger than max clip length.")
        if cfg.videos_per_folder < 1:
            raise ValueError("Videos per folder must be at least 1.")

        # Better than the old version: do not nest _clips if user chooses a folder with same video name.
        parent = output_base / f"{safe_folder_name(video.stem)}_clips"
        parent.mkdir(parents=True, exist_ok=True)
        db_path = parent / "clipque_queue.sqlite3"
        db = ClipQueueDB(db_path)

        self.log("")
        self.log(f"Parent folder: {parent}")
        self.log(f"Selected section: {format_seconds(start)} to {format_seconds(end)}")

        if cfg.copy_original:
            copied_original = parent / video.name
            try:
                if copied_original.resolve() != video.resolve():
                    self.log("Copying original video into parent folder...")
                    shutil.copy2(video, copied_original)
            except Exception as copy_exc:
                self.log(f"Warning: could not copy original video, continuing anyway: {copy_exc}")

        segments = generate_random_segments(start, end, cfg.min_clip_seconds, cfg.max_clip_seconds)
        groups = list(chunk_list(segments, cfg.videos_per_folder))
        self.log(f"Generated {len(segments)} clip section(s).")
        self.log(f"Grouped into {len(groups)} folder(s), {cfg.videos_per_folder} video(s) per folder.")
        self.log("Loading Whisper model. First time can take a while...")

        transcriber = LocalWhisperTranscriber(cfg.whisper_model)
        captioner = CaptionCycler(
            gemini_key=cfg.gemini_api_key,
            gemini_model=cfg.gemini_model,
            openrouter_key=cfg.openrouter_api_key,
            openrouter_models=cfg.openrouter_models,
            base_hashtags=cfg.base_hashtags,
            log_callback=self.log,
        )

        project_name = safe_folder_name(video.stem)
        index_lines = [
            f"Original video: {video.name}",
            f"Selected section: {format_seconds(start)} to {format_seconds(end)}",
            f"Clip length range: {cfg.min_clip_seconds}-{cfg.max_clip_seconds} seconds",
            f"Videos per folder: {cfg.videos_per_folder}",
            f"Total clips: {len(segments)}",
            f"Total groups: {len(groups)}",
            f"SQLite queue: {db_path.name}",
            "",
        ]

        for group_number, group_segments in enumerate(groups, start=1):
            group_name = f"group_{group_number:02d}"
            group_folder = parent / group_name
            group_folder.mkdir(parents=True, exist_ok=True)
            self.log("")
            self.log(f"{group_name}: creating {len(group_segments)} TikTok part(s)...")

            combined_transcript_parts: list[str] = []
            created_parts: list[tuple[int, Path, float, float, float]] = []

            for part_number, (clip_start, clip_end) in enumerate(group_segments, start=1):
                clip_duration = clip_end - clip_start
                part_video = group_folder / f"part_{part_number}.mp4"
                self.log(f"Creating part_{part_number}: {format_seconds(clip_start)} to {format_seconds(clip_end)}")
                create_tiktok_part(video, part_video, clip_start, clip_duration, part_number)
                self.log(f"Transcribing part_{part_number} internally...")
                plain_text = transcriber.transcribe_plain(part_video)
                if plain_text:
                    combined_transcript_parts.append(f"Part {part_number}: {plain_text}")
                created_parts.append((part_number, part_video, clip_start, clip_end, clip_duration))
                index_lines.append(
                    f"{group_name}/part_{part_number}.mp4: {format_seconds(clip_start)} - {format_seconds(clip_end)} ({format_seconds(clip_duration)})"
                )

            combined_transcript = "\n".join(combined_transcript_parts).strip()
            self.log(f"Creating caption + hashtags for {group_name}...")
            result = captioner.generate(combined_transcript, group_name, group_number)
            caption_text = clean_ai_caption(result.text)
            caption_path = group_folder / "caption.txt"
            caption_path.write_text(caption_text.strip() + "\n", encoding="utf-8")
            self.log(f"Saved caption.txt using {result.provider}")

            for part_number, part_video, clip_start, clip_end, clip_duration in created_parts:
                part_caption = f"Part {part_number}/{len(created_parts)} - {caption_text}"
                db.add_clip({
                    "project_name": project_name,
                    "group_name": group_name,
                    "part_number": part_number,
                    "video_file": str(part_video),
                    "caption": part_caption,
                    "status": "READY",
                    "start_time": format_seconds(clip_start),
                    "end_time": format_seconds(clip_end),
                    "duration": format_seconds(clip_duration),
                    "caption_provider": result.provider,
                    "last_error": "",
                })

        (parent / "clips_index.txt").write_text("\n".join(index_lines).strip() + "\n", encoding="utf-8")
        csv_path = parent / "clipque_upload_queue.csv"
        db.export_csv(csv_path, project_name=project_name)
        return ClipJobResult(parent_folder=parent, db_path=db_path, csv_path=csv_path, total_clips=len(segments), total_groups=len(groups))
