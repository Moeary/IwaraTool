"""Application configuration backed by QSettings."""
import os
import sys
import threading
from pathlib import Path
from PySide6.QtCore import QSettings


DEFAULT_FILENAME_TEMPLATE = "{author}/{YYYY-MM-DD}_{title}_{id}.mp4"
LEGACY_FILENAME_TEMPLATE = "{username}/{YYYYMMDD}_{title}_{id}.mp4"
LEGACY_FILENAME_TEMPLATES = frozenset(
    {
        LEGACY_FILENAME_TEMPLATE,
        "{username}/{YYYY-MM-DD}_{title}_{id}.mp4",
    }
)


def _app_root_dir() -> str:
    """Directory where the app is launched (portable-friendly)."""
    return str(Path(sys.argv[0]).resolve().parent)


def _app_data_dir() -> str:
    data_dir = Path(_app_root_dir()) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir)


class _Setting:
    """Plain persisted setting: the attribute name is the QSettings key."""

    def __set_name__(self, owner, name):
        self._key = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return obj._get(self._key)

    def __set__(self, obj, value):
        if isinstance(AppConfig._DEFAULTS.get(self._key), bool):
            value = bool(value)
        obj._set(self._key, value)


class AppConfig:
    """Persistent application settings wrapper."""

    _DEFAULTS = {
        "download_dir": os.path.join(_app_root_dir(), "download"),
        "max_concurrent": 3,
        "task_stall_timeout_seconds": 30,
        "auto_restore_stalled_cancelled": False,
        "api_proxy_enabled": True,
        "api_proxy_url": "http://127.0.0.1:7890",
        "download_proxy_enabled": False,
        "download_proxy_url": "http://127.0.0.1:7890",
        "proxy_enabled": False,
        "proxy_url": "http://127.0.0.1:7890",
        "auth_enabled": False,
        "username": "",
        "password": "",
        "auth_token": "",
        "auth_token_saved_at": "",
        "preferred_quality": "Source",  # Source / 540 / 360
        "auto_login": True,
        "skip_existing_files": True,
        "filename_template": DEFAULT_FILENAME_TEMPLATE,
        "ui_language": "zh_CN",
        "filter_enabled": False,
        "filter_min_likes_enabled": False,
        "filter_min_likes": 0,
        "filter_min_views_enabled": False,
        "filter_min_views": 0,
        "filter_date_enabled": False,
        "filter_start_date": "1970-01-01",
        "filter_end_date": "",
        "filter_include_tags_enabled": False,
        "filter_include_tags": "",
        "filter_exclude_tags_enabled": False,
        "filter_exclude_tags": "",
        "filter_title_include": "",
        "filter_title_exclude": "",
        "search_limit_enabled": True,
        "search_limit_count": 100,
        "search_history_limit": 20,
        "search_auto_search_enabled": True,
        "aria2_rpc_enabled": False,
        "aria2_rpc_url": "http://127.0.0.1:6800/jsonrpc",
        "aria2_rpc_token": "",
        "download_video_file": True,
        "download_thumbnail": False,
        "collect_nfo_info": False,
        "mark_submitted_as_downloaded": False,
        "record_to_history": True,
        "subscription_prompt_mode": "ask",  # ask / always / never
        "completed_task_click_action": "folder",
        "subscription_auto_refresh_enabled": False,
        "subscription_refresh_interval_minutes": 30,
        "desktop_notifications_enabled": True,
        "subscription_auto_enqueue_enabled": False,
        "subscription_auto_enqueue_rule_id": "__builtin_default__",
        "global_speed_limit_enabled": False,
        "global_speed_limit_kib": 0,
        "download_schedule_enabled": False,
        "download_schedule_start": "00:00",
        "download_schedule_end": "00:00",
        "update_check_enabled": True,
        "update_last_prompted_version": "",
        "theme_mode": "auto",  # auto / light / dark
        "request_min_interval_ms": 200,  # spacing between API requests per host
        "request_max_retries": 2,  # retries for 429/5xx and transient network errors
        "x_version_salts": "",  # extra X-Version salts (comma separated), tried first
        "minimize_to_tray": False,
    }

    # Never copied out of the old registry-based store.
    _LEGACY_UNMIGRATED_KEYS = frozenset({"auth_enabled", "username", "password"})

    def __init__(self):
        self._data_dir = _app_data_dir()
        self._config_path = os.path.join(self._data_dir, "config.ini")
        self._history_db_path = os.path.join(self._data_dir, "history.db")

        self._qs = QSettings(self._config_path, QSettings.Format.IniFormat)
        # QSettings is reentrant, not thread-safe: the download watchdog and
        # subscription workers read settings while the GUI thread writes them.
        self._lock = threading.RLock()
        self._migrate_legacy_settings_if_needed()
        self._purge_legacy_qsettings()
        self._migrate_download_dir_if_needed()
        self._migrate_split_proxy_settings()
        self._migrate_filename_template_if_needed()

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
        safe_keys = set(self._DEFAULTS) - self._LEGACY_UNMIGRATED_KEYS
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

    def _migrate_split_proxy_settings(self):
        """Split the old single proxy into API and download proxy settings."""
        legacy_enabled_present = self._qs.contains("proxy_enabled")
        legacy_url_present = self._qs.contains("proxy_url")
        legacy_enabled = self._coerce_bool(
            self._qs.value("proxy_enabled", self._DEFAULTS["proxy_enabled"])
        )
        legacy_url = str(
            self._qs.value("proxy_url", self._DEFAULTS["proxy_url"])
            or self._DEFAULTS["proxy_url"]
        )

        if not self._qs.contains("api_proxy_enabled"):
            self._qs.setValue("api_proxy_enabled", self._DEFAULTS["api_proxy_enabled"])
        if not self._qs.contains("api_proxy_url"):
            self._qs.setValue(
                "api_proxy_url",
                legacy_url if legacy_url_present else self._DEFAULTS["api_proxy_url"],
            )
        if not self._qs.contains("download_proxy_enabled"):
            self._qs.setValue(
                "download_proxy_enabled",
                legacy_enabled if legacy_enabled_present else self._DEFAULTS["download_proxy_enabled"],
            )
        if not self._qs.contains("download_proxy_url"):
            self._qs.setValue(
                "download_proxy_url",
                legacy_url if legacy_url_present else self._DEFAULTS["download_proxy_url"],
            )
        self._qs.sync()

    def _migrate_filename_template_if_needed(self):
        """Move untouched legacy defaults to the stable-author template."""

        current = str(self._qs.value("filename_template", "") or "").strip()
        if current in LEGACY_FILENAME_TEMPLATES:
            self._qs.setValue("filename_template", DEFAULT_FILENAME_TEMPLATE)
            self._qs.sync()

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _coerce_bool(value) -> bool:
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)

    def _get(self, key: str):
        default = self._DEFAULTS[key]
        with self._lock:
            try:
                value = self._qs.value(key, default)
            except RuntimeError:
                # Background watchdogs can poll once more while the
                # interpreter tears QSettings down at exit.
                value = default
        # QSettings serialises bools as strings on Windows
        if isinstance(default, bool):
            return self._coerce_bool(value)
        if isinstance(default, int):
            return int(value)
        return value

    def _set(self, key: str, value):
        with self._lock:
            self._qs.setValue(key, value)
            self._qs.sync()

    def get_ui_value(self, key: str, default=""):
        with self._lock:
            return self._qs.value(f"ui/{key}", default)

    def set_ui_value(self, key: str, value, *, sync: bool = True):
        with self._lock:
            self._qs.setValue(f"ui/{key}", value)
            if sync:
                self._qs.sync()

    def sync(self):
        with self._lock:
            self._qs.sync()

    # ── properties ───────────────────────────────────────────────────────────

    download_dir = _Setting()

    max_concurrent = _Setting()

    @property
    def task_stall_timeout_seconds(self) -> int:
        return max(0, min(3600, self._get("task_stall_timeout_seconds")))

    @task_stall_timeout_seconds.setter
    def task_stall_timeout_seconds(self, v: int):
        self._set("task_stall_timeout_seconds", max(0, min(3600, int(v))))

    auto_restore_stalled_cancelled = _Setting()

    api_proxy_enabled = _Setting()

    api_proxy_url = _Setting()

    download_proxy_enabled = _Setting()

    download_proxy_url = _Setting()

    proxy_enabled = _Setting()

    proxy_url = _Setting()

    auth_enabled = _Setting()

    username = _Setting()

    password = _Setting()

    auth_token = _Setting()

    auth_token_saved_at = _Setting()

    preferred_quality = _Setting()

    auto_login = _Setting()

    skip_existing_files = _Setting()

    filename_template = _Setting()

    @property
    def app_data_dir(self) -> str:
        return self._data_dir

    @property
    def config_path(self) -> str:
        return self._config_path

    @property
    def history_db_path(self) -> str:
        return self._history_db_path

    ui_language = _Setting()

    filter_enabled = _Setting()

    filter_min_likes_enabled = _Setting()

    filter_min_likes = _Setting()

    filter_min_views_enabled = _Setting()

    filter_min_views = _Setting()

    filter_date_enabled = _Setting()

    filter_start_date = _Setting()

    filter_end_date = _Setting()

    filter_include_tags_enabled = _Setting()

    filter_include_tags = _Setting()

    filter_exclude_tags_enabled = _Setting()

    filter_exclude_tags = _Setting()

    filter_title_include = _Setting()

    filter_title_exclude = _Setting()

    search_limit_enabled = _Setting()

    search_limit_count = _Setting()

    @property
    def search_history_limit(self) -> int:
        """Maximum number of recent searches retained in the UI history."""

        try:
            value = int(self._get("search_history_limit"))
        except (TypeError, ValueError):
            value = 20
        return max(1, min(100, value))

    @search_history_limit.setter
    def search_history_limit(self, v: int):
        try:
            value = int(v)
        except (TypeError, ValueError):
            value = 20
        self._set("search_history_limit", max(1, min(100, value)))

    search_auto_search_enabled = _Setting()

    aria2_rpc_enabled = _Setting()

    aria2_rpc_url = _Setting()

    aria2_rpc_token = _Setting()

    download_video_file = _Setting()

    download_thumbnail = _Setting()

    collect_nfo_info = _Setting()

    mark_submitted_as_downloaded = _Setting()

    record_to_history = _Setting()

    @property
    def subscription_prompt_mode(self) -> str:
        mode = str(self._get("subscription_prompt_mode") or "ask").lower()
        return mode if mode in ("ask", "always", "never") else "ask"

    @subscription_prompt_mode.setter
    def subscription_prompt_mode(self, v: str):
        mode = str(v or "ask").lower()
        self._set("subscription_prompt_mode", mode if mode in ("ask", "always", "never") else "ask")

    completed_task_click_action = _Setting()

    subscription_auto_refresh_enabled = _Setting()

    @property
    def subscription_refresh_interval_minutes(self) -> int:
        return max(1, min(24 * 60, self._get("subscription_refresh_interval_minutes")))

    @subscription_refresh_interval_minutes.setter
    def subscription_refresh_interval_minutes(self, v: int):
        self._set("subscription_refresh_interval_minutes", max(1, min(24 * 60, int(v))))

    desktop_notifications_enabled = _Setting()

    subscription_auto_enqueue_enabled = _Setting()

    @property
    def subscription_auto_enqueue_rule_id(self) -> str:
        return str(self._get("subscription_auto_enqueue_rule_id") or "__builtin_default__")

    @subscription_auto_enqueue_rule_id.setter
    def subscription_auto_enqueue_rule_id(self, v: str):
        self._set("subscription_auto_enqueue_rule_id", str(v or "__builtin_default__"))

    global_speed_limit_enabled = _Setting()

    @property
    def global_speed_limit_kib(self) -> int:
        return max(0, min(10 * 1024 * 1024, self._get("global_speed_limit_kib")))

    @global_speed_limit_kib.setter
    def global_speed_limit_kib(self, v: int):
        self._set("global_speed_limit_kib", max(0, min(10 * 1024 * 1024, int(v))))

    download_schedule_enabled = _Setting()

    @property
    def download_schedule_start(self) -> str:
        return str(self._get("download_schedule_start") or "00:00")

    @download_schedule_start.setter
    def download_schedule_start(self, v: str):
        self._set("download_schedule_start", str(v or "00:00"))

    @property
    def download_schedule_end(self) -> str:
        return str(self._get("download_schedule_end") or "00:00")

    @download_schedule_end.setter
    def download_schedule_end(self, v: str):
        self._set("download_schedule_end", str(v or "00:00"))

    update_check_enabled = _Setting()

    @property
    def update_last_prompted_version(self) -> str:
        return str(self._get("update_last_prompted_version") or "")

    @update_last_prompted_version.setter
    def update_last_prompted_version(self, v: str):
        self._set("update_last_prompted_version", str(v or ""))

    request_min_interval_ms = _Setting()
    request_max_retries = _Setting()
    x_version_salts = _Setting()
    minimize_to_tray = _Setting()

    @property
    def theme_mode(self) -> str:
        mode = str(self._get("theme_mode") or "auto").strip().lower()
        return mode if mode in ("auto", "light", "dark") else "auto"

    @theme_mode.setter
    def theme_mode(self, v: str):
        mode = str(v or "auto").strip().lower()
        self._set("theme_mode", mode if mode in ("auto", "light", "dark") else "auto")


# Module-level singleton
app_config = AppConfig()
