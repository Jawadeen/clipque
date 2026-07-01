from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .config import APP_DIR

LICENSE_PATH = APP_DIR / "license.json"


@dataclass
class LicenseStatus:
    valid: bool
    mode: str
    message: str


class LicenseManager:
    """
    Local placeholder for v1 sales gating.

    Later this should call a tiny backend endpoint:
    POST /api/license/validate { license_key, machine_id }
    and cache the signed result locally.
    """
    def __init__(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)

    def machine_id(self) -> str:
        raw = str(Path.home()).encode("utf-8", errors="ignore")
        return hashlib.sha256(raw).hexdigest()[:16]

    def load_key(self) -> str:
        if not LICENSE_PATH.exists():
            return ""
        try:
            data = json.loads(LICENSE_PATH.read_text(encoding="utf-8"))
            return str(data.get("license_key", ""))
        except Exception:
            return ""

    def save_key(self, license_key: str) -> None:
        LICENSE_PATH.write_text(json.dumps({"license_key": license_key.strip()}, indent=2), encoding="utf-8")

    def validate(self, license_key: str | None = None) -> LicenseStatus:
        key = (license_key if license_key is not None else self.load_key()).strip()
        if not key:
            return LicenseStatus(True, "DEV", "No license key set. Running in dev/local mode.")
        if len(key) >= 12:
            return LicenseStatus(True, "LICENSED", "License key saved locally. Backend validation comes next.")
        return LicenseStatus(False, "INVALID", "License key is too short.")
