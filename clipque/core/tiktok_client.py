from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

try:
    import keyring
except Exception:
    keyring = None

SERVICE_NAME = "ClipQue TikTok"

TIKTOK_USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
TIKTOK_CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
TIKTOK_VIDEO_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_VIDEO_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

MIN_CHUNK_SIZE_BYTES = 5 * 1024 * 1024  # TikTok minimum normal chunk size
SINGLE_CHUNK_LIMIT_BYTES = 64 * 1024 * 1024  # TikTok maximum normal chunk size
MULTI_CHUNK_SIZE_BYTES = 32 * 1024 * 1024  # Safe chunk size for multi-part uploads

# TikTok's Direct Post initialize endpoint is limited to 6 requests per minute
# per user access token. One request every 12 seconds gives a safety buffer.
TIKTOK_DIRECT_POST_MIN_GAP_SECONDS = 12.0
TIKTOK_CREATOR_INFO_CACHE_SECONDS = 10 * 60
TIKTOK_429_DEFAULT_WAIT_SECONDS = 70.0
TIKTOK_MAX_429_RETRIES = 4


@dataclass
class TikTokAuthState:
    connected: bool
    open_id: str = ""
    username_hint: str = ""
    message: str = ""


class TikTokTokenStore:
    """Stores TikTok tokens in the OS credential vault.

    Windows: Credential Manager
    macOS: Keychain
    Linux: Secret Service
    Nothing is written to plaintext files.
    """

    def available(self) -> bool:
        return keyring is not None

    # ── Access token ──────────────────────────────────────────────────────
    def save_access_token(self, open_id: str, token: str) -> None:
        self._require_keyring()
        keyring.set_password(SERVICE_NAME, f"{open_id}:access", token)

    def load_access_token(self, open_id: str) -> str:
        if keyring is None:
            return ""
        return keyring.get_password(SERVICE_NAME, f"{open_id}:access") or ""

    def delete_access_token(self, open_id: str) -> None:
        self._try_delete(f"{open_id}:access")

    # ── Refresh token ─────────────────────────────────────────────────────
    def save_refresh_token(self, open_id: str, token: str) -> None:
        self._require_keyring()
        keyring.set_password(SERVICE_NAME, f"{open_id}:refresh", token)

    def load_refresh_token(self, open_id: str) -> str:
        if keyring is None:
            return ""
        return keyring.get_password(SERVICE_NAME, f"{open_id}:refresh") or ""

    # ── Connected open_id ─────────────────────────────────────────────────
    def save_open_id(self, open_id: str) -> None:
        self._require_keyring()
        keyring.set_password(SERVICE_NAME, "active_open_id", open_id)

    def load_open_id(self) -> str:
        if keyring is None:
            return ""
        return keyring.get_password(SERVICE_NAME, "active_open_id") or ""

    def disconnect(self) -> None:
        open_id = self.load_open_id()
        if open_id:
            self._try_delete(f"{open_id}:access")
            self._try_delete(f"{open_id}:refresh")
        self._try_delete("active_open_id")

    # ── Internals ─────────────────────────────────────────────────────────
    def _require_keyring(self) -> None:
        if keyring is None:
            raise RuntimeError("keyring is not installed. Run: pip install keyring")

    def _try_delete(self, key: str) -> None:
        if keyring is None:
            return
        try:
            keyring.delete_password(SERVICE_NAME, key)
        except Exception:
            pass


