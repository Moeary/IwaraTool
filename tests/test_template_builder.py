import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.core.download.paths import validate_filename_template
from app.core.download.template_builder import (
    compose_template,
    parse_template,
    preview_template,
    template_fields,
    template_presets,
)
from app.ui.rules.rule_form import RuleFormWidget
from app.ui.rules.template_builder import FilenameTemplateBuilder

_app = QApplication.instance() or QApplication([])


class TemplateModelTests(unittest.TestCase):
    def test_every_preset_is_valid_unique_and_round_trips(self):
        presets = template_presets()
        self.assertGreaterEqual(len(presets), 20)
        self.assertEqual(len({p.template for p in presets}), len(presets))
        for preset in presets:
            with self.subTest(preset.template):
                self.assertTrue(validate_filename_template(preset.template)[0])
                parts = parse_template(preset.template)
                self.assertIsNotNone(parts)
                self.assertEqual(compose_template(parts.prefix, list(parts.rows)), preset.template)
                self.assertTrue(preview_template(preset.template).endswith(".mp4"))

    def test_fields_cover_every_template_token(self):
        self.assertEqual(len({f.token for f in template_fields()}), len(template_fields()))

    def test_parse_keeps_prefix_and_trailing_text_and_rejects_unknown(self):
        parts = parse_template("iwara/{author}/{title}[{id}].mp4")
        self.assertEqual(parts.prefix, "iwara/")
        self.assertEqual(parts.rows, (("author", "/"), ("title", "["), ("id", "]")))
        self.assertEqual(compose_template(parts.prefix, list(parts.rows)), "iwara/{author}/{title}[{id}].mp4")
        self.assertIsNone(parse_template("{nope}.mp4"))
        self.assertIsNone(parse_template("{title"))
        self.assertIsNone(parse_template("fixed.mp4"))
        self.assertEqual(parse_template("{title}").rows, (("title", ""),))

    def test_preview_uses_sample_values_and_blank_when_invalid(self):
        shown = preview_template("{author}/{YYYY}/{title}_{id}.mp4")
        self.assertEqual(shown.replace("\\", "/"), "user154126/2025/Sample Title_AbCd1234.mp4")
        self.assertEqual(preview_template("{nope}.mp4"), "")


class TemplateBuilderWidgetTests(unittest.TestCase):
    def test_builder_edits_emit_composed_template(self):
        builder = FilenameTemplateBuilder()
        seen = []
        builder.template_changed.connect(seen.append)
        builder.set_template("{title}.mp4")
        self.assertEqual(len(builder._rows), 1)
        builder._add_row()
        self.assertEqual(seen[-1], "{title}_{id}.mp4")
        builder._rows[1].after_edit.setText("/x")
        self.assertEqual(seen[-1], "{title}_{id}/x.mp4")
        builder._remove_row(builder._rows[1])
        self.assertEqual(seen[-1], "{title}_.mp4")
        builder.close()

    def test_choosing_a_preset_emits_and_manual_text_selects_it_back(self):
        builder = FilenameTemplateBuilder()
        seen = []
        builder.template_changed.connect(seen.append)
        preset = template_presets()[5]
        builder.preset_combo.setCurrentIndex(6)
        self.assertEqual(seen, [preset.template])
        self.assertEqual(builder.template(), preset.template)
        builder.set_template("{id}.mp4")
        self.assertEqual(builder.preset_combo.currentIndex(), 0)
        builder.close()

    def test_unrepresentable_text_is_left_alone_and_recovers(self):
        builder = FilenameTemplateBuilder()
        builder.set_template("{title}_{id}.mp4")
        builder.set_template("{unknown}.mp4")
        self.assertTrue(builder.status_label.isHidden() is False)
        self.assertEqual(builder.template(), "{title}_{id}.mp4")
        builder.set_template("{id}.mp4")
        self.assertTrue(builder.status_label.isHidden())
        self.assertEqual(builder.template(), "{id}.mp4")
        builder.close()

    def test_rule_form_keeps_text_and_builder_in_sync(self):
        form = RuleFormWidget()
        form.filename_template_edit.setText("HMV/{id}_{title}.mp4")
        self.assertEqual(form.template_builder.template(), "HMV/{id}_{title}.mp4")
        self.assertIn("AbCd1234_Sample Title.mp4", form.template_preview_label.text())
        form.template_builder.preset_combo.setCurrentIndex(2)
        self.assertEqual(form.filename_template_edit.text(), template_presets()[1].template)
        self.assertEqual(form.payload()["filename_template"], template_presets()[1].template)
        form.filename_template_edit.setText("{unknown}.mp4")
        self.assertIn("无效", form.template_preview_label.text())
        form.close()


if __name__ == "__main__":
    unittest.main()
