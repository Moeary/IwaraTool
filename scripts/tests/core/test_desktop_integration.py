"""System notifications, launch at login and the notification text for the Flet build (no Qt)."""

from __future__ import annotations

import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class NotificationTextTests(unittest.TestCase):
    def test_single_and_batched_messages(self):
        from app.presentation.notifications import PreparedTaskNotifications, notification_message

        self.assertIsNone(notification_message(PreparedTaskNotifications()))
        title, message = notification_message(PreparedTaskNotifications(("A",)))
        self.assertEqual(message, "A")
        _title, message = notification_message(PreparedTaskNotifications((), ("B: boom",)))
        self.assertEqual(message, "B: boom")
        _title, message = notification_message(PreparedTaskNotifications(("A", "C"), ("B",)))
        self.assertIn("2", message)
        self.assertIn("1", message)


class SystemNotifyTests(unittest.TestCase):
    def test_macos_command_escapes_quotes(self):
        from app.core.system_notify import command

        args = command('Ti"tle', 'a "quoted" \\ text', "darwin")
        self.assertEqual(args[:2], ["osascript", "-e"])
        self.assertIn('\\"quoted\\"', args[2])
        self.assertIn("\\\\", args[2])

    def test_windows_command_cannot_break_out_of_the_powershell_string(self):
        from app.core.system_notify import command

        script = command("it's", "<b> & 'x' \"y\"", "win32")[-1]
        xml = script.split("LoadXml('", 1)[1].split("');", 1)[0]
        self.assertNotIn("'", xml)
        self.assertIn("&apos;", xml)
        self.assertIn("&lt;b&gt; &amp;", xml)

    def test_linux_without_notify_send(self):
        from app.core import system_notify

        with mock.patch.object(system_notify.shutil, "which", lambda _name: None):
            self.assertIsNone(system_notify.command("t", "m", "linux"))
            with mock.patch.object(system_notify, "sys") as fake_sys:
                fake_sys.platform = "linux"
                self.assertFalse(system_notify.notify("t", "m"))

    @unittest.skipUnless(sys.platform == "win32", "PowerShell toast script")
    def test_windows_script_loads_the_toast_xml(self):
        from app.core.system_notify import command

        script = command("IwaraTool test", "Ünïcode & <xml> 'quotes'", "win32")[-1]
        script = script.rsplit("[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier", 1)[0]
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script + "'ok'"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


class LoginItemTests(unittest.TestCase):
    def test_packaged_and_source_commands(self):
        from app.core import login_items

        self.assertEqual(login_items.launch_arguments(True, "/A/IwaraTool.app/Contents/MacOS/IwaraTool", "darwin"),
                         ["/usr/bin/open", "-a", "/A/IwaraTool.app"])
        self.assertEqual(login_items.launch_arguments(True, r"C:\Apps\IwaraTool.exe", "win32"), [r"C:\Apps\IwaraTool.exe"])
        args = login_items.launch_arguments(False, "/env/bin/python", "linux")
        self.assertEqual(args, ["/env/bin/python", str(login_items.SCRIPT)])

    def test_macos_agent_and_linux_entry(self):
        from app.core import login_items

        home = Path(tempfile.mkdtemp())
        args = ["/usr/bin/open", "-a", "/Applications/My App.app"]
        login_items.set_enabled(True, home=home, platform="darwin", args=args)
        self.assertTrue(login_items.is_enabled(home=home, platform="darwin"))
        data = plistlib.loads(login_items.agent_path(home).read_bytes())
        self.assertEqual(data["ProgramArguments"], args)
        self.assertTrue(data["RunAtLoad"])
        login_items.set_enabled(False, home=home, platform="darwin")
        self.assertFalse(login_items.is_enabled(home=home, platform="darwin"))

        login_items.set_enabled(True, home=home, platform="linux", args=["/opt/iwara tool/IwaraTool"])
        text = login_items.desktop_path(home).read_text(encoding="utf-8")
        self.assertIn('Exec="/opt/iwara tool/IwaraTool"', text)
        login_items.set_enabled(False, home=home, platform="linux")
        self.assertFalse(login_items.is_enabled(home=home, platform="linux"))

    def test_takes_over_the_entry_of_older_versions(self):
        from app.core import login_items

        self.assertEqual(login_items.VALUE_NAME, "IwaraTool")  # the Run value v1.2 and earlier wrote

    def test_an_existing_entry_follows_the_program(self):
        from app.core import login_items

        home = Path(tempfile.mkdtemp())
        for platform in ("darwin", "linux"):
            new = ["/Applications/IwaraTool.app/Contents/MacOS/IwaraTool"]
            self.assertFalse(login_items.follow_program(home=home, platform=platform, args=new))  # nothing to follow
            self.assertFalse(login_items.is_enabled(home=home, platform=platform))
            login_items.set_enabled(True, home=home, platform=platform, args=["/old/IwaraTool"])
            self.assertTrue(login_items.follow_program(home=home, platform=platform, args=new))
            self.assertFalse(login_items.follow_program(home=home, platform=platform, args=new))  # already current
            self.assertTrue(login_items.is_enabled(home=home, platform=platform))
        self.assertEqual(plistlib.loads(login_items.agent_path(home).read_bytes())["ProgramArguments"], new)
        self.assertIn(f"Exec={new[0]}", login_items.desktop_path(home).read_text(encoding="utf-8"))

    @unittest.skipUnless(sys.platform == "win32", "Windows registry")
    def test_windows_run_value(self):
        from app.core import login_items

        before = login_items.is_enabled()
        try:
            login_items.set_enabled(True, args=[r"C:\Program Files\IwaraTool\IwaraTool.exe"])
            self.assertTrue(login_items.is_enabled())
            login_items.set_enabled(False)
            self.assertFalse(login_items.is_enabled())
        finally:
            if before:
                login_items.set_enabled(True)


if __name__ == "__main__":
    unittest.main()
