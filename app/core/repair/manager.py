"""Folder repair operations mixed into the central download manager."""
from __future__ import annotations

from .file_moves import RepairFileMovesMixin
from .preview import RepairPreviewMixin
from .repairing import RepairingMixin
from .scanning import RepairScanningMixin


class RepairManagerMixin(
    RepairScanningMixin,
    RepairPreviewMixin,
    RepairingMixin,
    RepairFileMovesMixin,
):
    """Folder repair features of the download manager, grouped by topic."""
