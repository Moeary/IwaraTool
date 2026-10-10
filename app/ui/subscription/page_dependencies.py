"""Services the subscription modules look up on the page module at call time.

Tests replace ``download_manager`` / ``signal_bus`` / ``InfoBar`` /
``isDarkTheme`` on ``app.ui.subscription.page`` only; every other subscription
module reaches them through these proxies, so one patch covers the whole page.
"""

from __future__ import annotations

from qfluentwidgets import InfoBar as _DefaultInfoBar, isDarkTheme as _default_is_dark_theme

from ...core.manager import download_manager as _default_download_manager
from ...signal_bus import signal_bus as _default_signal_bus
from ..page_dependency import PageDependency

PAGE_MODULE = "app.ui.subscription.page"

download_manager = PageDependency(PAGE_MODULE, "download_manager", _default_download_manager)
signal_bus = PageDependency(PAGE_MODULE, "signal_bus", _default_signal_bus)
InfoBar = PageDependency(PAGE_MODULE, "InfoBar", _DefaultInfoBar)
isDarkTheme = PageDependency(PAGE_MODULE, "isDarkTheme", _default_is_dark_theme)
