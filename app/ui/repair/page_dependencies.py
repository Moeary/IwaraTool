"""Services the repair modules look up on the page module at call time.

Tests replace ``download_manager`` on ``app.ui.repair.page`` only; the repair
mixins reach it through this proxy, so one patch covers them all.
"""

from __future__ import annotations

from ...core.manager import download_manager as _default_download_manager
from ..page_dependency import PageDependency

PAGE_MODULE = "app.ui.repair.page"

download_manager = PageDependency(PAGE_MODULE, "download_manager", _default_download_manager)
