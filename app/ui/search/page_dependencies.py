"""Services the search modules look up on the page module at call time.

Tests replace ``download_manager`` / ``signal_bus`` / ``read_pixmap`` on
``app.ui.search.page`` only; every other search module reaches them through
these proxies, so one patch covers the whole page.
"""

from __future__ import annotations

import sys

from ...core.manager import download_manager as _default_download_manager
from ...signal_bus import signal_bus as _default_signal_bus
from ..media_card import read_pixmap as _default_read_pixmap

PAGE_MODULE = "app.ui.search.page"


class PageDependency:
    def __init__(self, name: str, fallback: object):
        self.name = name
        self.fallback = fallback

    def _target(self):
        return getattr(sys.modules.get(PAGE_MODULE), self.name, self.fallback)

    def __getattr__(self, attribute: str):
        return getattr(self._target(), attribute)

    def __call__(self, *args, **kwargs):
        return self._target()(*args, **kwargs)


download_manager = PageDependency("download_manager", _default_download_manager)
signal_bus = PageDependency("signal_bus", _default_signal_bus)
read_pixmap = PageDependency("read_pixmap", _default_read_pixmap)
