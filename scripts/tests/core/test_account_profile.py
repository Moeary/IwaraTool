"""The signed-in account's name and avatar for the account panel (core/account.current_profile)."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from app.core import account

USER = {"id": "u1", "name": "Moe", "username": "moe", "avatar": None}


def _manager(current=None, profile=None):
    api = SimpleNamespace(
        token="t",
        get_current_user=lambda: (current, "" if current else "no"),
        get_user_profile=lambda name: ({"user": profile} if profile else None, ""),
    )
    return SimpleNamespace(api=api)


class ProfileTests(unittest.TestCase):
    def test_from_the_current_user_endpoint(self):
        found = account.current_profile(_manager(current=USER))
        self.assertEqual((found["id"], found["name"], found["username"]), ("u1", "Moe", "moe"))

    def test_falls_back_to_the_public_profile_of_a_user_name_login(self):
        with mock.patch.object(account, "app_config", SimpleNamespace(username="moe")):
            self.assertEqual(account.current_profile(_manager(profile=USER))["id"], "u1")
        with mock.patch.object(account, "app_config", SimpleNamespace(username="a@b.c")):
            self.assertEqual(account.current_profile(_manager(profile=USER)), {})

    def test_nothing_when_signed_out(self):
        manager = _manager(current=USER)
        manager.api.token = None
        self.assertEqual(account.current_profile(manager), {})


if __name__ == "__main__":
    unittest.main()
