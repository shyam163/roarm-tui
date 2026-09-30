"""Diagnostics tab (minimal; completed in Task 9)."""

from __future__ import annotations

from textual.containers import Container
from textual.widgets import RichLog


class DiagTab(Container):
    def compose(self):
        yield RichLog(id="log", max_lines=2000)

    def add_line(self, direction: str, text: str) -> None:
        self.query_one(RichLog).write(f"{direction} {text}")

    def add_loads(self, loads: dict[str, int]) -> None:
        pass
