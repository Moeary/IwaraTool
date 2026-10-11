"""Subscription source, refresh, and metadata operations."""
from __future__ import annotations

from .items import SubscriptionItemsMixin
from .media_cache import SubscriptionMediaCacheMixin
from .refresh import SubscriptionRefreshMixin
from .sources import SubscriptionSourcesMixin
from .submission import SubscriptionSubmissionMixin


class SubscriptionManagerMixin(
    SubscriptionSourcesMixin,
    SubscriptionItemsMixin,
    SubscriptionMediaCacheMixin,
    SubscriptionSubmissionMixin,
    SubscriptionRefreshMixin,
):
    """Subscription features of the download manager, grouped by topic."""
