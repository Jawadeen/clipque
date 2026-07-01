from __future__ import annotations

import os
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk

from .core.config import APP_DIR, APP_TITLE, GEMINI_MODELS, load_settings, save_settings
from .core.job import ClipJobConfig, ClipJobRunner
from .core.licensing import LicenseManager
from .core.oauth import TikTokOAuthFlow
from .core.queue_db import ClipQueueDB
from .core.tiktok_client import TikTokClient, TikTokTokenStore
from .core.utils import format_seconds, open_path
from .core.video import check_ffmpeg, get_video_duration


class ClipQueApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1040x780")
        self.root.minsize(940, 700)

        self.settings        = load_settings()
        self.license_manager = LicenseManager()
        self.token_store     = TikTokTokenStore()
        self.tiktok_client   = TikTokClient(self.token_store)
        self.oauth_flow      = TikTokOAuthFlow(self.token_store)

        self.is_running          = False
        self.is_posting          = False
        self.last_parent_folder: Path | None = None
        self.last_db_path: Path   | None = None

        # ── Tkinter variables ─────────────────────────────────────────────────
        self.video_path         = tk.StringVar()
        self.output_folder      = tk.StringVar(value=self.settings.get("last_output_folder", ""))
        self.start_time         = tk.StringVar(value="00:00:00")
        self.end_time           = tk.StringVar(value="")
        self.min_clip           = tk.StringVar(value=str(self.settings.get("min_clip_seconds", "30")))
        self.max_clip           = tk.StringVar(value=str(self.settings.get("max_clip_seconds", "90")))
        self.group_size         = tk.StringVar(value=str(self.settings.get("videos_per_folder", "3")))
        self.model_name         = tk.StringVar(value=str(self.settings.get("whisper_model", "base")))
        self.copy_original      = tk.BooleanVar(value=bool(self.settings.get("copy_original", False)))
        self.gemini_api_key     = tk.StringVar(value=os.environ.get("GEMINI_API_KEY", ""))
        self.gemini_model_name  = tk.StringVar(value=str(self.settings.get("gemini_model", GEMINI_MODELS[0])))
        self.openrouter_api_key = tk.StringVar(value=os.environ.get("OPENROUTER_API_KEY", ""))
        self.openrouter_models  = tk.StringVar(value=str(self.settings.get("openrouter_models", "")))
        self.base_hashtags      = tk.StringVar(value=str(self.settings.get("base_hashtags", "#fyp #viral #gaming #storytime #part1")))
        self.license_key        = tk.StringVar(value=self.license_manager.load_key())

        self.build_ui()
        self.log("ClipQue Desktop ready.")
        self.log("Make sure FFmpeg is installed and requirements are met (pip install -r requirements.txt).")
        # Restore TikTok connected state from keychain on startup
        self.root.after(500, self._restore_tiktok_state)

    # ── UI construction ───────────────────────────────────────────────────────
    def build_ui(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="ClipQue Desktop", font=("Arial", 22, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="Clip, transcribe, caption, and post to TikTok.",
            font=("Arial", 10),
        ).pack(anchor="w", pady=(2, 12))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        self.tab_create  = ttk.Frame(notebook, padding=12)
        self.tab_ai      = ttk.Frame(notebook, padding=12)
        self.tab_queue   = ttk.Frame(notebook, padding=12)
        self.tab_license = ttk.Frame(notebook, padding=12)
        self.tab_log     = ttk.Frame(notebook, padding=12)

        notebook.add(self.tab_create,  text="Create Clips")
        notebook.add(self.tab_ai,      text="AI Settings")
        notebook.add(self.tab_queue,   text="Queue / TikTok")
        notebook.add(self.tab_license, text="License")
        notebook.add(self.tab_log,     text="Logs")

        self.build_create_tab()
        self.build_ai_tab()
        self.build_queue_tab()
        self.build_license_tab()
        self.build_log_tab()

    def build_create_tab(self):
        file_frame = ttk.LabelFrame(self.tab_create, text="1. Pick video", padding=12)
        file_frame.pack(fill="x", pady=(0, 10))
        ttk.Entry(file_frame, textvariable=self.video_path).pack(side="left", fill="x", expand=True)
        ttk.Button(file_frame, text="Browse", command=self.pick_video).pack(side="left", padx=(8, 0))

        section_frame = ttk.LabelFrame(self.tab_create, text="2. Choose section", padding=12)
        section_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(section_frame, text="Start").grid(row=0, column=0, sticky="w")
        ttk.Entry(section_frame, textvariable=self.start_time, width=18).grid(row=1, column=0, sticky="w", padx=(0, 18))
        ttk.Label(section_frame, text="End").grid(row=0, column=1, sticky="w")
        ttk.Entry(section_frame, textvariable=self.end_time, width=18).grid(row=1, column=1, sticky="w", padx=(0, 18))
        ttk.Label(section_frame, text="Format: HH:MM:SS, MM:SS, or seconds").grid(row=1, column=2, sticky="w")

        split_frame = ttk.LabelFrame(self.tab_create, text="3. Clip settings", padding=12)
        split_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(split_frame, text="Min clip seconds").grid(row=0, column=0, sticky="w")
        ttk.Entry(split_frame, textvariable=self.min_clip, width=12).grid(row=1, column=0, sticky="w", padx=(0, 18))
        ttk.Label(split_frame, text="Max clip seconds").grid(row=0, column=1, sticky="w")
        ttk.Entry(split_frame, textvariable=self.max_clip, width=12).grid(row=1, column=1, sticky="w", padx=(0, 18))
        ttk.Label(split_frame, text="Videos per folder").grid(row=0, column=2, sticky="w")
        ttk.Entry(split_frame, textvariable=self.group_size, width=12).grid(row=1, column=2, sticky="w", padx=(0, 18))
        ttk.Label(split_frame, text="Whisper model").grid(row=0, column=3, sticky="w")
        ttk.Combobox(
            split_frame, textvariable=self.model_name,
            values=["tiny", "base", "small", "medium", "large-v3"], width=14, state="readonly",
        ).grid(row=1, column=3, sticky="w")
        ttk.Checkbutton(
            split_frame, text="Copy original video into output folder", variable=self.copy_original,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(10, 0))

        output_frame = ttk.LabelFrame(self.tab_create, text="4. Save location", padding=12)
        output_frame.pack(fill="x", pady=(0, 10))
        ttk.Entry(output_frame, textvariable=self.output_folder).pack(side="left", fill="x", expand=True)
        ttk.Button(output_frame, text="Browse", command=self.pick_output_folder).pack(side="left", padx=(8, 0))

        action_frame = ttk.Frame(self.tab_create)
        action_frame.pack(fill="x", pady=(0, 10))
        self.run_button = ttk.Button(action_frame, text="Create TikTok Parts", command=self.start_processing)
        self.run_button.pack(side="left")
        ttk.Button(action_frame, text="Open Last Output", command=self.open_last_output).pack(side="left", padx=(8, 0))
        ttk.Button(action_frame, text="Save Settings", command=self.save_current_settings).pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(action_frame, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

        ttk.Label(
            self.tab_create,
            text="Output per group: part_1.mp4, part_2.mp4, part_3.mp4, caption.txt. Upload state stored in clipque_queue.sqlite3.",
            wraplength=900,
        ).pack(anchor="w")

    def build_ai_tab(self):
        gemini_frame = ttk.LabelFrame(self.tab_ai, text="Gemini", padding=12)
        gemini_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(gemini_frame, text="Gemini API key").grid(row=0, column=0, sticky="w")
        ttk.Entry(gemini_frame, textvariable=self.gemini_api_key, show="*", width=60).grid(row=1, column=0, sticky="w", padx=(0, 18))
        ttk.Label(gemini_frame, text="Gemini model").grid(row=0, column=1, sticky="w")
        ttk.Combobox(gemini_frame, textvariable=self.gemini_model_name, values=GEMINI_MODELS, width=24).grid(row=1, column=1, sticky="w")

        openrouter_frame = ttk.LabelFrame(self.tab_ai, text="OpenRouter fallback", padding=12)
        openrouter_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(openrouter_frame, text="OpenRouter API key").grid(row=0, column=0, sticky="w")
        ttk.Entry(openrouter_frame, textvariable=self.openrouter_api_key, show="*", width=60).grid(row=1, column=0, sticky="w", padx=(0, 18))
        ttk.Label(openrouter_frame, text="Fallback models, comma-separated").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(openrouter_frame, textvariable=self.openrouter_models).grid(row=3, column=0, columnspan=2, sticky="ew")
        openrouter_frame.columnconfigure(0, weight=1)

        hashtag_frame = ttk.LabelFrame(self.tab_ai, text="Caption / Hashtags", padding=12)
        hashtag_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(hashtag_frame, text="Base hashtags").pack(anchor="w")
        ttk.Entry(hashtag_frame, textvariable=self.base_hashtags).pack(fill="x")
        ttk.Label(
            hashtag_frame,
            text="Caption order: Gemini → OpenRouter free models → local fallback.",
            wraplength=850,
        ).pack(anchor="w", pady=(10, 0))

    def build_queue_tab(self):
        # ── TikTok connection panel ───────────────────────────────────────────
        conn_frame = ttk.LabelFrame(self.tab_queue, text="TikTok account", padding=12)
        conn_frame.pack(fill="x", pady=(0, 10))

        self.tiktok_status_label = ttk.Label(conn_frame, text="Checking…")
        self.tiktok_status_label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.connect_btn    = ttk.Button(conn_frame, text="Connect TikTok",    command=self.connect_tiktok)
        self.disconnect_btn = ttk.Button(conn_frame, text="Disconnect",         command=self.disconnect_tiktok, state="disabled")
        self.refresh_status_btn = ttk.Button(conn_frame, text="Refresh Status", command=self.refresh_tiktok_status)

        self.connect_btn.grid(row=1, column=0, padx=(0, 8))
        self.disconnect_btn.grid(row=1, column=1, padx=(0, 8))
        self.refresh_status_btn.grid(row=1, column=2)

        # ── Queue actions ─────────────────────────────────────────────────────
        action_frame = ttk.Frame(self.tab_queue)
        action_frame.pack(fill="x", pady=(0, 10))
        ttk.Button(action_frame, text="Refresh Queue",   command=self.refresh_queue).pack(side="left")
        ttk.Button(action_frame, text="Open Queue DB",   command=self.open_queue_db).pack(side="left", padx=(8, 0))
        ttk.Button(action_frame, text="Export CSV",      command=self.export_queue_csv).pack(side="left", padx=(8, 0))
        self.post_btn = ttk.Button(action_frame, text="Post Selected to TikTok", command=self.post_selected, state="disabled")
        self.post_btn.pack(side="left", padx=(8, 0))
        self.post_all_btn = ttk.Button(action_frame, text="Post All READY", command=self.post_all_ready, state="disabled")
        self.post_all_btn.pack(side="left", padx=(8, 0))

        # ── Queue table ───────────────────────────────────────────────────────
        columns = ("id", "status", "group", "part", "caption", "provider")
        self.queue_tree = ttk.Treeview(self.tab_queue, columns=columns, show="headings", height=15)
        for col in columns:
            self.queue_tree.heading(col, text=col.title())
        self.queue_tree.column("id",       width=55,  anchor="center")
        self.queue_tree.column("status",   width=110, anchor="center")
        self.queue_tree.column("group",    width=90,  anchor="center")
        self.queue_tree.column("part",     width=55,  anchor="center")
        self.queue_tree.column("caption",  width=530)
        self.queue_tree.column("provider", width=160)
        self.queue_tree.pack(fill="both", expand=True)
        self.queue_tree.tag_configure("UPLOADED",  background="#dcfce7")
        self.queue_tree.tag_configure("FAILED",    background="#fee2e2")
        self.queue_tree.tag_configure("UPLOADING", background="#fef9c3")

        ttk.Label(
            self.tab_queue,
            text="Select a row and click 'Post Selected' to post one clip, or use 'Post All READY' to queue everything.",
            wraplength=850,
        ).pack(anchor="w", pady=(8, 0))

    def build_license_tab(self):
        frame = ttk.LabelFrame(self.tab_license, text="License", padding=12)
        frame.pack(fill="x", pady=(0, 10))
        ttk.Label(frame, text="License key").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.license_key, width=60).pack(anchor="w", pady=(3, 8))
        ttk.Button(frame, text="Save / Validate License", command=self.validate_license).pack(anchor="w")
        self.license_status = ttk.Label(frame, text="")
        self.license_status.pack(anchor="w", pady=(10, 0))
        self.validate_license(show_popup=False)

        ttk.Label(
            self.tab_license,
            text="Backend validation coming soon. Local dev mode is active until a key is entered.",
            wraplength=850,
        ).pack(anchor="w")

    def build_log_tab(self):
        log_frame = ttk.LabelFrame(self.tab_log, text="Progress", padding=12)
        log_frame.pack(fill="both", expand=True)
        self.log_box = tk.Text(log_frame, height=20, wrap="word")
        self.log_box.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_box.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_box.configure(yscrollcommand=scrollbar.set)

    # ── Logging ───────────────────────────────────────────────────────────────
    def log(self, message: str):
        def write():
            self.log_box.insert("end", message + "\n")
            self.log_box.see("end")
        self.root.after(0, write)

    # ── Create-tab actions ────────────────────────────────────────────────────
    def pick_video(self):
        path = filedialog.askopenfilename(
            title="Pick a video",
            filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"), ("All files", "*.*")],
        )
        if not path:
            return
        video = Path(path)
        self.video_path.set(str(video))
        if not self.output_folder.get().strip():
            self.output_folder.set(str(video.parent))
        self.log(f"Selected video: {video.name}")
        try:
            check_ffmpeg()
            duration = get_video_duration(video)
            self.end_time.set(format_seconds(duration))
            self.log(f"Video duration: {format_seconds(duration)}")
        except Exception as exc:
            self.log(f"Could not auto-read duration: {exc}")

    def pick_output_folder(self):
        path = filedialog.askdirectory(title="Pick save location")
        if path:
            self.output_folder.set(path)

    def save_current_settings(self):
        save_settings({
            "whisper_model":    self.model_name.get(),
            "min_clip_seconds": self.min_clip.get(),
            "max_clip_seconds": self.max_clip.get(),
            "videos_per_folder": self.group_size.get(),
            "gemini_model":     self.gemini_model_name.get(),
            "openrouter_models": self.openrouter_models.get(),
            "base_hashtags":    self.base_hashtags.get(),
            "copy_original":    bool(self.copy_original.get()),
            "last_output_folder": self.output_folder.get(),
        })
        self.log("Settings saved.")

    def collect_config(self) -> ClipJobConfig:
        video  = Path(self.video_path.get().strip())
        output = Path(self.output_folder.get().strip()) if self.output_folder.get().strip() else video.parent
        openrouter_models = [m.strip() for m in self.openrouter_models.get().split(",") if m.strip()]
        return ClipJobConfig(
            video_path=video,
            output_folder=output,
            start_time=self.start_time.get(),
            end_time=self.end_time.get(),
            min_clip_seconds=int(self.min_clip.get().strip()),
            max_clip_seconds=int(self.max_clip.get().strip()),
            videos_per_folder=int(self.group_size.get().strip()),
            whisper_model=self.model_name.get(),
            copy_original=bool(self.copy_original.get()),
            gemini_api_key=self.gemini_api_key.get(),
            gemini_model=self.gemini_model_name.get(),
            openrouter_api_key=self.openrouter_api_key.get(),
            openrouter_models=openrouter_models,
            base_hashtags=self.base_hashtags.get(),
        )

    def start_processing(self):
        if self.is_running:
            return
        self.save_current_settings()
        self.is_running = True
        self.run_button.configure(state="disabled")
        self.progress.start(10)
        threading.Thread(target=self.process_video, daemon=True).start()

    def finish_processing(self):
        self.is_running = False
        self.progress.stop()
        self.run_button.configure(state="normal")

    def process_video(self):
        try:
            cfg    = self.collect_config()
            runner = ClipJobRunner(cfg, log_callback=self.log)
            result = runner.run()
            self.last_parent_folder = result.parent_folder
            self.last_db_path       = result.db_path
            self.log("")
            self.log(f"Done. Output: {result.parent_folder}")
            self.root.after(0, self.refresh_queue)
            messagebox.showinfo("Done", f"ClipQue output saved in:\n{result.parent_folder}")
        except Exception as exc:
            self.log(f"ERROR: {exc}")
            messagebox.showerror("Error", str(exc))
        finally:
            self.root.after(0, self.finish_processing)

    def open_last_output(self):
        if not self.last_parent_folder or not self.last_parent_folder.exists():
            messagebox.showwarning("Not found", "No output has been created yet.")
            return
        open_path(self.last_parent_folder)

    # ── Queue-tab actions ─────────────────────────────────────────────────────
    def queue_db(self) -> ClipQueueDB | None:
        if self.last_db_path and self.last_db_path.exists():
            return ClipQueueDB(self.last_db_path)
        return None

    def refresh_queue(self):
        for item in self.queue_tree.get_children():
            self.queue_tree.delete(item)
        db = self.queue_db()
        if not db:
            return
        for row in db.list_clips(limit=500):
            status = row.get("status", "")
            tag    = status if status in ("UPLOADED", "FAILED", "UPLOADING") else ""
            self.queue_tree.insert("", "end", values=(
                row.get("id"),
                status,
                row.get("group_name"),
                row.get("part_number"),
                row.get("caption"),
                row.get("caption_provider"),
            ), tags=(tag,) if tag else ())

    def open_queue_db(self):
        if not self.last_db_path or not self.last_db_path.exists():
            messagebox.showwarning("Not found", "No SQLite queue has been created yet.")
            return
        open_path(self.last_db_path.parent)

    def export_queue_csv(self):
        db = self.queue_db()
        if not db:
            messagebox.showwarning("Not found", "No SQLite queue has been created yet.")
            return
        export_path = self.last_db_path.parent / "clipque_upload_queue_export.csv"
        db.export_csv(export_path)
        self.log(f"Queue exported: {export_path}")
        messagebox.showinfo("Exported", f"Queue exported to:\n{export_path}")

    # ── TikTok connection ─────────────────────────────────────────────────────
    def _restore_tiktok_state(self):
        """Called once on startup to check keychain for an existing token."""
        self.refresh_tiktok_status(silent=True)

    def refresh_tiktok_status(self, silent: bool = False):
        def check():
            state = self.tiktok_client.auth_status()
            def update():
                self.tiktok_status_label.configure(text=state.message)
                if state.connected:
                    self.connect_btn.configure(state="disabled")
                    self.disconnect_btn.configure(state="normal")
                    self.post_btn.configure(state="normal")
                    self.post_all_btn.configure(state="normal")
                else:
                    self.connect_btn.configure(state="normal")
                    self.disconnect_btn.configure(state="disabled")
                    self.post_btn.configure(state="disabled")
                    self.post_all_btn.configure(state="disabled")
                if not silent:
                    self.log(f"TikTok status: {state.message}")
            self.root.after(0, update)
        threading.Thread(target=check, daemon=True).start()

    def connect_tiktok(self):
        self.connect_btn.configure(state="disabled")
        self.tiktok_status_label.configure(text="Opening TikTok login in browser…")
        self.log("Starting TikTok OAuth flow…")

        def run_oauth():
            result = self.oauth_flow.start_login(timeout_seconds=120)
            def on_done():
                if result.success:
                    self.log(f"TikTok connected. open_id={result.open_id}")
                    self.refresh_tiktok_status()
                else:
                    self.log(f"TikTok login failed: {result.message}")
                    self.tiktok_status_label.configure(text=f"Login failed: {result.message}")
                    self.connect_btn.configure(state="normal")
                    messagebox.showerror("TikTok Login Failed", result.message)
            self.root.after(0, on_done)

        threading.Thread(target=run_oauth, daemon=True).start()

    def disconnect_tiktok(self):
        if not messagebox.askyesno("Disconnect TikTok", "Remove saved TikTok tokens from this computer?"):
            return
        self.tiktok_client.disconnect()
        self.log("TikTok account disconnected.")
        self.refresh_tiktok_status()

    # ── TikTok posting ────────────────────────────────────────────────────────
    def post_selected(self):
        selected = self.queue_tree.selection()
        if not selected:
            messagebox.showwarning("Nothing selected", "Select a clip in the queue first.")
            return
        item    = selected[0]
        values  = self.queue_tree.item(item, "values")
        clip_id = int(values[0])
        status  = values[1]
        if status == "UPLOADED":
            if not messagebox.askyesno("Already uploaded", "This clip is marked UPLOADED. Post again?"):
                return
        db = self.queue_db()
        if not db:
            messagebox.showerror("Error", "No queue database found.")
            return
        rows = [r for r in db.list_clips(limit=1000) if r["id"] == clip_id]
        if not rows:
            messagebox.showerror("Error", "Could not find that clip in the database.")
            return
        self._post_clips([rows[0]], db)

    def post_all_ready(self):
        db = self.queue_db()
        if not db:
            messagebox.showerror("Error", "No queue database found.")
            return
        ready = [r for r in db.list_clips(limit=1000) if r["status"] == "READY"]
        if not ready:
            messagebox.showinfo("Nothing to post", "No clips with status READY in the queue.")
            return
        if not messagebox.askyesno("Post all READY", f"Post {len(ready)} clip(s) to TikTok?"):
            return
        self._post_clips(ready, db)

    def _post_clips(self, clips: list[dict], db: ClipQueueDB):
        if self.is_posting:
            messagebox.showwarning("Busy", "A post is already in progress. Wait for it to finish.")
            return
        self.is_posting = True
        self.post_btn.configure(state="disabled")
        self.post_all_btn.configure(state="disabled")

        def run():
            for clip in clips:
                clip_id = clip["id"]
                self.log(f"Posting clip {clip_id} ({clip['group_name']} part {clip['part_number']})…")
                db.update_status(clip_id, "UPLOADING")
                self.root.after(0, self.refresh_queue)
                try:
                    post_id = self.tiktok_client.upload_clip(clip, log_callback=self.log)
                    db.update_status(clip_id, "UPLOADED", post_id=post_id)
                    self.log(f"Clip {clip_id} posted. TikTok ID: {post_id}")
                except Exception as exc:
                    db.update_status(clip_id, "FAILED", last_error=str(exc))
                    self.log(f"Clip {clip_id} failed: {exc}")
                self.root.after(0, self.refresh_queue)

            def done():
                self.is_posting = False
                state = self.tiktok_client.auth_status()
                if state.connected:
                    self.post_btn.configure(state="normal")
                    self.post_all_btn.configure(state="normal")
                messagebox.showinfo("Done", "Posting complete. Check queue for results.")
            self.root.after(0, done)

        threading.Thread(target=run, daemon=True).start()

    # ── License tab ───────────────────────────────────────────────────────────
    def validate_license(self, show_popup: bool = True):
        key    = self.license_key.get().strip()
        self.license_manager.save_key(key)
        status = self.license_manager.validate(key)
        text   = f"{status.mode}: {status.message}  Machine ID: {self.license_manager.machine_id()}"
        self.license_status.configure(text=text)
        if show_popup:
            messagebox.showinfo("License", text)


def main():
    root = tk.Tk()
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    ClipQueApp(root)
    root.mainloop()
