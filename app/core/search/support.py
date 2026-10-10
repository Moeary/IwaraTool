"""Tag suggestions, per-worker API clients and the search image cache."""

from __future__ import annotations


from ..api.client import IwaraAPI


class SearchSupportMixin:
    """Tag suggestions, per-worker API clients and the search image cache."""

    def get_search_tag_suggestions(self, query: str, *, limit: int = 16):
        """Return offline multilingual tag candidates for the search UI."""

        return self.tag_dictionary.suggest(query, limit=limit)

    def canonical_search_tag(self, value: str) -> str:
        """Normalize a localized tag label to its canonical search key."""

        return self.tag_dictionary.canonical_key(value)

    def update_search_tag_dictionary(self) -> tuple[int, str]:
        """Refresh the cached LoveIwara multilingual tag dictionary."""

        with self._api_lock:
            return self.tag_dictionary.update_from_remote(self.api.scraper)

    def create_worker_api_client(self) -> IwaraAPI:
        """Create an isolated API session for background network work.

        The main API session is intentionally serialized because it is shared
        by login, parsing and download metadata.  Image and subscription
        workers can safely use independent cloudscraper sessions while
        carrying over the current token and proxy configuration.
        """
        # Snapshot without _api_lock, which is held across shared-session requests.
        token = self.api.token or ""
        proxies = dict(getattr(self.api.scraper, "proxies", {}) or {})
        client = IwaraAPI()
        client.token = token or None
        client.scraper.proxies = proxies
        return client

    @staticmethod
    def close_worker_api_client(client: IwaraAPI | None):
        if client is None:
            return
        close = getattr(getattr(client, "scraper", None), "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def cache_search_image(
        self,
        kind: str,
        item_key: str,
        image_url: str,
        *,
        api_client: IwaraAPI | None = None,
    ) -> str:
        """Cache one search card image using an isolated or shared API session."""

        if api_client is None:
            with self._api_lock:
                client = self.api
                token = client.token or ""
        else:
            client = api_client
            token = client.token or ""
        headers = {
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": (
                "https://oreno3d.com/"
                if "oreno3d.com" in str(image_url or "").casefold()
                else "https://www.iwara.tv/"
            ),
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.search_image_cache.get_or_fetch(
            kind,
            item_key,
            image_url,
            session=client.scraper,
            headers=headers,
        )
