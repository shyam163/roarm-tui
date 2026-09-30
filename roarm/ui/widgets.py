"""Reusable widgets: joint sliders, load bars, braille arm view, status bar, confirm modal."""

from __future__ import annotations

import math

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Label, Static

from roarm import protocol as P

# Braille dot bits indexed [row][col] within a 2x4 cell.
_BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
BLANK = "⠀"

LOAD_FULL_SCALE = 500
COLOR_TRACK = "#334155"
COLOR_FILL = "#38bdf8"
COLOR_THUMB = "#f472b6"
COLOR_GHOST = "#94a3b8"


class BrailleCanvas:
    def __init__(self, cols: int, rows: int):
        self.cols, self.rows = cols, rows
        self.w, self.h = cols * 2, rows * 4
        self._cells = [[0] * cols for _ in range(rows)]

    def set(self, x: float, y: float) -> None:
        xi, yi = int(round(x)), int(round(y))
        if 0 <= xi < self.w and 0 <= yi < self.h:
            self._cells[yi // 4][xi // 2] |= _BITS[yi % 4][xi % 2]

    def line(self, x0: float, y0: float, x1: float, y1: float) -> None:
        steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0))))
        for i in range(steps + 1):
            t = i / steps
            self.set(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)

    def dot(self, x: float, y: float) -> None:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                self.set(x + dx, y + dy)

    def lines(self) -> list[str]:
        return ["".join(chr(0x2800 + c) for c in row) for row in self._cells]


FLOOR_Z = -120.0


def render_side(pose: P.Pose, cols: int, rows: int) -> list[str]:
    """Side view in the arm's plane: reach to the right, height up."""
    c = BrailleCanvas(cols, rows)
    rmin, rmax = -0.45 * P.REACH, 1.05 * P.REACH
    zmin, zmax = FLOOR_Z - 20, 1.05 * P.REACH
    scale = min(c.w / (rmax - rmin), c.h / (zmax - zmin))
    ox = (c.w - (rmax - rmin) * scale) / 2

    def px(r: float, z: float) -> tuple[float, float]:
        return ox + (r - rmin) * scale, c.h - 1 - (z - zmin) * scale

    c.line(*px(rmin, FLOOR_Z), *px(rmax, FLOOR_Z))
    c.line(*px(0, FLOOR_Z), *px(0, 0))
    pts = [px(r, z) for r, z in P.planar_points(pose)]
    for a, b in zip(pts, pts[1:]):
        c.line(*a, *b)
    for p in pts:
        c.dot(*p)
    return c.lines()


def render_top(pose: P.Pose, cols: int, rows: int) -> list[str]:
    """Top view: forward is up, left is left; ring shows maximum reach."""
    c = BrailleCanvas(cols, rows)
    cx, cy = (c.w - 1) / 2, (c.h - 1) / 2
    scale = (min(c.w, c.h) / 2 - 1) / P.REACH
    for deg in range(0, 360, 8):
        a = math.radians(deg)
        c.set(cx + math.cos(a) * P.REACH * scale, cy + math.sin(a) * P.REACH * scale)
    reach = P.planar_points(pose)[2][0]
    fwd, left = reach * math.cos(pose.base), reach * math.sin(pose.base)
    tip = (cx - left * scale, cy - fwd * scale)
    c.line(cx, cy, *tip)
    c.dot(cx, cy)
    c.dot(*tip)
    return c.lines()


def slider_value(x: int, bar_width: int, lo: float, hi: float) -> float:
    frac = min(1.0, max(0.0, x / max(1, bar_width - 1)))
    return lo + frac * (hi - lo)


def slider_index(value: float, bar_width: int, lo: float, hi: float) -> int:
    frac = min(1.0, max(0.0, (value - lo) / (hi - lo)))
    return round(frac * (bar_width - 1))


def load_color(load: int) -> str:
    v = abs(load)
    if v < 150:
        return "#4ade80"
    if v < 350:
        return "#fbbf24"
    return "#f87171"


