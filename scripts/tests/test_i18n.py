import ast
import importlib.util
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings

from app.config import app_config
from app.core.models import TaskStatus, status_label
from app.i18n import pinned_language, tr


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class I18nTest(unittest.TestCase):
    def test_tr_selects_all_supported_languages(self):
        translations = {"en": "English", "zh": "中文", "ja": "日本語"}
        for language, expected in translations.items():
            with self.subTest(language=language):
                with patch("app.i18n.current_language", return_value=language):
                    self.assertEqual(tr("English", "中文", "日本語"), expected)

    def test_status_labels_follow_runtime_language_changes(self):
        expected = {"en": "Downloading", "zh": "下载中", "ja": "ダウンロード中"}
        for language, label in expected.items():
            with self.subTest(language=language):
                with patch("app.i18n.current_language", return_value=language):
                    self.assertEqual(status_label(TaskStatus.DOWNLOADING), label)

    def test_pinned_language_is_thread_local_and_restored(self):
        seen_in_other_thread = []
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        settings = QSettings(str(Path(temp.name) / "config.ini"), QSettings.Format.IniFormat)
        with patch.object(app_config, "_qs", settings):
            app_config.ui_language = "zh_CN"
            with pinned_language("en"):
                self.assertEqual(tr("English", "中文", "日本語"), "English")
                with pinned_language("ja"):
                    self.assertEqual(tr("English", "中文", "日本語"), "日本語")
                self.assertEqual(tr("English", "中文", "日本語"), "English")
                worker = threading.Thread(target=lambda: seen_in_other_thread.append(tr("English", "中文", "日本語")))
                worker.start()
                worker.join()
            self.assertEqual(seen_in_other_thread, ["中文"])
            self.assertEqual(tr("English", "中文", "日本語"), "中文")

    def test_every_translation_call_provides_three_languages(self):
        missing: list[str] = []
        for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "tr"
                ):
                    continue
                has_japanese_keyword = any(
                    keyword.arg == "ja_text" for keyword in node.keywords
                )
                if len(node.args) < 3 and not has_japanese_keyword:
                    relative = path.relative_to(PROJECT_ROOT)
                    missing.append(f"{relative}:{node.lineno}")
        self.assertEqual(missing, [], f"tr() calls missing Japanese text: {missing}")

    def test_translations_are_not_frozen_at_module_import(self):
        frozen: list[str] = []
        for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for statement in tree.body:
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                for node in ast.walk(statement):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "tr"
                    ):
                        relative = path.relative_to(PROJECT_ROOT)
                        frozen.append(f"{relative}:{node.lineno}")
        self.assertEqual(frozen, [], f"module-scope translations found: {frozen}")

    def test_extract_script_collects_every_translation_call(self):
        spec = importlib.util.spec_from_file_location("i18n_extract", PROJECT_ROOT / "scripts" / "tools" / "i18n_extract.py")
        extract = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {spec.name: extract}):  # dataclasses look the module up
            spec.loader.exec_module(extract)

        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp:
            source = Path(temp)
            (source / "sample.py").write_text(
                "def f(n):\n"
                "    tr('Cancel', '取消', 'キャンセル')\n"
                "    tr('Cancel', '中断', '中断')\n"
                "    tr(f'{n} items', f'{n} 项', f'{n} 件')\n"
                "    tr('Cancel', '取消', 'キャンセル')\n"
                "    tr('Only English', '仅中文')\n",
                encoding="utf-8",
            )
            entries = extract.collect(source)

        by_key = {(e.en, e.zh): e for e in entries}
        self.assertEqual(len(entries), 4)
        self.assertEqual(len(by_key[("Cancel", "取消")].places), 2)
        self.assertTrue(by_key[("{n} items", "{n} 项")].dynamic)
        self.assertEqual(by_key[("Only English", "仅中文")].ja, "Only English")
        self.assertEqual(list(extract.conflicts(entries)), ["Cancel"])


if __name__ == "__main__":
    unittest.main()
