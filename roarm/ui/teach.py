"""Teach & Replay tab (minimal; completed in Task 8)."""

from __future__ import annotations

from pathlib import Path

from textual.containers import Container
from textual.widgets import Static

from roarm.protocol import Pose
from roarm.sequence import Point, Sequence


class TeachTab(Container):
    def __init__(self, sequence_dir: Path, **kw):
        super().__init__(**kw)
        self.sequence_dir = Path(sequence_dir)
        self.sequence = Sequence()

    def compose(self):
        yield Static("", id="seq-info")

    def capture(self, pose: Pose) -> int:
        self.sequence.points.append(Point(pose.clamped(), dwell=0.5))
        return len(self.sequence.points)

    def refresh_status(self) -> None:
        pass

    def set_torque(self, on: bool) -> None:
        pass
