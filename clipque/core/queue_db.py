from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    group_name TEXT NOT NULL,
    part_number INTEGER NOT NULL,
    video_file TEXT NOT NULL,
    caption TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'READY',
    start_time TEXT,
    end_time TEXT,
    duration TEXT,
    caption_provider TEXT,
    tiktok_post_id TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);
CREATE INDEX IF NOT EXISTS idx_clips_project ON clips(project_name);
"""


class ClipQueueDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def add_clip(self, row: dict) -> int:
        columns = [
            "project_name", "group_name", "part_number", "video_file", "caption", "status",
            "start_time", "end_time", "duration", "caption_provider", "last_error",
        ]
        values = [row.get(col) for col in columns]
        placeholders = ", ".join(["?"] * len(columns))
        sql = f"INSERT INTO clips ({', '.join(columns)}) VALUES ({placeholders})"
        with self.connect() as conn:
            cur = conn.execute(sql, values)
            return int(cur.lastrowid)

    def list_clips(self, limit: int = 500) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM clips ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_project(self, project_name: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM clips WHERE project_name = ? ORDER BY id ASC",
                (project_name,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_status(self, clip_id: int, status: str, last_error: str = "", post_id: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE clips
                SET status = ?, last_error = ?, tiktok_post_id = COALESCE(NULLIF(?, ''), tiktok_post_id), updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, last_error, post_id, clip_id),
            )

    def export_csv(self, csv_path: Path, project_name: str | None = None) -> None:
        rows = self.list_project(project_name) if project_name else self.list_clips(limit=100000)
        fieldnames = [
            "id", "project_name", "group_name", "part_number", "video_file", "caption", "status",
            "start_time", "end_time", "duration", "caption_provider", "tiktok_post_id", "last_error",
            "created_at", "updated_at",
        ]
        csv_path = Path(csv_path)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fieldnames})
