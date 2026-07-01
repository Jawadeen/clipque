from __future__ import annotations

"""
ClipQue Post All READY order fix.

Put this file in your ClipQue project root, next to main.py, then run:

    python fix_post_all_order.py

It patches clipque/app.py so "Post All READY" posts clips in natural story order:
    group_01 part 1
    group_01 part 2
    group_01 part 3
    group_02 part 1
    ...

It also sorts the Queue table the same way when you click Refresh Queue.
A backup is created as clipque/app.py.bak_order_fix.
"""

from pathlib import Path
import re
import sys

ROOT = Path.cwd()
APP_PATH = ROOT / "clipque" / "app.py"
BACKUP_PATH = ROOT / "clipque" / "app.py.bak_order_fix"


def die(message: str) -> None:
    print(f"ERROR: {message}")
    sys.exit(1)


def find_block(text: str, start_marker: str, end_marker: str) -> tuple[int, int]:
    start = text.find(start_marker)
    if start == -1:
        die(f"Could not find block start: {start_marker!r}")
    end = text.find(end_marker, start)
    if end == -1:
        die(f"Could not find block end after {start_marker!r}: {end_marker!r}")
    return start, end


NATURAL_ORDER_METHOD = '''    def _clip_sort_key(self, row: dict):
        """Sort clips in the order viewers should see them.

        Handles group names like:
        - group_01
        - group_1
        - Group 01
        - any name containing a number

        Fallbacks keep sorting stable if a value is missing.
        """
        group_name = str(row.get("group_name", "") or "")
        match = re.search(r"(\\d+)", group_name)
        group_num = int(match.group(1)) if match else 999999

        try:
            part_num = int(row.get("part_number", 999999) or 999999)
        except (TypeError, ValueError):
            part_num = 999999

        try:
            row_id = int(row.get("id", 999999) or 999999)
        except (TypeError, ValueError):
            row_id = 999999

        return (group_num, part_num, row_id)

'''

NEW_POST_ALL_READY = '''    def post_all_ready(self):
        db = self.queue_db()
        if not db:
            messagebox.showerror("Error", "No queue database found.")
            return

        ready = [r for r in db.list_clips(limit=1000) if r["status"] == "READY"]
        ready = sorted(ready, key=self._clip_sort_key)

        if not ready:
            messagebox.showinfo("Nothing to post", "No clips with status READY in the queue.")
            return

        first = ready[0]
        last = ready[-1]
        order_msg = (
            f"Post {len(ready)} clip(s) to TikTok in order?\\n\\n"
            f"First: {first.get('group_name')} part {first.get('part_number')}\\n"
            f"Last: {last.get('group_name')} part {last.get('part_number')}"
        )

        if not messagebox.askyesno("Post all READY", order_msg):
            return

        self._post_clips(ready, db)

'''


def patch_import_re(text: str) -> str:
    if "import re" in text:
        return text

    # Normal formatted file.
    if "import threading\n" in text:
        return text.replace("import threading\n", "import threading\nimport re\n", 1)

    # Defensive fallback for weird/minified copies.
    if "import threading " in text:
        return text.replace("import threading ", "import threading import re ", 1)

    die("Could not insert import re")


def patch_post_all_ready(text: str) -> str:
    start, end = find_block(text, "    def post_all_ready(self):", "    def _post_clips")
    return text[:start] + NEW_POST_ALL_READY + text[end:]


def patch_sort_method(text: str) -> str:
    if "def _clip_sort_key" in text:
        return text

    start, _ = find_block(text, "    def post_all_ready(self):", "    def _post_clips")
    return text[:start] + NATURAL_ORDER_METHOD + text[start:]


def patch_refresh_queue_sort(text: str) -> str:
    old = "for row in db.list_clips(limit=500):"
    new = "for row in sorted(db.list_clips(limit=500), key=self._clip_sort_key):"
    if new in text:
        return text
    if old not in text:
        print("WARNING: Could not patch Refresh Queue display order. Post All READY order was still fixed.")
        return text
    return text.replace(old, new, 1)


def main() -> None:
    if not APP_PATH.exists():
        die(f"Run this from your ClipQue project root. Missing: {APP_PATH}")

    text = APP_PATH.read_text(encoding="utf-8")

    if not BACKUP_PATH.exists():
        BACKUP_PATH.write_text(text, encoding="utf-8")
        print(f"Backup created: {BACKUP_PATH}")
    else:
        print(f"Backup already exists: {BACKUP_PATH}")

    text = patch_import_re(text)
    text = patch_sort_method(text)
    text = patch_post_all_ready(text)
    text = patch_refresh_queue_sort(text)

    APP_PATH.write_text(text, encoding="utf-8")
    print("Done. Post All READY will now start from the first clip in natural order.")
    print("Restart ClipQue, click Refresh Queue, then click Post All READY.")


if __name__ == "__main__":
    main()
