"""Shared sign-in steps: save, announce, restore."""

from __future__ import annotations

import unittest
from unittest import mock

from app.core import account


class _Api:
    def __init__(self, ok=True):
        self.ok = ok
        self.token = None

    def login(self, credential, password):
        if self.ok:
            self.token = "tok"
            return True, ""
        return False, "bad password"

    def logout(self):
        self.token = None


class _Manager:
    def __init__(self, ok=True, cached=False):
        self.api = _Api(ok)
        self.cached = cached
        self.pending = []
        self.logins = []

    def set_login_pending(self, pending):
        self.pending.append(pending)

    def set_login(self, logged_in, token=None):
        self.logins.append((logged_in, token))

    def restore_cached_login(self):
        return self.cached


class _Config:
    auth_enabled = False
    username = ""
    password = ""


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.config = _Config()
        self.emitted = []
        bus = mock.MagicMock()
        bus.login_state_changed.emit.side_effect = lambda v: self.emitted.append(v)
        for patch in (mock.patch.object(account, "app_config", self.config), mock.patch.object(account, "signal_bus", bus)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_successful_login_saves_and_announces(self):
        manager = _Manager()
        self.assertEqual(account.login(manager, " me ", "pw"), (True, ""))
        self.assertEqual((self.config.username, self.config.password, self.config.auth_enabled), ("me", "pw", True))
        self.assertEqual(manager.logins, [(True, "tok")])
        self.assertEqual(manager.pending, [True, False])
        self.assertEqual(self.emitted, [True])

    def test_failed_login_changes_nothing(self):
        manager = _Manager(ok=False)
        self.assertEqual(account.login(manager, "me", "pw"), (False, "bad password"))
        self.assertEqual((self.config.username, manager.logins, self.emitted), ("", [], []))
        self.assertEqual(manager.pending, [True, False])

    def test_restore_prefers_the_cached_token_then_saved_credentials(self):
        self.assertTrue(account.restore_login(_Manager(cached=True)))
        self.assertEqual(self.emitted, [True])
        self.assertFalse(account.restore_login(_Manager()))  # nothing saved
        self.config.auth_enabled, self.config.username, self.config.password = True, "me", "pw"
        manager = _Manager()
        self.assertTrue(account.restore_login(manager))
        self.assertTrue(account.is_logged_in(manager))

    def test_logout(self):
        manager = _Manager()
        manager.api.token = "tok"
        account.logout(manager)
        self.assertEqual((manager.api.token, manager.logins, self.emitted), (None, [(False, None)], [False]))


if __name__ == "__main__":
    unittest.main()
