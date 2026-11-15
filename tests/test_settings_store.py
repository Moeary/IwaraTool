import os
import shutil
import tempfile
import unittest

from app.events import Signal, SignalHost
from app.settings_store import IniSettingsStore, RawIniValue, decode_value, encode_value

try:
    from PySide6.QtCore import QSettings
except ImportError:  # pragma: no cover - the desktop test env has PySide6
    QSettings = None

# What the app keeps in config.ini: strings of every awkward kind, ints, bools.
SAMPLE = {
    "plain": "hello",
    "int": 3,
    "neg": -5,
    "bool_t": True,
    "bool_f": False,
    "empty": "",
    "comma": "a, b,c",
    "json": '{"open": "Ctrl+K", "x": "a\\b"}',
    "quote": 'say "hi"',
    "unicode": "中文 テスト",
    "lead": "  pad  ",
    "nl": "line1\nline2",
    "tab": "a\tb",
    "at": "@home",
    "semi": "a;b",
    "eq": "a=b",
    "hash": "#x",
    "pct": "100%",
    "path": "C:\\Users\\me",
    "Key With Space": "v",
    "ui/col": "x",
    "ui/sizes": "120,340",
    "slash/deep/key": "v2",
    "none": None,
}

# The same values read back from disk: QSettings returns strings.
FROM_DISK = {
    key: (None if value is None else "true" if value is True else "false" if value is False else str(value))
    for key, value in SAMPLE.items()
}


class IniSettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _path(self, name="config.ini"):
        return os.path.join(self.tmp, name)

    def test_values_survive_a_reopen_as_qsettings_would_return_them(self):
        store = IniSettingsStore(self._path())
        for key, value in SAMPLE.items():
            store.setValue(key, value)
        self.assertEqual(store.value("int"), 3)  # in-session values keep their type
        store.sync()

        reopened = IniSettingsStore(self._path())
        self.assertEqual({key: reopened.value(key) for key in SAMPLE}, FROM_DISK)
        self.assertEqual(sorted(reopened.allKeys()), sorted(SAMPLE))

    def test_contains_remove_and_defaults(self):
        store = IniSettingsStore(self._path())
        store.setValue("ui/a", "1")
        store.setValue("ui/b", "2")
        store.setValue("top", "3")
        self.assertTrue(store.contains("ui/a"))
        self.assertEqual(store.value("missing", 7), 7)
        store.remove("ui")  # a group goes with its keys
        self.assertEqual(store.allKeys(), ["top"])
        store.remove("top")
        store.sync()
        self.assertEqual(IniSettingsStore(self._path()).allKeys(), [])

    def test_unquoted_commas_make_a_list_and_qt_variants_round_trip(self):
        self.assertEqual(decode_value("a, b"), ["a", "b"])
        self.assertEqual(decode_value('"a, b"'), "a, b")
        self.assertEqual(decode_value("@@home"), "@home")
        self.assertIsNone(decode_value("@Invalid()"))
        raw = decode_value(r"@ByteArray(\x1\x2\x61\x62)")
        self.assertIsInstance(raw, RawIniValue)
        self.assertEqual(encode_value(raw), r"@ByteArray(\x1\x2\x61\x62)")

        with open(self._path(), "w", encoding="utf-8") as handle:
            handle.write("[General]\nkeep=1\n\n[ui]\ngeom=@ByteArray(\\x1\\x2\\x61\\x62)\n")
        store = IniSettingsStore(self._path())
        store.setValue("other", "x")
        store.sync()
        with open(self._path(), encoding="utf-8") as handle:
            self.assertIn("geom=@ByteArray(\\x1\\x2\\x61\\x62)", handle.read())

    def test_values_qt_alone_can_store_are_refused(self):
        store = IniSettingsStore(self._path())
        with self.assertRaises(TypeError):
            store.setValue("dict", {"k": 1})

    @unittest.skipIf(QSettings is None, "needs PySide6 to compare with")
    def test_writes_the_same_file_as_qsettings(self):
        qt_path, ini_path = self._path("qt.ini"), self._path("ini.ini")
        qt = QSettings(qt_path, QSettings.Format.IniFormat)
        ini = IniSettingsStore(ini_path)
        for key, value in SAMPLE.items():
            qt.setValue(key, value)
            ini.setValue(key, value)
        qt.sync()
        ini.sync()
        with open(qt_path, encoding="utf-8") as a, open(ini_path, encoding="utf-8") as b:
            self.assertEqual(b.read(), a.read())

    @unittest.skipIf(QSettings is None, "needs PySide6 to compare with")
    def test_each_backend_reads_what_the_other_wrote(self):
        qt_path = self._path("from_qt.ini")
        qt = QSettings(qt_path, QSettings.Format.IniFormat)
        for key, value in SAMPLE.items():
            qt.setValue(key, value)
        qt.sync()
        ini = IniSettingsStore(qt_path)
        self.assertEqual({key: ini.value(key) for key in SAMPLE}, FROM_DISK)

        ini_path = self._path("from_ini.ini")  # never opened by QSettings: read from disk
        written = IniSettingsStore(ini_path)
        for key, value in SAMPLE.items():
            written.setValue(key, value)
        written.sync()
        fresh = QSettings(ini_path, QSettings.Format.IniFormat)
        self.assertEqual({key: fresh.value(key) for key in SAMPLE}, FROM_DISK)


class PureSignalTests(unittest.TestCase):
    class Host(SignalHost):
        changed = Signal(str, int)
        other = Signal()

    def test_connect_emit_disconnect(self):
        host, seen = self.Host(), []
        record = lambda name, value: seen.append((name, value))  # noqa: E731
        host.changed.connect(record)
        host.changed.emit("a", 1)
        self.assertTrue(host.changed.disconnect(record))
        self.assertFalse(host.changed.disconnect(record))
        host.changed.emit("b", 2)
        self.assertEqual(seen, [("a", 1)])

    def test_each_instance_has_its_own_signal(self):
        first, second, seen = self.Host(), self.Host(), []
        first.other.connect(lambda: seen.append("first"))
        second.other.emit()
        self.assertEqual(seen, [])
        self.assertIs(first.other, first.other)

    def test_a_failing_slot_does_not_stop_the_others(self):
        host, seen = self.Host(), []

        def broken(*_args):
            raise ValueError("boom")

        host.changed.connect(broken)
        host.changed.connect(lambda *args: seen.append(args))
        with self.assertLogs("iwaratool.events", level="ERROR"):
            host.changed.emit("x", 1)
        self.assertEqual(seen, [("x", 1)])


if __name__ == "__main__":
    unittest.main()
