"""Services the detail-view modules look up on ``detail_view`` at call time.

Tests replace ``download_manager`` / ``DetailWorker`` on
``app.ui.media.detail_view`` only; the detail mixins reach them through these
proxies, so one patch covers the whole view.
"""

from __future__ import annotations

from ...core.manager import download_manager as _default_download_manager
from ..home.workers import DetailWorker as _DefaultDetailWorker
from ..page_dependency import PageDependency

PAGE_MODULE = "app.ui.media.detail_view"

download_manager = PageDependency(PAGE_MODULE, "download_manager", _default_download_manager)
DetailWorker = PageDependency(PAGE_MODULE, "DetailWorker", _DefaultDetailWorker)
