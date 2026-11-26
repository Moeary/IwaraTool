"""Navigation entries: the primary five, Tools and Settings at the rail's foot, Tools grouping rules,
repair and the upload tools (routes.TOOL_GROUP, maintainer's request 2026-11-26)."""
from __future__ import annotations

import importlib.util
import unittest
import unittest.mock
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


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class SecondaryPageTests(unittest.TestCase):
    def test_phone_back_from_settings_returns_where_it_was_opened(self):
        from app.ui_flet import layout, routes
        from app.ui_flet.shell import Shell

        moves = []
        shell = SimpleNamespace(
            state=SimpleNamespace(destination=routes.SEARCH, path="/search", can_back=False, account_open=False,
                                  layout=SimpleNamespace(nav_mode=layout.NAV_BAR)),
            _return_to=routes.HOME, caps=SimpleNamespace(mobile=True),
            navigator=SimpleNamespace(back=lambda: False, can_go_back=lambda: False, switch=lambda d: moves.append(d)),
        )
        shell.secondary_page = lambda: Shell.secondary_page(shell)
        self.assertFalse(shell.secondary_page())
        Shell._on_navigated(shell, routes.SETTINGS, "/settings")
        self.assertTrue(shell.secondary_page())
        self.assertTrue(Shell.back(shell))
        self.assertEqual(moves, [routes.SEARCH])
        shell.state.layout.nav_mode = layout.NAV_RAIL  # wide windows keep settings in the rail
        self.assertFalse(shell.secondary_page())


class RailDragTests(unittest.TestCase):
    def test_drag_widens_then_collapses_and_saves(self):
        from app.ui_flet import layout, shell as shell_module
        from app.ui_flet.shell import Shell
        from app.ui_flet.tokens import Size

        saved = {}
        config = SimpleNamespace(set_ui_value=lambda k, v: saved.__setitem__(k, v))
        shell = SimpleNamespace(state=SimpleNamespace(layout=layout.LayoutState(1000, 800, "macos")), _rail_reach=None)
        with unittest.mock.patch.object(shell_module, "app_config", config):
            Shell.drag_rail(shell, 200)  # the labelled rail's width + 200
            self.assertEqual(shell.state.layout.nav_mode, layout.NAV_RAIL_EXTENDED)
            self.assertEqual(shell.state.layout.nav_width, Size.NAV_RAIL + 200)
            Shell.drag_rail(shell, -200)  # back: the labelled rail
            self.assertEqual(shell.state.layout.nav_mode, layout.NAV_RAIL)
            Shell.end_rail_drag(shell)
        self.assertFalse(saved[shell_module.RAIL_EXTENDED_KEY])
        self.assertEqual(saved[shell_module.RAIL_WIDTH_KEY], Size.NAV_RAIL + 200)
        self.assertIsNone(shell._rail_reach)


if __name__ == "__main__":
    unittest.main()
