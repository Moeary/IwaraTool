"""Services the search modules look up on the page module at call time.

Tests replace ``download_manager`` / ``signal_bus`` / ``read_pixmap`` on
``app.ui.search.page`` only; every other search module reaches them through
these proxies, so one patch covers the whole page.
"""

from __future__ import annotations

from ...core.manager import download_manager as _default_download_manager
from ...signal_bus import signal_bus as _default_signal_bus
from ..media.pixmaps import read_pixmap as _default_read_pixmap
from ..page_dependency import PageDependency

PAGE_MODULE = "app.ui.search.page"

download_manager = PageDependency(PAGE_MODULE, "download_manager", _default_download_manager)
signal_bus = PageDependency(PAGE_MODULE, "signal_bus", _default_signal_bus)
read_pixmap = PageDependency(PAGE_MODULE, "read_pixmap", _default_read_pixmap)
