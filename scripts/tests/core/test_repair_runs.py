"""Repair runs (core) and the Repair Center rules shared by both interfaces."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock


class FakeManager:
    def __init__(self, count: int = 5):
        self.count = count
        self.calls: list[tuple] = []

    def _items(self):
        return [{"path": f"/v/{i}.mp4", "status": "ready", "video_id": f"id{i:06d}"} for i in range(self.count)]

    def scan_repair_folder(self, folder, *, filename_template, output_root, move_to_output, preview_options,
                           progress_callback, activity_callback):
        self.calls.append(("scan", folder, filename_template, output_root, move_to_output, preview_options))
        done = []
        for index, item in enumerate(self._items(), 1):
            activity_callback(index, self.count, 1)
            if not progress_callback(item, index, self.count):
                break
            done.append(item)
        return done

    def repair_folder_files(self, items, *, options, progress_callback):
        self.calls.append(("repair", len(items), options))
        out = []
        for index, item in enumerate(items, 1):
            item = dict(item, status="completed")
            if not progress_callback(item, index, len(items)):
                break
            out.append(item)
        return {"items": out, "renamed": len(out)}

    def resolve_repair_item(self, items, path, text, *, options):
        return [dict(i, video_id=text) if i["path"] == path else i for i in items]

    def preview_repair_files(self, items, *, options):
        return [dict(i, target_path=i["path"] + ".new") for i in items]


class RepairRunsTests(unittest.TestCase):
    def test_scan_hands_over_batches_and_progress(self):
        from app.core.repair import runs

        batches, progress, activity = [], [], []
        result = runs.scan(FakeManager(5), "/v", filename_template="{id}.mp4", options={"rename": True},
                           on_batch=batches.append, on_progress=lambda i, t: progress.append((i, t)),
                           on_activity=lambda c, t, a: activity.append(c), should_stop=lambda: False, interval=3600)
        self.assertEqual(len(result), 5)
        # The first item and the last one flush; the rest wait for the interval.
        self.assertEqual([len(b) for b in batches], [1, 4])
        self.assertEqual(progress[-1], (5, 5))
        self.assertEqual(activity, [1, 5])

    def test_stop_ends_the_run_and_flushes_what_finished(self):
        from app.core.repair import runs

        state = {"n": 0}

        def should_stop():
            state["n"] += 1
            return state["n"] > 3

        batches = []
        result = runs.repair(FakeManager(), [{"path": f"/v/{i}"} for i in range(6)], on_batch=batches.append,
                             should_stop=should_stop, interval=3600)
        self.assertLess(len(result["items"]), 6)
        self.assertEqual(sum(len(b) for b in batches), len(result["items"]))

    def test_follows_the_current_cancel_token(self):
        from app.core.cancellation import CancelToken, cancel_scope
        from app.core.repair import runs

        token = CancelToken()
        token.cancel()
        with cancel_scope(token):
            result = runs.scan(FakeManager(4), "/v", filename_template="x")
        self.assertEqual(result, [])

    def test_resolve_and_preview_pass_through(self):
        from app.core.repair import runs

        items = [{"path": "/a"}, {"path": "/b"}]
        self.assertEqual(runs.resolve(FakeManager(), items, "/b", "abcdefgh")[1]["video_id"], "abcdefgh")
        self.assertEqual(runs.preview(FakeManager(), items, {})[0]["target_path"], "/a.new")


class RepairPresentationTests(unittest.TestCase):
    def test_form_options_and_output_root(self):
        from app.presentation.repair import page as rp

        folder = tempfile.mkdtemp()
        form = rp.RepairForm(folder=folder)
        self.assertEqual(form.output_root, "")
        self.assertEqual(form.options()["filename_template"], rp.DEFAULT_TEMPLATE)
        organize = rp.RepairForm(folder=folder, mode=rp.MODE_ORGANIZE)
        self.assertEqual(organize.output_root, os.path.abspath(folder))
        self.assertTrue(organize.options()["move_to_output"])
        custom = rp.RepairForm(folder=folder, mode=rp.MODE_ORGANIZE, output="~/out")
        self.assertEqual(custom.output_root, os.path.abspath(os.path.expanduser("~/out")))

    def test_saved_form_restores_organize_only_when_saved(self):
        from app.presentation.repair import page as rp

        folder = tempfile.mkdtemp()
        ui = {rp.KEY_FOLDER: folder, rp.KEY_OUTPUT: "/out"}
        with mock.patch.object(rp.app_config, "get_ui_value", lambda key, default=None: ui.get(key, default)), \
                mock.patch.object(rp.app_config, "set_ui_value", lambda key, value: ui.__setitem__(key, value)):
            self.assertEqual(rp.saved_form("{title}.mp4").mode, rp.MODE_IN_PLACE)
            self.assertEqual(rp.saved_form("{title}.mp4").template, "{title}.mp4")
            rp.save_form(rp.RepairForm(folder=folder, mode=rp.MODE_ORGANIZE, template="{id}.mp4"), folder=True)
            saved = rp.saved_form()
            self.assertEqual((saved.mode, saved.template, saved.folder), (rp.MODE_ORGANIZE, "{id}.mp4", os.path.abspath(folder)))

    def test_row_states_and_summary(self):
        from app.presentation.repair import page as rp

        items = [
            {"path": "/a", "status": "ready"},
            {"path": "/b", "status": "ready", "target_conflict": "exists"},
            {"path": "/c", "status": "not_found", "message": "none"},
            {"path": "/d", "status": "completed"},
        ]
        self.assertEqual([rp.state_key(i) for i in items], ["ready", "conflict", "not_found", "completed"])
        self.assertTrue(rp.repairable(items))
        self.assertFalse(rp.repairable(items[1:]))
        self.assertIn(rp.manual_id_hint(), rp.row_detail(items[2]))
        self.assertEqual(rp.row_detail(items[2], hint="H"), "none · H")
        self.assertFalse(rp.id_editable(items[3]))
        self.assertEqual([n for _label, n in rp.summary_counts(items)], [4, 1, 1, 1, 1])
        by_path: dict = {}
        self.assertEqual(len(rp.merge_batch(by_path, [{"path": "/x"}, {"path": ""}])), 1)
        self.assertEqual(set(rp.state_styles()), {"", "ready", "completed", "not_found", "ambiguous", "failed", "conflict", "skipped"})


if __name__ == "__main__":
    unittest.main()
