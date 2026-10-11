"""Download scheduler, transfer backends, and task finalization."""
from __future__ import annotations

from .existing_files import ExistingFilesMixin
from .resolving import ResolvingMixin
from .scheduling import SchedulingMixin
from .stall_watchdog import StallWatchdogMixin
from .task_rules import TaskRulesMixin
from .temp_files import TempFilesMixin
from .terminal_states import TerminalStatesMixin
from .transfer import TransferMixin


class DownloadRuntimeMixin(
    TaskRulesMixin,
    SchedulingMixin,
    ResolvingMixin,
    TransferMixin,
    ExistingFilesMixin,
    TerminalStatesMixin,
    StallWatchdogMixin,
    TempFilesMixin,
):
    """Everything the download manager does to run tasks, grouped by topic."""
