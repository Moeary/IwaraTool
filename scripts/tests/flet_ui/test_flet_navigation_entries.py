"""Navigation entries: the primary five, Tools and Settings at the rail's foot, Tools grouping rules,
repair and the upload tools (routes.TOOL_GROUP, maintainer's request 2026-11-26)."""
from __future__ import annotations

import importlib.util
import unittest
from types import SimpleNamespace

HAS_FLET = importlib.util.find_spec("flet") is not None


class EntryTests(unittest.TestCase):
    def test_desktop_and_phone_entries(self):
        from app.ui_flet import routes

        self.assertEqual(routes.entries(desktop=True), routes.PRIMARY + (routes.TOOLS, routes.SETTINGS))
        self.assertEqual(routes.entries(desktop=False), routes.PRIMARY + (routes.RULES, routes.SETTINGS))
        self.assertEqual(routes.TOOL_GROUP, (routes.RULES, routes.REPAIR, routes.TOOLS))

    def test_the_marked_entry(self):
        from app.ui_flet import routes

        for destination in routes.TOOL_GROUP:
            self.assertEqual(routes.entry_for(destination, desktop=True), routes.TOOLS)
        self.assertEqual(routes.entry_for(routes.RULES, desktop=False), routes.RULES)
        self.assertEqual(routes.entry_for(routes.SEARCH, desktop=True), routes.SEARCH)


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class SelectEntryTests(unittest.TestCase):
    def _shell(self, platform, destination):
        from app.ui_flet import routes
        from app.ui_flet.platform.capabilities import capabilities
        from app.ui_flet.shell import Shell

        selected = []
        shell = SimpleNamespace(state=SimpleNamespace(destination=destination), _last_tool=routes.RULES,
                                caps=capabilities(SimpleNamespace(platform=platform)), select=selected.append)
        return shell, selected, lambda entry: Shell.select_entry(shell, entry)

    def test_tools_opens_rules_first_then_the_tab_used_last(self):
        from app.ui_flet import routes

        shell, selected, select_entry = self._shell("macos", routes.HOME)
        select_entry(routes.TOOLS)
        shell._last_tool = routes.REPAIR
        select_entry(routes.TOOLS)
        self.assertEqual(selected, [routes.RULES, routes.REPAIR])

    def test_tools_on_a_tools_page_stays_on_it(self):
        from app.ui_flet import routes

        _shell, selected, select_entry = self._shell("macos", routes.TOOLS)
        select_entry(routes.TOOLS)
        self.assertEqual(selected, [routes.TOOLS])

    def test_other_entries_pass_through(self):
        from app.ui_flet import routes

        _shell, selected, select_entry = self._shell("android", routes.HOME)
        select_entry(routes.RULES)
        select_entry(routes.SETTINGS)
        self.assertEqual(selected, [routes.RULES, routes.SETTINGS])


if __name__ == "__main__":
    unittest.main()
