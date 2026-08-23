"""GitHub Releases based application update checks."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

import cloudscraper

from ..config import app_config
from ..version import __version__


LATEST_RELEASE_URL = "https://api.github.com/repos/Moeary/IwaraTool/releases/latest"


def version_key(value: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)*", str(value or ""))
    if not match:
        return ()
    return tuple(int(part) for part in match.group(0).split("."))


def is_newer_version(candidate: str, current: str) -> bool:
    candidate_key = version_key(candidate)
    current_key = version_key(current)
    width = max(len(candidate_key), len(current_key))
    if not candidate_key or not current_key:
        return False
    return candidate_key + (0,) * (width - len(candidate_key)) > current_key + (0,) * (width - len(current_key))


@dataclass(frozen=True)
class ReleaseCheckResult:
    ok: bool
    available: bool
    current_version: str
    version: str = ""
    name: str = ""
    url: str = ""
    notes: str = ""
    published_at: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GitHubReleaseChecker:
    def __init__(self, *, current_version: str = __version__, session=None):
        self.current_version = current_version
        self._session = session

    def check(self) -> ReleaseCheckResult:
        session = self._session or cloudscraper.create_scraper()
        proxies = None
        if app_config.api_proxy_enabled and app_config.api_proxy_url:
            proxies = {"http": app_config.api_proxy_url, "https": app_config.api_proxy_url}
        response = None
        try:
            response = session.get(
                LATEST_RELEASE_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": f"IwaraTool/{self.current_version}",
                },
                timeout=15,
                proxies=proxies,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("GitHub Releases returned a non-object payload")
            version = str(payload.get("tag_name", "") or "").strip()
            url = str(payload.get("html_url", "") or "").strip()
            if not version or not url:
                raise ValueError("GitHub Release is missing tag_name or html_url")
            return ReleaseCheckResult(
                ok=True,
                available=is_newer_version(version, self.current_version),
                current_version=self.current_version,
                version=version,
                name=str(payload.get("name", "") or version),
                url=url,
                notes=str(payload.get("body", "") or ""),
                published_at=str(payload.get("published_at", "") or ""),
            )
        except Exception as exc:
            return ReleaseCheckResult(
                ok=False,
                available=False,
                current_version=self.current_version,
                error=str(exc),
            )
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