class TikTokClient:
    """TikTok Content Posting API client.

    Covers:
    - auth_status() — check if a token is stored and verify it against TikTok
    - upload_clip() — query creator info, initialize Direct Post, upload chunks, poll status
    - refresh_token() — renew an expired access token using the saved refresh token
    - disconnect() — wipe all stored tokens

    Rate-limit fix:
    TikTok Direct Post init is limited to 6 requests/minute per access token.
    This client now spaces init calls, caches creator_info, and retries 429s.
    """

    def __init__(self, token_store: TikTokTokenStore | None = None):
        self.token_store = token_store or TikTokTokenStore()
        self._last_direct_post_init_at = 0.0
        self._creator_info_cache: dict | None = None
        self._creator_info_cache_at = 0.0

    # ── Auth status ───────────────────────────────────────────────────────
    def auth_status(self) -> TikTokAuthState:
        if not self.token_store.available():
            return TikTokAuthState(False, message="keyring is not installed. Run: pip install keyring")

        open_id = self.token_store.load_open_id()
        if not open_id:
            return TikTokAuthState(False, message="No TikTok account connected. Click 'Connect TikTok' to log in.")

        token = self.token_store.load_access_token(open_id)
        if not token:
            return TikTokAuthState(False, open_id=open_id, message="Token missing. Please reconnect.")

        try:
            info = self._get_user_info(token)
            display = info.get("data", {}).get("user", {}).get("display_name", "")
            hint = f"@{display}" if display else f"open_id: {open_id[:12]}…"
            return TikTokAuthState(True, open_id=open_id, username_hint=hint, message=f"Connected as {hint}")
        except Exception as exc:
            return TikTokAuthState(True, open_id=open_id, message=f"Connected (could not verify: {exc})")

    # ── Upload a clip ─────────────────────────────────────────────────────
    def upload_clip(self, clip_row: dict, log_callback=None) -> str:
        """Upload one queue row to TikTok using Direct Post.

        Returns the publish_id, which the app stores as tiktok_post_id.
        Raises RuntimeError on failure.
        """

        def log(msg: str) -> None:
            if log_callback:
                log_callback(msg)

        open_id = self.token_store.load_open_id()
        if not open_id:
            raise RuntimeError("No TikTok account connected.")

        token = self.token_store.load_access_token(open_id)
        if not token:
            raise RuntimeError("TikTok access token missing. Please reconnect.")

        video_file = Path(clip_row["video_file"])
        if not video_file.exists():
            raise RuntimeError(f"Video file not found: {video_file}")

        caption = str(clip_row.get("caption", "") or "")
        file_size = video_file.stat().st_size
        if file_size <= 0:
            raise RuntimeError(f"Video file is empty: {video_file}")

        creator_info = self.query_creator_info(token, log_callback=log)
        privacy_level = self._choose_privacy_level(creator_info)
        log(f"Using TikTok privacy level: {privacy_level}")

        chunk_size, total_chunk_count = self._calculate_upload_plan(file_size)
        log(
            f"TikTok upload plan: video_size={file_size} bytes, "
            f"chunk_size={chunk_size} bytes, total_chunk_count={total_chunk_count}"
        )

        self._wait_for_direct_post_slot(log)
        log(f"Initialising TikTok upload for {video_file.name}…")

        init_body = json.dumps(
            {
                "post_info": {
                    "title": caption[:2200],
                    "privacy_level": privacy_level,
                    "disable_comment": False,
                    "disable_duet": False,
                    "disable_stitch": False,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": total_chunk_count,
                },
            }
        ).encode("utf-8")

        init_resp = self._api_post(
            TIKTOK_VIDEO_INIT_URL,
            token=token,
            body=init_body,
            content_type="application/json; charset=utf-8",
            log_callback=log,
            retry_429=True,
            update_init_timestamp=True,
        )

        upload_url = init_resp["data"]["upload_url"]
        publish_id = init_resp["data"]["publish_id"]
        log(f"Upload initialised. publish_id={publish_id}")

        with video_file.open("rb") as fh:
            for chunk_index in range(total_chunk_count):
                chunk_start = chunk_index * chunk_size
                if chunk_index == total_chunk_count - 1:
                    # TikTok's rule is unusual: total_chunk_count is floor(video_size / chunk_size),
                    # and the final chunk absorbs all trailing bytes. Do not use ceil().
                    bytes_to_read = file_size - chunk_start
                else:
                    bytes_to_read = chunk_size

                chunk_data = fh.read(bytes_to_read)
                if len(chunk_data) != bytes_to_read:
                    raise RuntimeError(
                        f"Could not read expected TikTok chunk bytes: "
                        f"wanted {bytes_to_read}, got {len(chunk_data)}"
                    )

                chunk_end = chunk_start + len(chunk_data) - 1

                log(
                    f"Uploading chunk {chunk_index + 1}/{total_chunk_count} "
                    f"bytes {chunk_start}-{chunk_end}/{file_size}…"
                )

                req = urllib.request.Request(upload_url, data=chunk_data, method="PUT")
                req.add_header("Content-Type", "video/mp4")
                req.add_header("Content-Length", str(len(chunk_data)))
                req.add_header("Content-Range", f"bytes {chunk_start}-{chunk_end}/{file_size}")

                try:
                    with urllib.request.urlopen(req, timeout=120):
                        pass
                except urllib.error.HTTPError as exc:
                    try:
                        detail = exc.read().decode("utf-8")[:500]
                    except Exception:
                        detail = exc.reason
                    raise RuntimeError(f"Chunk upload failed ({exc.code}): {detail}") from exc

        log("All chunks uploaded. Waiting for TikTok to process…")

        for attempt in range(20):
            time.sleep(5)
            status_body = json.dumps({"publish_id": publish_id}).encode("utf-8")
            status_resp = self._api_post(
                TIKTOK_VIDEO_STATUS_URL,
                token=token,
                body=status_body,
                content_type="application/json; charset=utf-8",
                log_callback=log,
                retry_429=True,
            )

            data = status_resp.get("data", {})
            status = data.get("status", "")
            log(f"Publish status: {status} (check {attempt + 1}/20)")

            if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                log(f"Published successfully. publish_id={publish_id}")
                return publish_id

            if status in ("FAILED", "PUBLISH_FAILED"):
                fail_code = data.get("fail_reason") or data.get("fail_code") or "unknown"
                raise RuntimeError(f"TikTok publish failed: {fail_code}")

        raise RuntimeError("Timed out waiting for TikTok to publish the video.")

    def query_creator_info(self, token: str, log_callback=None) -> dict:
        now = time.monotonic()
        if self._creator_info_cache and (now - self._creator_info_cache_at) < TIKTOK_CREATOR_INFO_CACHE_SECONDS:
            if log_callback:
                log_callback("Using cached TikTok creator info…")
            return self._creator_info_cache

        if log_callback:
            log_callback("Querying TikTok creator info…")

        data = self._api_post(
            TIKTOK_CREATOR_INFO_URL,
            token=token,
            body=b"{}",
            content_type="application/json; charset=utf-8",
            log_callback=log_callback,
            retry_429=True,
        )
        self._creator_info_cache = data
        self._creator_info_cache_at = time.monotonic()
        return data

    @staticmethod
    def _choose_privacy_level(creator_info: dict) -> str:
        options = creator_info.get("data", {}).get("privacy_level_options", []) or []
        if "SELF_ONLY" in options:
            return "SELF_ONLY"
        if options:
            return options[0]
        return "SELF_ONLY"

    @staticmethod
    def _calculate_upload_plan(file_size: int) -> tuple[int, int]:
        """Return (chunk_size, total_chunk_count) using TikTok's exact rules.

        TikTok's Content Posting API does NOT want ceil(video_size / chunk_size).
        It wants total_chunk_count to be floor(video_size / chunk_size), with the
        last uploaded chunk containing chunk_size plus any trailing bytes.

        For files up to 64 MiB we use a single whole-file upload. For larger
        files, 32 MiB chunks keep every normal chunk within TikTok's 5–64 MB
        range and keep the final absorbed chunk safely below 128 MB.
        """
        if file_size <= 0:
            raise RuntimeError("Video file is empty.")

        if file_size <= SINGLE_CHUNK_LIMIT_BYTES:
            return file_size, 1

        chunk_size = MULTI_CHUNK_SIZE_BYTES
        total_chunk_count = file_size // chunk_size  # TikTok requires floor(), not ceil()

        if total_chunk_count < 2:
            # Defensive fallback for any future constant changes. Videos greater
            # than 64 MB must be uploaded in multiple chunks.
            chunk_size = file_size // 2
            total_chunk_count = 2

        if total_chunk_count > 1000:
            raise RuntimeError(
                f"Video needs {total_chunk_count} chunks, but TikTok allows a maximum of 1000."
            )

        final_chunk_size = file_size - ((total_chunk_count - 1) * chunk_size)
        if not (MIN_CHUNK_SIZE_BYTES <= chunk_size <= SINGLE_CHUNK_LIMIT_BYTES):
            raise RuntimeError(f"Calculated invalid TikTok chunk size: {chunk_size}")
        if final_chunk_size > 128 * 1024 * 1024:
            raise RuntimeError(f"Calculated final TikTok chunk is too large: {final_chunk_size}")

        return chunk_size, total_chunk_count

    # ── Token refresh ─────────────────────────────────────────────────────
    def refresh_token(self, client_key: str, client_secret: str) -> bool:
        """Exchange the stored refresh token for a new access token."""
        from .oauth import TIKTOK_TOKEN_URL  # local import avoids circular import

        open_id = self.token_store.load_open_id()
        if not open_id:
            return False

        refresh = self.token_store.load_refresh_token(open_id)
        if not refresh:
            return False

        body = urllib.parse.urlencode(
            {
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            TIKTOK_TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            new_token = data.get("access_token", "")
            new_refresh = data.get("refresh_token", "")
            if not new_token:
                return False

            self.token_store.save_access_token(open_id, new_token)
            if new_refresh:
                self.token_store.save_refresh_token(open_id, new_refresh)
            return True
        except Exception:
            return False

    # ── Disconnect ────────────────────────────────────────────────────────
    def disconnect(self) -> None:
        self.token_store.disconnect()

    # ── Rate limit helpers ────────────────────────────────────────────────
    def _wait_for_direct_post_slot(self, log_callback=None) -> None:
        elapsed = time.monotonic() - self._last_direct_post_init_at
        wait_for = TIKTOK_DIRECT_POST_MIN_GAP_SECONDS - elapsed
        if wait_for > 0:
            if log_callback:
                log_callback(f"TikTok rate-limit guard: waiting {wait_for:.1f}s before next upload init…")
            time.sleep(wait_for)

    @staticmethod
    def _retry_after_seconds(exc: urllib.error.HTTPError) -> float:
        header = None
        try:
            header = exc.headers.get("Retry-After")
        except Exception:
            header = None

        if header:
            try:
                return max(1.0, float(header))
            except ValueError:
                pass

        return TIKTOK_429_DEFAULT_WAIT_SECONDS + random.uniform(0, 5)

    # ── Internals ─────────────────────────────────────────────────────────
    def _api_post(
        self,
        url: str,
        token: str,
        body: bytes,
        content_type: str,
        log_callback=None,
        retry_429: bool = True,
        update_init_timestamp: bool = False,
    ) -> dict:
        for attempt in range(TIKTOK_MAX_429_RETRIES + 1):
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Authorization", f"Bearer {token}")
            req.add_header("Content-Type", content_type)
            req.add_header("Content-Length", str(len(body)))

            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                if update_init_timestamp:
                    self._last_direct_post_init_at = time.monotonic()

                err = data.get("error", {})
                if isinstance(err, dict) and err.get("code", "ok") not in ("ok", ""):
                    code = err.get("code", "")
                    message = err.get("message", "")
                    if code == "rate_limit_exceeded" and retry_429 and attempt < TIKTOK_MAX_429_RETRIES:
                        wait_for = TIKTOK_429_DEFAULT_WAIT_SECONDS + random.uniform(0, 5)
                        if log_callback:
                            log_callback(
                                f"TikTok rate limit hit. Waiting {wait_for:.1f}s then retrying "
                                f"({attempt + 1}/{TIKTOK_MAX_429_RETRIES})…"
                            )
                        time.sleep(wait_for)
                        continue
                    raise RuntimeError(f"TikTok API: {code} — {message}")

                return data

            except urllib.error.HTTPError as exc:
                try:
                    detail = exc.read().decode("utf-8")[:500]
                except Exception:
                    detail = str(exc)

                if exc.code == 429 and retry_429 and attempt < TIKTOK_MAX_429_RETRIES:
                    wait_for = self._retry_after_seconds(exc)
                    if log_callback:
                        log_callback(
                            f"TikTok API rate limit reached. Waiting {wait_for:.1f}s then retrying "
                            f"({attempt + 1}/{TIKTOK_MAX_429_RETRIES})…"
                        )
                    time.sleep(wait_for)
                    continue

                raise RuntimeError(f"TikTok API error {exc.code}: {detail}") from exc

        raise RuntimeError("TikTok API rate limit kept happening after all retries. Try again later.")

    def _get_user_info(self, token: str) -> dict:
        req = urllib.request.Request(
            f"{TIKTOK_USER_INFO_URL}?fields=display_name,open_id",
            method="GET",
        )
        req.add_header("Authorization", f"Bearer {token}")

        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
