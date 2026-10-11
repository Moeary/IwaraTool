"""Commands and context-menu actions for the subscription page."""
from __future__ import annotations

from .context_menus import SubscriptionContextMenusMixin
from .downloading import SubscriptionDownloadingMixin
from .pending_rows import SubscriptionPendingRowsMixin
from .refreshing import SubscriptionRefreshingMixin
from .selection import SubscriptionSelectionMixin
from .source_management import SubscriptionSourceManagementMixin
from .view_navigation import SubscriptionViewNavigationMixin


class SubscriptionActionsMixin(
    SubscriptionPendingRowsMixin,
    SubscriptionContextMenusMixin,
    SubscriptionSelectionMixin,
    SubscriptionSourceManagementMixin,
    SubscriptionRefreshingMixin,
    SubscriptionDownloadingMixin,
    SubscriptionViewNavigationMixin,
):
    """Every command of the subscription page, grouped by topic."""
