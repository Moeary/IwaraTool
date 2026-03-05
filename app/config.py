"""Application configuration backed by QSettings."""
import os
import sys
from pathlib import Path
from PySide6.QtCore import QSettings


def _app_root_dir() -> str:
    """Directory where the app is launched (portable-friendly)."""
    return str(Path(sys.argv[0]).resolve().parent)


def _app_data_dir() -> str:
    data_dir = Path(_app_root_dir()) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir)


class AppConfig:
    """Persistent application settings wrapper."""

    _DEFAULTS = {
        "download_dir": os.path.join(_app_root_dir(), "download"),
        "max_concurrent": 3,
        "proxy_enabled": False,
        "proxy_url": "http://127.0.0.1:7890",
        "auth_enabled": False,
        "username": "",
        "password": "",
        "preferred_quality": "Source",  # Source / 540 / 360
        "auto_login": True,
        "skip_existing_files": True,
        "filename_template": "{YYYY-MM-DD}_{title}_{id}.mp4",
        "ui_language": "zh_CN",
    }

    def __init__(self):
        self._data_dir = _app_data_dir()
        self._config_path = os.path.join(self._data_dir, "config.ini")
        self._history_db_path = os.path.join(self._data_dir, "history.db")

        self._qs = QSettings(self._config_path, QSettings.Format.IniFormat)
        self._migrate_legacy_settings_if_needed()
        self._purge_legacy_qsettings()
        self._migrate_download_dir_if_needed()

        # Ensure default download directory exists
        os.makedirs(self.download_dir, exist_ok=True)

    def _migrate_legacy_settings_if_needed(self):
        """One-time migration from old platform-default QSettings location.

        Only migrate non-sensitive preferences. Credentials are intentionally
        not migrated to avoid unexpected password resurrection.
        """
        if os.path.exists(self._config_path):
            return
        legacy = QSettings("IwaraTool", "IwaraTool")
        safe_keys = {
            "download_dir",
            "max_concurrent",
            "proxy_enabled",
            "proxy_url",
            "preferred_quality",
            "auto_login",
            "skip_existing_files",
            "filename_template",
            "ui_language",
        }
        for key, default in self._DEFAULTS.items():
            if key not in safe_keys:
                continue
            if legacy.contains(key):
                self._qs.setValue(key, legacy.value(key, default))
        self._qs.sync()

    def _purge_legacy_qsettings(self):
        """Remove all values from old registry-based QSettings store.

        We now persist only to portable data/config.ini.
        """
        legacy = QSettings("IwaraTool", "IwaraTool")
        if legacy.allKeys():
            legacy.clear()
            legacy.sync()

    def _migrate_download_dir_if_needed(self):
        """Normalize old local folder name 'downloads' to new default './download'."""
        current = str(self._qs.value("download_dir", self._DEFAULTS["download_dir"]))
        app_root = Path(_app_root_dir()).resolve()
        old_local = app_root / "downloads"
        new_local = app_root / "download"

        try:
            cur_path = Path(current).resolve()
        except Exception:
            return

        if cur_path == old_local and not self._qs.value("_migrated_download_dir_v2", False):
            self._qs.setValue("download_dir", str(new_local))
            self._qs.setValue("_migrated_download_dir_v2", True)
            self._qs.sync()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _get(self, key: str):
        default = self._DEFAULTS[key]
        value = self._qs.value(key, default)
        # QSettings serialises bools as strings on Windows
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)
        if isinstance(default, int):
            return int(value)
        return value

    def _set(self, key: str, value):
        self._qs.setValue(key, value)
        self._qs.sync()

    # ── properties ───────────────────────────────────────────────────────────

    @property
    def download_dir(self) -> str:
        return self._get("download_dir")

    @download_dir.setter
    def download_dir(self, v: str):
        self._set("download_dir", v)

    @property
    def max_concurrent(self) -> int:
        return self._get("max_concurrent")

    @max_concurrent.setter
    def max_concurrent(self, v: int):
        self._set("max_concurrent", v)

    @property
    def proxy_enabled(self) -> bool:
        return self._get("proxy_enabled")

    @proxy_enabled.setter
    def proxy_enabled(self, v: bool):
        self._set("proxy_enabled", v)

    @property
    def proxy_url(self) -> str:
        return self._get("proxy_url")

    @proxy_url.setter
    def proxy_url(self, v: str):
        self._set("proxy_url", v)

    @property
    def auth_enabled(self) -> bool:
        return self._get("auth_enabled")

    @auth_enabled.setter
    def auth_enabled(self, v: bool):
        self._set("auth_enabled", v)

    @property
    def username(self) -> str:
        return self._get("username")

    @username.setter
    def username(self, v: str):
        self._set("username", v)

    @property
    def password(self) -> str:
        return self._get("password")

    @password.setter
    def password(self, v: str):
        self._set("password", v)

    @property
    def preferred_quality(self) -> str:
        return self._get("preferred_quality")

    @preferred_quality.setter
    def preferred_quality(self, v: str):
        self._set("preferred_quality", v)

    @property
    def auto_login(self) -> bool:
        return self._get("auto_login")

    @auto_login.setter
    def auto_login(self, v: bool):
        self._set("auto_login", v)

    @property
    def skip_existing_files(self) -> bool:
        return self._get("skip_existing_files")

    @skip_existing_files.setter
    def skip_existing_files(self, v: bool):
        self._set("skip_existing_files", v)

    @property
    def filename_template(self) -> str:
        return self._get("filename_template")

    @filename_template.setter
    def filename_template(self, v: str):
        self._set("filename_template", v)

    @property
    def app_data_dir(self) -> str:
        return self._data_dir

    @property
    def config_path(self) -> str:
        return self._config_path

    @property
    def history_db_path(self) -> str:
        return self._history_db_path

    @property
    def ui_language(self) -> str:
        return self._get("ui_language")

    @ui_language.setter
    def ui_language(self, v: str):
        self._set("ui_language", v)


# Module-level singleton
app_config = AppConfig()
