"""Teach & Replay tab: waypoint table, capture, record, playback, save/load."""

from __future__ import annotations

import math
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static

from roarm import protocol as P
from roarm.ui.widgets import ConfirmScreen
from roarm.sequence import Point, Sequence, list_sequences, load, save

SPEEDS = [("×0.25", 0.25), ("×0.5", 0.5), ("×1", 1.0), ("×1.5", 1.5), ("×2", 2.0)]
TORQUE_OFF_LABEL = "✋ Torque off (hand-guide)"
TORQUE_ON_LABEL = "⚡ Torque on"


class TeachTab(Container):
    def __init__(self, sequence_dir: Path, **kw):
        super().__init__(**kw)
        self.sequence_dir = Path(sequence_dir)
        self.sequence = Sequence()

    def compose(self) -> ComposeResult:
        with Horizontal(id="teach-main"):
            with Vertical(id="wp-panel", classes="panel"):
                yield DataTable(id="wp-table", cursor_type="row", zebra_stripes=True)
                with Horizontal(classes="row"):
                    yield Button("● Capture point", id="wp-capture", variant="primary")
                    yield Button("✕ Delete", id="wp-delete")
                    yield Button("🗑 Clear all", id="wp-clear")
                with Horizontal(classes="row"):
                    yield Button("▲", id="wp-up", classes="small")
                    yield Button("▼", id="wp-down", classes="small")
                    yield Input(placeholder="dwell s", id="wp-dwell", type="number", classes="small-input")
                    yield Button("Set dwell", id="wp-set-dwell")
            with Vertical(id="teach-side", classes="panel"):
                yield Static("", id="seq-info")
                yield Button(TORQUE_OFF_LABEL, id="teach-torque")
                yield Button("⏺ Record path", id="teach-record")
                with Horizontal(classes="row"):
                    yield Button("▶ Play", id="teach-play", variant="success")
                    yield Button("■ Stop", id="teach-stop")
                with Horizontal(classes="row"):
                    yield Checkbox("Loop", id="teach-loop")
                    yield Select(SPEEDS, value=1.0, allow_blank=False, id="teach-speed")
                yield Input(placeholder="sequence name", id="seq-name")
                with Horizontal(classes="row"):
                    yield Button("💾 Save", id="seq-save")
                    yield Button("✚ New", id="seq-new")
                yield Select([], prompt="saved sequences…", id="seq-select")
                yield Button("📂 Load", id="seq-load")

    def on_mount(self) -> None:
        self.query_one("#wp-panel").border_title = "WAYPOINTS"
        self.query_one("#teach-side").border_title = "SEQUENCE"
        self.query_one(DataTable).add_columns("#", "Base°", "Shoulder°", "Elbow°", "Grip°", "Dwell/t s")
        self.refresh_table()
        self.refresh_files()

    # --- called by the app ---------------------------------------------------
    def capture(self, pose: P.Pose) -> int:
        if self.sequence.kind != "waypoints":
            self.sequence = Sequence()
        self._stop_if_playing()
        self.sequence.points.append(Point(pose.clamped(), dwell=0.5))
        self.refresh_table()
        self.query_one(DataTable).move_cursor(row=len(self.sequence.points) - 1)
        return len(self.sequence.points)

    def set_torque(self, on: bool) -> None:
        self.query_one("#teach-torque", Button).label = TORQUE_OFF_LABEL if on else TORQUE_ON_LABEL

    def refresh_status(self) -> None:
        self.query_one("#teach-record", Button).label = (
            "⏹ Stop recording path" if self.app.recorder is not None else "⏺ Record path")
        seq = self.sequence
        t = Text()
        t.append(seq.name, style="bold")
        t.append(f" · {seq.kind} · {len(seq.points)} points")
        player = self.app.player
        if player is not None and player.running and player.sequence is seq:
            t.append(f"\n▶ playing {player.index + 1}/{len(seq.points)}", style="bold #7dcfff")
        if self.app.recorder is not None:
            t.append(f"\n⏺ recording · {len(self.app.recorder.points)} samples", style="bold #f87171")
        if not seq.points:
            t.append("\nPress space or ● Capture point to add a waypoint", style="dim")
        self.query_one("#seq-info", Static).update(t)

    # --- table ---------------------------------------------------------------
    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for i, pt in enumerate(self.sequence.points):
            extra = f"{pt.t:.2f}" if pt.t is not None else f"{pt.dwell:.2f}"
            table.add_row(str(i + 1), *(f"{math.degrees(pt.pose.get(j)):.1f}" for j in P.JOINTS), extra)
        self.refresh_status()

    def refresh_files(self) -> None:
        self.query_one("#seq-select", Select).set_options(
            [(p.stem, str(p)) for p in list_sequences(self.sequence_dir)])

    def _row(self) -> int | None:
        row = self.query_one(DataTable).cursor_row
        return row if 0 <= row < len(self.sequence.points) else None

    def _stop_if_playing(self) -> None:
        player = self.app.player
        if player is not None and player.running and player.sequence is self.sequence:
            self.app.stop_playback()

    # --- buttons -------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "wp-capture": self.app.action_capture,
            "wp-delete": self._delete,
            "wp-clear": self._clear_all,
            "wp-up": lambda: self._move(-1),
            "wp-down": lambda: self._move(1),
            "wp-set-dwell": self._set_dwell,
            "teach-torque": self.app.action_toggle_torque,
            "teach-record": self._toggle_record,
            "teach-play": self._play,
            "teach-stop": self.app.stop_playback,
            "seq-save": self._save,
            "seq-new": self._new,
            "seq-load": self._load,
        }
        handler = handlers.get(event.button.id or "")
        if handler is not None:
            event.stop()
            handler()

    def _delete(self) -> None:
        row = self._row()
        if row is None:
            return
        self._stop_if_playing()
        del self.sequence.points[row]
        self.refresh_table()
        if self.sequence.points:
            self.query_one(DataTable).move_cursor(row=min(row, len(self.sequence.points) - 1))

    def _clear_all(self) -> None:
        n = len(self.sequence.points)
        if n == 0:
            self.notify("No points to clear", severity="warning")
            return
        seq = self.sequence

        def done(yes: bool | None) -> None:
            if not yes or self.sequence is not seq:
                return
            self._stop_if_playing()
            seq.points.clear()
            seq.kind = "waypoints"
            self.refresh_table()
            self.notify("Cleared all points")

        self.app.push_screen(ConfirmScreen(f"Delete all {n} points?"), done)

    def _move(self, delta: int) -> None:
        row = self._row()
        if row is None or not 0 <= row + delta < len(self.sequence.points):
            return
        self._stop_if_playing()
        pts = self.sequence.points
        pts[row], pts[row + delta] = pts[row + delta], pts[row]
        self.refresh_table()
        self.query_one(DataTable).move_cursor(row=row + delta)

    def _set_dwell(self) -> None:
        row = self._row()
        if row is None:
            self.notify("Select a waypoint first.", severity="warning")
            return
        if self.sequence.kind != "waypoints":
            self.notify("Dwell only applies to waypoint sequences.", severity="warning")
            return
        try:
            dwell = float(self.query_one("#wp-dwell", Input).value)
            if not math.isfinite(dwell) or dwell < 0:
                raise ValueError
        except ValueError:
            self.notify("Dwell must be a number ≥ 0 (seconds).", severity="error")
            return
        self.sequence.points[row].dwell = dwell
        self.refresh_table()
        self.query_one(DataTable).move_cursor(row=row)

    def _toggle_record(self) -> None:
        if self.app.recorder is None:
            self.app.start_recording()
            return
        seq = self.app.stop_recording()
        if not seq.points:
            self.notify("No samples recorded (no feedback?).", severity="warning")
            return
        self.sequence = seq
        self.refresh_table()
        self.notify(f"Recorded {len(seq.points)} samples ({seq.points[-1].t:.1f} s)")

    def _play(self) -> None:
        speed = self.query_one("#teach-speed", Select).value
        loop = self.query_one("#teach-loop", Checkbox).value
        self.app.start_playback(self.sequence, speed=float(speed), loop=loop)

    def _save(self) -> None:
        name = self.query_one("#seq-name", Input).value.strip() or self.sequence.name
        self.sequence.name = name
        try:
            path = save(self.sequence, self.sequence_dir)
        except OSError as e:
            self.notify(f"Could not save: {e}", severity="error", markup=False)
            return
        self.notify(f"Saved {path.name}", markup=False)
        self.refresh_files()
        self.refresh_status()

    def _new(self) -> None:
        self._stop_if_playing()
        self.sequence = Sequence()
        self.query_one("#seq-name", Input).value = ""
        self.refresh_table()

    def _load(self) -> None:
        value = self.query_one("#seq-select", Select).value
        if not isinstance(value, str):
            self.notify("Pick a saved sequence first.", severity="warning")
            return
        try:
            seq = load(Path(value))
        except ValueError as e:
            self.notify(f"Could not load: {e}", severity="error", markup=False)
            return
        self._stop_if_playing()
        self.sequence = seq
        self.query_one("#seq-name", Input).value = seq.name
        self.refresh_table()
        self.notify(f"Loaded {seq.name} ({len(seq.points)} points)", markup=False)
