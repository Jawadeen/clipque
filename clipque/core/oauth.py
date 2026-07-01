from __future__ import annotations

import hashlib
import json
import os
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
# For local sandbox testing, env vars are preferred. The fallback values are kept
# from the current repo so this drop-in patch does not break your local test flow.
# Rotate the secret in TikTok Developer Portal before public release.
TIKTOK_CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "sbaw37dfc686u0hjkj")
TIKTOK_CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "VwDPmcuESsU5F8zmnsFGGGQrq4jR3Yay")

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

# Direct Post uses /v2/post/publish/video/init/, so the needed scope is
# video.publish, not video.upload.
TIKTOK_SCOPES = "user.info.basic,video.publish"

# Sandbox: http://127.0.0.1:8765/callback
# Production: https://clipque.vercel.app/api/tiktok-callback
TIKTOK_REDIRECT_URI = os.environ.get("TIKTOK_REDIRECT_URI", "http://127.0.0.1:8765/callback")

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8765

_ERROR_MESSAGES: dict[str, str] = {
    "access_denied": "You declined the TikTok permission request.",
    "missing_code": "TikTok did not return an authorization code.",
    "server_misconfigured": "TikTok client key/secret is missing.",
    "token_exchange_failed": "Could not exchange authorization code for a token.",
    "unexpected_error": "An unexpected error occurred during login.",
    "state_mismatch": "Security check failed (state mismatch). Please try again.",
}

_UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"


def _friendly(code: str) -> str:
    return _ERROR_MESSAGES.get(code, f"TikTok error: {code}")


def _generate_pkce() -> tuple[str, str]:
    """Generate TikTok Desktop Login PKCE values.

    Important: TikTok Desktop Login currently expects the S256 challenge to be
    SHA256(verifier) encoded as HEX, not normal OAuth base64url.
    """
    code_verifier = "".join(secrets.choice(_UNRESERVED) for _ in range(64))
    code_challenge = hashlib.sha256(code_verifier.encode("ascii")).hexdigest()
    return code_verifier, code_challenge


@dataclass
class OAuthResult:
    success: bool
    open_id: str = ""
    message: str = ""


@dataclass
class _ServerState:
    token_store: TikTokTokenStore
    expected_state: str
    code_verifier: str
    done: threading.Event = field(default_factory=threading.Event)
    result: OAuthResult = field(default_factory=lambda: OAuthResult(False, message="Login timed out."))


class _ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


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

        code = params.get("code", [""])[0]
        error = params.get("error", [""])[0]
        returned_state = params.get("state", [""])[0]

        # Production path: Vercel can already exchange the code and return tokens.
        access_token = params.get("access_token", [""])[0]
        open_id = params.get("open_id", [""])[0]
        refresh_token = params.get("refresh_token", [""])[0]

        if error:
            msg = _friendly(error)
            self._finish(state, OAuthResult(False, message=msg), ok=False, msg=msg)
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
                self._save(
                    state,
                    data.get("open_id", ""),
                    data.get("access_token", ""),
                    data.get("refresh_token", ""),
                )
                return

            msg = f"Token exchange failed: {err_detail}"
            self._finish(state, OAuthResult(False, message=msg), ok=False, msg=msg)
            return

        msg = _friendly("missing_code")
        self._finish(state, OAuthResult(False, message=msg), ok=False, msg=msg)

    def _exchange(self, code: str, code_verifier: str) -> tuple[dict | None, str]:
        """Returns (token_data_or_None, error_detail_string)."""
        if not TIKTOK_CLIENT_KEY or not TIKTOK_CLIENT_SECRET:
            return None, _friendly("server_misconfigured")

        body = urllib.parse.urlencode(
            {
                "client_key": TIKTOK_CLIENT_KEY,
                "client_secret": TIKTOK_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": TIKTOK_REDIRECT_URI,
                "code_verifier": code_verifier,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            TIKTOK_TOKEN_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cache-Control": "no-cache",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("access_token"):
                return data, ""

            return None, f"TikTok said: {self._extract_tiktok_error(data)}"

        except urllib.error.HTTPError as exc:
            try:
                body_text = exc.read().decode("utf-8")
                parsed = json.loads(body_text)
                detail = self._extract_tiktok_error(parsed)
            except Exception:
                detail = body_text if "body_text" in locals() else str(exc)
            return None, f"HTTP {exc.code}: {detail[:500]}"
        except Exception as exc:
            return None, str(exc)

    @staticmethod
    def _extract_tiktok_error(data: dict) -> str:
        err = data.get("error")
        if isinstance(err, dict):
            return err.get("message") or err.get("code") or str(err)
        return (
            data.get("error_description")
            or data.get("message")
            or data.get("description")
            or data.get("error")
            or str(data)
        )

    def _save(self, state: _ServerState, open_id: str, access_token: str, refresh_token: str) -> None:
        if not open_id or not access_token:
            msg = "TikTok token response was missing open_id or access_token. Please reconnect."
            self._finish(state, OAuthResult(False, message=msg), ok=False, msg=msg)
            return

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
        html = f"""
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8" />
            <title>ClipQue TikTok Login</title>
          </head>
          <body style="font-family: Arial, sans-serif; padding: 32px;">
            <h2 style="color:{color};">{msg}</h2>
          </body>
        </html>
        """.encode("utf-8")

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
        if not TIKTOK_CLIENT_KEY or not TIKTOK_CLIENT_SECRET:
            return OAuthResult(False, message=_friendly("server_misconfigured"))

        state_value = secrets.token_urlsafe(16)
        code_verifier, code_challenge = _generate_pkce()

        server_state = _ServerState(
            token_store=self.token_store,
            expected_state=state_value,
            code_verifier=code_verifier,
        )

        try:
            httpd = _ReusableHTTPServer((LOCAL_HOST, LOCAL_PORT), _CallbackHandler)
        except OSError as exc:
            return OAuthResult(False, message=f"Could not start local callback server on port {LOCAL_PORT}: {exc}")

        httpd.state = server_state  # type: ignore[attr-defined]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        auth_params = {
            "client_key": TIKTOK_CLIENT_KEY,
            "scope": TIKTOK_SCOPES,
            "response_type": "code",
            "redirect_uri": TIKTOK_REDIRECT_URI,
            "state": state_value,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        webbrowser.open(f"{TIKTOK_AUTH_URL}?{urlencode(auth_params)}")
        server_state.done.wait(timeout=timeout_seconds)
        result = server_state.result

        httpd.shutdown()
        httpd.server_close()
        return result
