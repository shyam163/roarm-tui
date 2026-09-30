"""Diagnostics tab: serial log, JSON console with history, load sparklines, device info."""

from __future__ import annotations

import json
import math
import re
from collections import deque

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, Label, RichLog, Sparkline, Static

from roarm import protocol as P

def _reject_non_finite(token: str) -> float:
    raise ValueError(f"{token} is not a finite number")


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f"{token} is not a finite number")
    return value


HISTORY_LEN = 120
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
DIRECTION_STYLE = {"tx": ("→ ", "#7dcfff"), "rx": ("← ", "#9ece6a"), "sys": ("• ", "#e0af68")}


def is_poll(text: str) -> bool:
    compact = text.replace(" ", "")
    return compact == '{"T":105}' or compact.startswith('{"T":1051')


class HistoryInput(Input):
    BINDINGS = [
        Binding("up", "history(-1)", show=False),
        Binding("down", "history(1)", show=False),
    ]

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.history: list[str] = []
        self._pos = 0

    def add_history(self, text: str) -> None:
        if not self.history or self.history[-1] != text:
            self.history.append(text)
        self._pos = len(self.history)

    def action_history(self, delta: int) -> None:
        if not self.history:
            return
        self._pos = max(0, min(len(self.history), self._pos + delta))
        self.value = self.history[self._pos] if self._pos < len(self.history) else ""
        self.cursor_position = len(self.value)


class DiagTab(Container):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._loads = {j: deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN) for j in P.JOINTS}
        self._info: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="diag-main"):
            with Vertical(id="log-panel", classes="panel"):
                yield RichLog(id="log", max_lines=2000, wrap=False, markup=False)
                with Horizontal(classes="row"):
                    yield Checkbox("Hide polling", value=True, id="hide-poll")
                    yield Checkbox("Pause", id="pause-log")
                    yield Button("Clear", id="log-clear")
                yield HistoryInput(placeholder='JSON command, e.g. {"T":105}  (↑/↓ history)', id="console")
            with Vertical(id="diag-side", classes="panel"):
                for j in P.JOINTS:
                    yield Label(P.JOINT_LABELS[j])
                    yield Sparkline(list(self._loads[j]), summary_function=max, id=f"spark-{j}")
                yield Static("", id="dev-info")
                yield Button("Query device info", id="diag-info")

    def on_mount(self) -> None:
        self.query_one("#log-panel").border_title = "SERIAL"
        self.query_one("#diag-side").border_title = "LOADS & DEVICE"

    def add_line(self, direction: str, text: str) -> None:
        self._capture_info(text)
        if self.query_one("#pause-log", Checkbox).value:
            return
        if self.query_one("#hide-poll", Checkbox).value and is_poll(text):
            return
        prefix, style = DIRECTION_STYLE.get(direction, ("  ", ""))
        line = Text(no_wrap=True)
        line.append(prefix, style=f"bold {style}")
        line.append(text, style=style)
        self.query_one(RichLog).write(line)

    def add_loads(self, loads: dict[str, int]) -> None:
        for j in P.JOINTS:
            self._loads[j].append(float(abs(loads.get(j, 0))))
            self.query_one(f"#spark-{j}", Sparkline).data = list(self._loads[j])

    def _capture_info(self, text: str) -> None:
        if MAC_RE.match(text.strip()):
            self._info["MAC"] = text.strip()
        elif '"ip"' in text:
            msg = P.parse_line(text)
            if msg is not None:
                self._info["Wi-Fi"] = f"ip {msg.get('ip')}  rssi {msg.get('rssi')}"
        else:
            return
        info = Text()
        for k, v in self._info.items():
            info.append(f"{k:<6}", style="bold")
            info.append(f"{v}\n")
        self.query_one("#dev-info", Static).update(info)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "log-clear":
            event.stop()
            self.query_one(RichLog).clear()
        elif event.button.id == "diag-info":
            event.stop()
            self.app.device.send(P.cmd_mac())
            self.app.device.send(P.cmd_wifi_info())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "console":
            return
        event.stop()
        text = event.value.strip()
        if not text:
            return
        try:
            cmd = json.loads(text, parse_constant=_reject_non_finite, parse_float=_finite_float)
            if not isinstance(cmd, dict):
                raise ValueError("command must be a JSON object")
        except ValueError as e:
            self.notify(f"Invalid JSON: {e}", severity="error", markup=False)
            return
        self.app.device.send(cmd)
        self.app.follow_feedback = True  # console is RAW — track the arm until the user's next move
        console = self.query_one("#console", HistoryInput)
        console.add_history(text)
        console.value = ""
