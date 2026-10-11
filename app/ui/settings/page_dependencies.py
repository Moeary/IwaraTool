"""Services the settings modules look up on the page module at call time.

Tests replace ``download_manager`` on ``app.ui.settings.page`` only; the card
modules reach it through this proxy, so one patch covers the whole page.
"""

from __future__ import annotations

from ...core.manager import download_manager as _default_download_manager
from ..page_dependency import PageDependency

PAGE_MODULE = "app.ui.settings.page"

download_manager = PageDependency(PAGE_MODULE, "download_manager", _default_download_manager)
