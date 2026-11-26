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
    def _shell(self, platform, destination, *, two_pane=True):
        from app.ui_flet.platform.capabilities import capabilities
        from app.ui_flet.shell import Shell

        calls = []
        navigator = SimpleNamespace(switch=lambda d: calls.append(("switch", d)), go_root=lambda d: calls.append(("root", d)))
        shell = SimpleNamespace(
            state=SimpleNamespace(destination=destination, layout=SimpleNamespace(two_pane=two_pane)),
            _last_tool="rules", caps=capabilities(SimpleNamespace(platform=platform)), navigator=navigator,
            select=lambda d: calls.append(("select", d)), open=lambda p: calls.append(("open", p)),
            tools=SimpleNamespace(choose_tool=lambda k: calls.append(("tool", k))),
        )
        shell.open_tool = lambda key: Shell.open_tool(shell, key)
        return shell, calls, lambda entry: Shell.select_entry(shell, entry)

    def test_tools_opens_rules_first_then_the_tool_used_last(self):
        from app.ui_flet import routes

        shell, calls, select_entry = self._shell("macos", routes.HOME)
        select_entry(routes.TOOLS)
        shell.open_tool("covers")
        shell.state.destination = routes.HOME
        select_entry(routes.TOOLS)
        self.assertEqual(calls, [("switch", routes.RULES), ("tool", "covers"), ("switch", routes.TOOLS),
                                 ("tool", "covers"), ("switch", routes.TOOLS)])

    def test_narrow_windows_open_the_tools_list(self):
        from app.ui_flet import routes

        shell, calls, select_entry = self._shell("macos", routes.HOME, two_pane=False)
        select_entry(routes.TOOLS)
        shell.open_tool("compress")
        self.assertEqual(calls, [("select", routes.TOOLS), ("tool", "compress"), ("open", routes.route(routes.TOOLS, "compress"))])

    def test_phones_have_no_repair_and_pass_other_entries(self):
        from app.ui_flet import routes

        shell, calls, select_entry = self._shell("android", routes.HOME)
        shell.open_tool("repair")
        select_entry(routes.RULES)
        self.assertEqual(calls, [("select", routes.RULES)])


if __name__ == "__main__":
    unittest.main()
