from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from .tiktok_client import TikTokTokenStore

# ─── TikTok app credentials ───────────────────────────────────────────────────
TIKTOK_CLIENT_KEY    = "sbaw37dfc686u0hjkj"
TIKTOK_CLIENT_SECRET = "VwDPmcuESsU5F8zmnsFGGGQrq4jR3Yay"

TIKTOK_AUTH_URL   = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL  = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_SCOPES     = "user.info.basic,video.upload"

# Sandbox:    http://127.0.0.1:8765/callback
# Production: https://clipque.vercel.app/api/tiktok-callback
TIKTOK_REDIRECT_URI = "http://127.0.0.1:8765/callback"

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT  = 8765

_ERROR_MESSAGES: dict[str, str] = {
    "access_denied":         "You declined the TikTok permission request.",
    "missing_code":          "TikTok did not return an authorization code.",
    "server_misconfigured":  "Server credentials are missing.",
    "token_exchange_failed": "Could not exchange authorization code for a token.",
    "unexpected_error":      "An unexpected error occurred during login.",
    "state_mismatch":        "Security check failed (state mismatch). Please try again.",
}


def _friendly(code: str) -> str:
    return _ERROR_MESSAGES.get(code, f"TikTok error: {code}")


def _generate_pkce() -> tuple[str, str]:
    code_verifier  = secrets.token_urlsafe(64)
    digest         = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


@dataclass
class OAuthResult:
    success: bool
    open_id: str = ""
    message: str = ""


@dataclass
class _ServerState:
    token_store:    TikTokTokenStore
    expected_state: str
    code_verifier:  str
    done:           threading.Event = field(default_factory=threading.Event)
    result:         OAuthResult     = field(default_factory=lambda: OAuthResult(False, message="Login timed out."))


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "ClipQueOAuth/1.0"

    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        state: _ServerState = self.server.state  # type: ignore[attr-defined]

        code           = params.get("code",  [""])[0]
        error          = params.get("error", [""])[0]
        returned_state = params.get("state", [""])[0]

        # Production path: Vercel already exchanged the code
        access_token  = params.get("access_token",  [""])[0]
        open_id       = params.get("open_id",       [""])[0]
        refresh_token = params.get("refresh_token", [""])[0]

        if error:
            self._finish(state, OAuthResult(False, message=_friendly(error)), ok=False, msg=_friendly(error))
            return

        if returned_state != state.expected_state:
            msg = _friendly("state_mismatch")
            self._finish(state, OAuthResult(False, message=msg), ok=False, msg=msg)
            return

        if access_token and open_id:
            self._save(state, open_id, access_token, refresh_token)
            return

        if code:
            data, err_detail = self._exchange(code, state.code_verifier)
            if data:
                self._save(state, data.get("open_id", ""), data.get("access_token", ""), data.get("refresh_token", ""))
                return
            # Show the real TikTok error in the app
            msg = f"Token exchange failed: {err_detail}"
            self._finish(state, OAuthResult(False, message=msg), ok=False, msg=msg)
            return

        msg = _friendly("missing_code")
        self._finish(state, OAuthResult(False, message=msg), ok=False, msg=msg)

    def _exchange(self, code: str, code_verifier: str) -> tuple[dict | None, str]:
        """Returns (token_data_or_None, error_detail_string)."""
        body = urllib.parse.urlencode({
            "client_key":    TIKTOK_CLIENT_KEY,
            "client_secret": TIKTOK_CLIENT_SECRET,
            "code":          code,
            "grant_type":    "authorization_code",
            "redirect_uri":  TIKTOK_REDIRECT_URI,
            "code_verifier": code_verifier,
        }).encode("utf-8")
        req = urllib.request.Request(
            TIKTOK_TOKEN_URL, data=body, method="POST",
            headers={
                "Content-Type":  "application/x-www-form-urlencoded",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("access_token"):
                return data, ""
            detail = data.get("error_description") or data.get("error") or str(data)
            return None, f"TikTok said: {detail}"
        except urllib.error.HTTPError as exc:
            try:
                body_text = exc.read().decode("utf-8")
            except Exception:
                body_text = str(exc)
            return None, f"HTTP {exc.code}: {body_text[:400]}"
        except Exception as exc:
            return None, str(exc)

    def _save(self, state: _ServerState, open_id: str, access_token: str, refresh_token: str) -> None:
        store = state.token_store
        store.save_access_token(open_id, access_token)
        if refresh_token:
            store.save_refresh_token(open_id, refresh_token)
        store.save_open_id(open_id)
        self._finish(
            state,
            OAuthResult(True, open_id=open_id, message="Connected."),
            ok=True,
            msg="TikTok account connected. You can close this tab.",
        )

    def _finish(self, state: _ServerState, result: OAuthResult, ok: bool, msg: str) -> None:
        color = "#16a34a" if ok else "#dc2626"
        html  = (
            f"<html><body style='font-family:sans-serif;text-align:center;margin-top:80px;'>"
            f"<h2 style='color:{color}'>{msg}</h2>"
            f"</body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)
        state.result = result
        state.done.set()


class TikTokOAuthFlow:
    def __init__(self, token_store: TikTokTokenStore | None = None):
        self.token_store = token_store or TikTokTokenStore()

    def start_login(self, timeout_seconds: int = 120) -> OAuthResult:
        state_value                   = secrets.token_urlsafe(16)
        code_verifier, code_challenge = _generate_pkce()

        server_state = _ServerState(
            token_store    = self.token_store,
            expected_state = state_value,
            code_verifier  = code_verifier,
        )

        httpd = HTTPServer((LOCAL_HOST, LOCAL_PORT), _CallbackHandler)
        httpd.state = server_state  # type: ignore[attr-defined]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        auth_params = {
            "client_key":            TIKTOK_CLIENT_KEY,
            "scope":                 TIKTOK_SCOPES,
            "response_type":         "code",
            "redirect_uri":          TIKTOK_REDIRECT_URI,
            "state":                 state_value,
            "code_challenge":        code_challenge,
            "code_challenge_method": "S256",
        }
        webbrowser.open(f"{TIKTOK_AUTH_URL}?{urlencode(auth_params)}")

        server_state.done.wait(timeout=timeout_seconds)
        result = server_state.result
        httpd.shutdown()
        httpd.server_close()
        return result