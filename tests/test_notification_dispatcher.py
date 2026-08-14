import unittest

from app.ui.notification_dispatcher import (
    PreparedTaskNotifications,
    TaskNotificationBatch,
    TaskNotificationEvent,
    prepare_task_notifications,
)


class _Task:
    def __init__(self, title, video_id="", error_msg=""):
        self.title = title
        self.video_id = video_id
        self.error_msg = error_msg


class TaskNotificationDispatcherTests(unittest.TestCase):
    def test_batch_deduplicates_same_terminal_event(self):
        batch = TaskNotificationBatch()

        self.assertTrue(batch.add("one", "completed"))
        self.assertFalse(batch.add("one", "completed"))
        self.assertFalse(batch.add("", "completed"))
        self.assertFalse(batch.add("two", "downloading"))
        self.assertEqual(batch.drain(), [TaskNotificationEvent("one", "completed")])
        self.assertFalse(batch)

    def test_new_status_replaces_previous_task_event(self):
        batch = TaskNotificationBatch()

        self.assertTrue(batch.add("one", "completed"))
        self.assertTrue(batch.add("one", "failed"))
        self.assertEqual(batch.drain(), [TaskNotificationEvent("one", "failed")])

    def test_prepare_copies_only_notification_data(self):
        tasks = {
            "one": _Task("First"),
            "two": _Task("", "video-two", "network error"),
        }
        prepared = prepare_task_notifications(
            [
                TaskNotificationEvent("one", "completed"),
                TaskNotificationEvent("two", "failed"),
                TaskNotificationEvent("missing", "completed"),
            ],
            tasks.get,
        )

        self.assertEqual(
            prepared,
            PreparedTaskNotifications(("First",), ("video-two: network error",)),
        )


if __name__ == "__main__":
    unittest.main()
