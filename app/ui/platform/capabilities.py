"""What this platform can do, in one place.

Features a platform lacks are hidden, not shown and then refused.  The
interface asks ``capabilities(shell.state.layout)`` instead of testing the
platform name itself.  ``IWARATOOL_PLATFORM`` (``main.py --platform``)
makes a desktop run behave as another platform, to check the phone build's
pages and settings without a device.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

MOBILE_PLATFORMS = frozenset({"android", "ios"})
OVERRIDE_VARIABLE = "IWARATOOL_PLATFORM"


def platform_override() -> str:
    return os.environ.get(OVERRIDE_VARIABLE, "").strip().lower()


@dataclass(frozen=True)
class Capabilities:
    platform: str

    @property
    def mobile(self) -> bool:
        return self.platform in MOBILE_PLATFORMS

    @property
    def desktop(self) -> bool:
        return not self.mobile

    # Repair walks arbitrary folders; the tools run an ffmpeg program: neither exists on phones.
    @property
    def repair(self) -> bool:
        return self.desktop

    @property
    def tools(self) -> bool:
        return self.desktop

    @property
    def open_folder(self) -> bool:
        """A file manager can be opened at a folder."""
        return self.desktop

    @property
    def local_aria2(self) -> bool:
        """aria2 may run on this device (phones use a remote one, 8.2)."""
        return self.desktop

    @property
    def player_command(self) -> bool:
        """A custom external player command can be run."""
        return self.desktop

    @property
    def external_player(self) -> bool:
        """A downloaded file can be handed to the system's player (long-press offers it)."""
        return self.desktop

    @property
    def keyboard_shortcuts(self) -> bool:
        return self.desktop

    @property
    def window(self) -> bool:
        """A window to close, minimize and remember (the close prompt, tray notes)."""
        return self.desktop

    @property
    def keep_awake(self) -> bool:
        """Downloads stop when the app sleeps, so keep the screen on while they run (8.2)."""
        return self.mobile


def capabilities(layout_state) -> Capabilities:
    """The capabilities of the platform a ``LayoutState`` was measured on."""
    return Capabilities(str(getattr(layout_state, "platform", "") or "macos").lower())
