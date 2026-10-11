"""X-Version salts and JWT expiry checks for the Iwara API."""

from __future__ import annotations

import base64
import json
import os
import time

from ...config import app_config
from ...logging_setup import get_logger


logger = get_logger(__name__)


# X-Version shared secrets (new first, legacy fallback)
_X_VERSION_SALTS = (
    "mSvL05GfEmeEmsEYfGCnVpEjYgTJraJN",
    "5nFp9kmbNnHdAFhaqMvt",
)


SALTS_OVERRIDE_FILE = "x_version_salts.json"


def x_version_salts() -> tuple[str, ...]:
    """Salts to try, user overrides first.

    The site rotates its shared secret from time to time; this lets a user
    recover without waiting for a new release by either filling the
    ``x_version_salts`` setting (comma separated) or dropping a JSON list
    into ``data/x_version_salts.json``.
    """
    extra: list[str] = []
    try:
        extra.extend(
            part.strip()
            for part in str(app_config.x_version_salts or "").replace(";", ",").split(",")
            if part.strip()
        )
        path = os.path.join(app_config.app_data_dir, SALTS_OVERRIDE_FILE)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if isinstance(payload, dict):
                payload = payload.get("salts", [])
            if isinstance(payload, list):
                extra.extend(str(item).strip() for item in payload if str(item).strip())
    except Exception:
        logger.warning("Ignoring unreadable X-Version salt override", exc_info=True)
    merged: list[str] = []
    for salt in (*extra, *_X_VERSION_SALTS):
        if salt not in merged:
            merged.append(salt)
    return tuple(merged)


def token_expires_at(token: str | None) -> float | None:
    """Unix ``exp`` of an Iwara JWT, or ``None`` when it cannot be read."""

    parts = str(token or "").split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp") if isinstance(claims, dict) else None
        return float(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


def token_is_expired(token: str | None, *, margin_seconds: float = 0.0) -> bool:
    """Whether a JWT is past (or within ``margin_seconds`` of) its expiry.

    Iwara does not reject an expired or invalid bearer token on public
    listings: it silently answers as a guest, hiding videos that need an
    account. A stale cached token therefore has to be caught on our side.
    Tokens without a readable ``exp`` are trusted.
    """

    exp = token_expires_at(token)
    return exp is not None and exp - margin_seconds <= time.time()