class JointSlider(Widget, can_focus=True):
    """One-line slider: click to jump, drag, mouse wheel to nudge. Shows target (●) and actual (◆)."""

    DEFAULT_CSS = "JointSlider { height: 1; }"
    LABEL_W = 10
    VALUE_W = 9

    target: reactive[float] = reactive(0.0)
    actual: reactive[float | None] = reactive(None)
    selected: reactive[bool] = reactive(False)

    class Changed(Message):
        def __init__(self, slider: JointSlider, joint: str, value: float):
            super().__init__()
            self.slider = slider
            self.joint = joint
            self.value = value

    def __init__(self, joint: str, label: str, step_deg: float = 5.0, **kw):
        super().__init__(**kw)
        self.joint = joint
        self.label = label
        self.lo, self.hi = P.LIMITS[joint]
        self.step_deg = step_deg
        self._dragging = False
        self.set_reactive(JointSlider.target, P.clamp_joint(joint, P.HOME.get(joint)))

    @property
    def bar_width(self) -> int:
        return max(3, self.size.width - self.LABEL_W - self.VALUE_W)

    def set_target(self, value: float, emit: bool = True) -> None:
        value = P.clamp_joint(self.joint, value)
        if value == self.target:
            return
        self.target = value
        if emit:
            self.post_message(self.Changed(self, self.joint, value))

    def nudge(self, direction: int) -> None:
        self.set_target(self.target + direction * math.radians(self.step_deg))

    def _value_at(self, x: int) -> float:
        return slider_value(x - self.LABEL_W, self.bar_width, self.lo, self.hi)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._dragging = True
        self.capture_mouse()
        self.focus()
        self.set_target(self._value_at(event.x))

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._dragging:
            self.set_target(self._value_at(event.x))

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self.nudge(1)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self.nudge(-1)

    def render(self) -> Text:
        bw = self.bar_width
        ti = slider_index(self.target, bw, self.lo, self.hi)
        ai = None if self.actual is None else slider_index(self.actual, bw, self.lo, self.hi)
        t = Text(no_wrap=True)
        marker = "▶" if self.selected else " "
        t.append(f"{marker}{self.label:<{self.LABEL_W - 1}}",
                 style="bold #7aa2f7" if self.selected else "#a9b1d6")
        for i in range(bw):
            if i == ti:
                t.append("●", style=f"bold {COLOR_THUMB}")
            elif i == ai:
                t.append("◆", style=COLOR_GHOST)
            elif i < ti:
                t.append("━", style=COLOR_FILL)
            else:
                t.append("─", style=COLOR_TRACK)
        t.append(f"{math.degrees(self.target):>7.1f}° ", style="bold")
        return t


class LoadBars(Widget):
    DEFAULT_CSS = "LoadBars { height: 4; }"

    loads: reactive[dict[str, int]] = reactive(dict, always_update=True)

    def render(self) -> Text:
        t = Text(no_wrap=True)
        width = max(4, self.size.width - 15)
        for i, j in enumerate(P.JOINTS):
            v = abs(int(self.loads.get(j, 0)))
            filled = round(min(1.0, v / LOAD_FULL_SCALE) * width)
            color = load_color(v)
            t.append(f"{P.JOINT_LABELS[j]:<9}", style="#a9b1d6")
            t.append("█" * filled, style=color)
            t.append("░" * (width - filled), style=COLOR_TRACK)
            t.append(f" {v:>4}", style=color)
            if i < len(P.JOINTS) - 1:
                t.append("\n")
        return t


class ArmView(Widget):
    pose: reactive[P.Pose | None] = reactive(None)
    xyz: reactive[tuple[float, float, float]] = reactive((0.0, 0.0, 0.0))

    def render(self) -> Text:
        if self.pose is None:
            return Text("waiting for feedback…", style="dim italic")
        w, h = self.content_size.width, self.content_size.height
        rows = max(3, h - 2)
        side_cols = max(8, (w - 3) * 3 // 5)
        top_cols = max(6, w - 3 - side_cols)
        side = render_side(self.pose, side_cols, rows)
        top = render_top(self.pose, top_cols, rows)
        t = Text(no_wrap=True)
        t.append(f"{'SIDE':<{side_cols}}", style="bold #7aa2f7")
        t.append(" │ ", style="#3b4261")
        t.append("TOP\n", style="bold #7aa2f7")
        for a, b in zip(side, top):
            t.append(a, style="#7dcfff")
            t.append(" │ ", style="#3b4261")
            t.append(b + "\n", style="#bb9af7")
        x, y, z = self.xyz
        t.append(f"x {x:7.1f}   y {y:7.1f}   z {z:7.1f}  mm", style="bold")
        return t


class StatusBar(Static):
    pass


class ConfirmScreen(ModalScreen[bool]):
    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self.message, id="confirm-message")
            with Horizontal(classes="row"):
                yield Button("Yes", id="confirm-yes", variant="warning")
                yield Button("Cancel", id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm-yes")
