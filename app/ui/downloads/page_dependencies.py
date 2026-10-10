"""Services the task-center modules look up on the task-center module at call time.

Tests replace ``download_manager`` on ``app.ui.downloads.task_center`` only;
the task-center mixins reach it through this proxy, so one patch covers them all.
"""

from __future__ import annotations

from ...core.manager import download_manager as _default_download_manager
from ..page_dependency import PageDependency

PAGE_MODULE = "app.ui.downloads.task_center"

download_manager = PageDependency(PAGE_MODULE, "download_manager", _default_download_manager)
