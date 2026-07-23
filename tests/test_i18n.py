import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.models import TaskStatus, status_label
from app.i18n import tr


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    unittest.main()
