"""Online search and bridge operations mixed into DownloadManager."""
from __future__ import annotations

from .iwara_search import IwaraSearchMixin
from .oreno3d_bridge import Oreno3DBridgeMixin
from .support import SearchSupportMixin


class SearchManagerMixin(IwaraSearchMixin, Oreno3DBridgeMixin, SearchSupportMixin):
    """Search features of the download manager, grouped by topic."""
