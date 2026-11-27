"""EventBridge: events from worker threads reach the loop in order; progress is coalesced."""

from __future__ import annotations

import asyncio
import threading
import unittest

from app.events import Signal, SignalHost
from app.ui.services.events import EventBridge


class _Bus(SignalHost):
    task_status_changed = Signal(str, str)
    task_progress_updated = Signal(str, int, int, str)


class EventBridgeTests(unittest.TestCase):
    def run_loop(self, body):
        return asyncio.run(body())

    def test_plain_events_arrive_on_the_loop_in_order(self):
        bus = _Bus()

        async def body():
            loop = asyncio.get_running_loop()
            bridge = EventBridge(loop, bus=bus)
            seen, threads = [], []
            bridge.subscribe("task_status_changed", lambda tid, status: seen.append((tid, status, threading.current_thread())))
            worker = threading.Thread(target=lambda: [bus.task_status_changed.emit("t", str(i)) for i in range(5)])
            worker.start()
            worker.join()
            await asyncio.sleep(0.05)
            return seen, threading.current_thread()

        seen, loop_thread = self.run_loop(body)
        self.assertEqual([status for _t, status, _th in seen], ["0", "1", "2", "3", "4"])
        self.assertTrue(all(thread is loop_thread for _t, _s, thread in seen))

    def test_handlers_run_in_the_context_the_bridge_was_made_in(self):
        """Stores re-render only inside the Flet page context; worker threads have none."""
        import contextvars

        marker = contextvars.ContextVar("marker", default="")
        bus = _Bus()

        async def body():
            marker.set("page")
            bridge = EventBridge(asyncio.get_running_loop(), bus=bus, flush_interval=0.01)
            seen = []
            bridge.subscribe("task_status_changed", lambda *_a: seen.append(marker.get()))
            bridge.subscribe("task_progress_updated", lambda *_a: seen.append(marker.get()), coalesce=lambda tid, *_r: tid)
            worker = threading.Thread(target=lambda: (bus.task_status_changed.emit("t", "x"), bus.task_progress_updated.emit("t", 1, 2, "")))
            worker.start()
            worker.join()
            await asyncio.sleep(0.1)
            return seen

        self.assertEqual(self.run_loop(body), ["page", "page"])

    def test_progress_keeps_the_latest_value_per_task(self):
        bus = _Bus()

        async def body():
            bridge = EventBridge(asyncio.get_running_loop(), bus=bus, flush_interval=0.05)
            seen = []
            bridge.subscribe(
                "task_progress_updated", lambda tid, done, total, speed: seen.append((tid, done)),
                coalesce=lambda tid, *_rest: tid,
            )

            def work():
                for done in range(100):
                    bus.task_progress_updated.emit("a", done, 100, "")
                    bus.task_progress_updated.emit("b", done * 2, 200, "")

            worker = threading.Thread(target=work)
            worker.start()
            worker.join()
            await asyncio.sleep(0.2)
            return seen

        seen = self.run_loop(body)
        self.assertLessEqual(len(seen), 4)  # one or two flushes, not 200 deliveries
        self.assertEqual(dict(seen[-2:]), {"a": 99, "b": 198})

    def test_coroutine_handlers_run(self):
        bus = _Bus()

        async def body():
            bridge = EventBridge(asyncio.get_running_loop(), bus=bus)
            seen = []

            async def handler(tid, status):
                await asyncio.sleep(0)
                seen.append(status)

            bridge.subscribe("task_status_changed", handler)
            bus.task_status_changed.emit("t", "done")
            await asyncio.sleep(0.05)
            return seen

        self.assertEqual(self.run_loop(body), ["done"])

    def test_closing_a_page_group_removes_its_receivers(self):
        bus = _Bus()

        async def body():
            bridge = EventBridge(asyncio.get_running_loop(), bus=bus)
            before = bus.task_status_changed.receivers()
            for _visit in range(5):  # enter and leave the page five times
                group = bridge.group()
                group.on("task_status_changed", lambda *a: None)
                group.on("task_progress_updated", lambda *a: None, coalesce=lambda tid, *_: tid)
                group.close()
            return before, bus.task_status_changed.receivers(), bus.task_progress_updated.receivers()

        before, status_after, progress_after = self.run_loop(body)
        self.assertEqual((status_after, progress_after), (before, 0))

    def test_nothing_is_delivered_after_close(self):
        bus = _Bus()

        async def body():
            bridge = EventBridge(asyncio.get_running_loop(), bus=bus)
            seen = []
            bridge.subscribe("task_status_changed", lambda *a: seen.append(a))
            bridge.close()
            bus.task_status_changed.emit("t", "late")
            await asyncio.sleep(0.05)
            return seen

        self.assertEqual(self.run_loop(body), [])


if __name__ == "__main__":
    unittest.main()
