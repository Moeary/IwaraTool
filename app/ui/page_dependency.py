"""Late-bound references to services named on a page module.

A page split into several mixin modules still has one module tests patch
(``download_manager``, ``signal_bus`` and so on).  The other modules import a
``PageDependency`` instead of the service itself; it looks the name up on the
page module at call time and falls back to the real object.
"""

from __future__ import annotations

import sys


class PageDependency:
    def __init__(self, page_module: str, name: str, fallback: object):
        self.page_module = page_module
        self.name = name
        self.fallback = fallback

    def _target(self):
        return getattr(sys.modules.get(self.page_module), self.name, self.fallback)

    def __getattr__(self, attribute: str):
        return getattr(self._target(), attribute)

    def __call__(self, *args, **kwargs):
        return self._target()(*args, **kwargs)
