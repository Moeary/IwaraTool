"""The unified video action list (presentation.media.video_actions)."""

from __future__ import annotations

import unittest

from app.core.download.locations import STORAGE_REMOTE, FileLocation
from app.presentation.media import video_actions as va


class VideoActionListTests(unittest.TestCase):
    def ids(self, actions):
        return [action.id for action in actions]

    def test_online_video_order(self):
        actions = va.video_actions(FileLocation(), url="https://www.iwara.tv/video/x", author="ann")
        self.assertEqual(self.ids(actions), [va.DETAIL, va.PLAY, va.DOWNLOAD, va.OPEN_BROWSER, va.COPY_LINK, va.AUTHOR_PAGE])
        self.assertEqual(actions[1].label, va.play_label(FileLocation()))
        self.assertEqual([a.group for a in actions], sorted(a.group for a in actions))

    def test_local_copy_adds_folder_and_path(self):
        location = FileLocation(file_path="/v/a.mp4", local_path="/v/a.mp4")
        actions = va.video_actions(location, url="u")
        self.assertEqual(self.ids(actions)[:4], [va.DETAIL, va.PLAY, va.OPEN_FOLDER, va.COPY_PATH])
        self.assertEqual(actions[3].value, "/v/a.mp4")

    def test_unreachable_nas_copy(self):
        location = FileLocation(storage=STORAGE_REMOTE, remote_path="/nas/a.mp4")
        ids = self.ids(va.video_actions(location, url="u", can_stream=False))
        self.assertNotIn(va.PLAY, ids)
        self.assertIn(va.NAS_FOLDER, ids)
        self.assertIn(va.COPY_NAS_PATH, ids)

    def test_optional_entries(self):
        ids = self.ids(va.video_actions(FileLocation(), detail=False, can_stream=False, download=False, select=True))
        self.assertEqual(ids, [va.SELECT])


class PlayerChoiceTests(unittest.TestCase):
    """Long-press on a downloaded file: in the app or in an external player, the Settings default first."""

    def test_choices_put_the_default_first(self):
        location = FileLocation(file_path="/v/a.mp4", local_path="/v/a.mp4")
        external = va.player_choices(location, external_default=True)
        self.assertEqual([a.id for a in external], [va.PLAY_EXTERNAL, va.PLAY_BUILTIN])
        self.assertIn(va.default_mark(), external[0].label)
        self.assertNotIn(va.default_mark(), external[1].label)
        self.assertEqual(external[0].value, "/v/a.mp4")
        builtin = va.player_choices(location, external_default=False)
        self.assertEqual([a.id for a in builtin], [va.PLAY_BUILTIN, va.PLAY_EXTERNAL])
        self.assertEqual(va.player_choices(FileLocation(), external_default=True), [])

    def test_menu_adds_the_other_way_after_play(self):
        location = FileLocation(file_path="/v/a.mp4", local_path="/v/a.mp4")
        ids = [a.id for a in va.video_actions(location, external_default=True)]
        self.assertEqual(ids[ids.index(va.PLAY) + 1], va.PLAY_BUILTIN)
        ids = [a.id for a in va.video_actions(location, external_default=False)]
        self.assertEqual(ids[ids.index(va.PLAY) + 1], va.PLAY_EXTERNAL)
        plain = [a.id for a in va.video_actions(location)]  # Classic: no choice
        self.assertNotIn(va.PLAY_BUILTIN, plain)
        self.assertNotIn(va.PLAY_EXTERNAL, plain)


if __name__ == "__main__":
    unittest.main()
