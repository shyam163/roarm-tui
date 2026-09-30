"""RoArmApp: wires the device to the tabs, owns jog target, playback and recording."""

from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, TabbedContent, TabPane

from roarm import protocol as P
from roarm.sequence import SEQUENCE_DIR, Player, Recorder, Sequence
from roarm.ui.control import ControlTab
from roarm.ui.diag import DiagTab
from roarm.ui.teach import TeachTab
from roarm.ui.widgets import ConfirmScreen, StatusBar

STALE_AFTER = 1.0
NOT_READY_MSG = "Arm not ready — waiting for connection/feedback"


class RoArmApp(App):
    CSS_PATH = "ui/theme.tcss"
    TITLE = "RoArm-M2-S"
    SUB_TITLE = "terminal control"

    BINDINGS = [
        Binding("escape", "estop", "E-STOP", priority=True),
        Binding("q", "quit", "Quit"),
        Binding("h", "home", "Home"),
        Binding("t", "toggle_torque", "Torque"),
        Binding("g", "toggle_grip", "Grip"),
        Binding("space", "capture", "Capture"),
        Binding("left,a", "jog(-1)", "Jog −", key_display="←/a"),
        Binding("right,d", "jog(1)", "Jog +", key_display="→/d"),
        Binding("up,w", "select_delta(-1)", "Prev joint", key_display="↑/w"),
        Binding("down,s", "select_delta(1)", "Next joint", key_display="↓/s"),
        Binding("1", "select_joint(0)", show=False),
        Binding("2", "select_joint(1)", show=False),
        Binding("3", "select_joint(2)", show=False),
        Binding("4", "select_joint(3)", show=False),
    ]

    def __init__(self, device, sequence_dir: Path = SEQUENCE_DIR):
        super().__init__()
        self.device = device
        self.sequence_dir = Path(sequence_dir)
        self.state: P.ArmState | None = None
        self.target: P.Pose = P.HOME
        self.torque_on = True
        self.needs_sync = True
        self.selected = 0
        self.step_deg = 5.0
        self.jog_speed = 1000
        self.led_on = False
        self.player: Player | None = None
        self.recorder: Recorder | None = None
        self.status_text = "starting"
        self._last_state_time = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def ready(self) -> bool:
        """True once connected, past boot, and resynced to a fresh feedback pose.

        Commands sent while not ready could replay against a stale target after a
        reboot or reconnect — every motion-issuing action must refuse while this
        is False.
        """
        return self.device.connected and not self.device.booting and not self.needs_sync

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(id="status")
        with TabbedContent(initial="tab-control"):
            with TabPane("Control", id="tab-control"):
                yield ControlTab()
            with TabPane("Teach & Replay", id="tab-teach"):
                yield TeachTab(self.sequence_dir)
            with TabPane("Diagnostics", id="tab-diag"):
                yield DiagTab()
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "tokyo-night"
        self._loop = asyncio.get_running_loop()
        self.device.on_state = lambda s: self._post(self._handle_state, s)
        self.device.on_line = lambda d, t: self._post(self._handle_line, d, t)
        self.device.on_status = lambda t: self._post(self._handle_status, t)
        self.device.start()
        self.set_interval(0.05, self._tick)
        self.set_interval(0.25, self._refresh_status)
        self._refresh_status()

    def on_unmount(self) -> None:
        if self.player is not None:
            self.player.stop()
        self.device.stop()

    # --- device callbacks (arrive on the device thread) ---------------------
    def _post(self, fn, *args) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            pass  # loop closed during shutdown

    def _handle_state(self, state: P.ArmState) -> None:
        self.state = state
        self._last_state_time = time.monotonic()
        playing = self.player is not None and self.player.running
        if self.needs_sync or not self.torque_on or playing:
            self.target = state.pose
            self.needs_sync = False
        if self.recorder is not None:
            self.recorder.add(time.monotonic(), state.pose)
        try:
            self.query_one(ControlTab).update_state(state, self.target, self.selected)
            self.query_one(DiagTab).add_loads(state.loads)
        except NoMatches:
            return  # widgets not mounted yet / shutting down

    def _handle_line(self, direction: str, text: str) -> None:
        try:
            self.query_one(DiagTab).add_line(direction, text)
        except NoMatches:
            pass

    def _handle_status(self, text: str) -> None:
        previous = self.status_text
        self.status_text = text
        if text == "disconnected":
            if self.player is not None and self.player.running:
                self.stop_playback()
            self.state = None
            self.needs_sync = True
            self.notify("Arm disconnected — reconnecting…", severity="error")
        elif text == "ready":
            self.needs_sync = True
            self.torque_on = True
            try:
                self.query_one(TeachTab).set_torque(True)
            except NoMatches:
                pass  # widgets not mounted yet / shutting down
            self.notify("Arm ready")
        elif text.startswith("cannot open") and text != previous:
            self.notify(text, severity="error", timeout=6, markup=False)
        self._refresh_status()

    # --- periodic -----------------------------------------------------------
    def _tick(self) -> None:
        p = self.player
        if p is None or not p.running:
            return
        pose = self.state.pose if self.state is not None else None
        for cmd in p.tick(time.monotonic(), pose):
            self.device.send(cmd)
        if not p.running:
            self.notify("Playback finished")
        try:
            self.query_one(TeachTab).refresh_status()
        except NoMatches:
            pass  # widgets torn down during shutdown

    def _refresh_status(self) -> None:
        d = self.device
        if not d.connected:
            dot, color, label = "●", "#f87171", "disconnected"
        elif d.booting:
            dot, color, label = "●", "#fbbf24", "booting…"
        elif self.state is None or time.monotonic() - self._last_state_time > STALE_AFTER:
            dot, color, label = "●", "#fbbf24", "no feedback"
        else:
            dot, color, label = "●", "#4ade80", "connected"
        t = Text(no_wrap=True)
        t.append(f"{dot} {label}", style=f"bold {color}")
        t.append(f"  {d.port}", style="#a9b1d6")
        t.append("  │  ", style="#3b4261")
        if self.torque_on:
            t.append("⚡ torque ON", style="bold #4ade80")
        else:
            t.append("✋ torque OFF", style="bold #fbbf24")
        if self.led_on:
            t.append("  │  💡 LED", style="#fbbf24")
        if self.player is not None and self.player.running:
            n = len(self.player.sequence.points)
            t.append(f"  │  ▶ playing {self.player.index + 1}/{n}", style="bold #7dcfff")
        if self.recorder is not None:
            t.append(f"  │  ⏺ recording {len(self.recorder.points)}", style="bold #f87171")
        if not d.connected and self.status_text.startswith("cannot open"):
            t.append(f"  │  {self.status_text}", style="#f87171")
        try:
            self.query_one(StatusBar).update(t)
        except NoMatches:
            pass  # widgets torn down during shutdown

    # --- jogging ------------------------------------------------------------
    def jog(self, joint: str, rad: float) -> None:
        control = self.query_one(ControlTab)
        if not self.ready:
            self.notify(NOT_READY_MSG, severity="warning")
            control.sync_targets(self.target)
            return
        if not self.torque_on:
            self.notify("Torque is off — turn it on (t) to jog.", severity="warning")
            control.sync_targets(self.target)
            return
        if self.player is not None and self.player.running:
            self.stop_playback()
        self.target = self.target.with_joint(joint, rad)
        self.device.set_target(self.target, spd=self.jog_speed, acc=10)
        control.sync_targets(self.target)

    def set_jog_speed(self, spd: int) -> None:
        self.jog_speed = int(spd)

    def set_step(self, deg: float) -> None:
        self.step_deg = float(deg)

    def action_jog(self, direction: int) -> None:
        joint = P.JOINTS[self.selected]
        self.jog(joint, self.target.get(joint) + direction * math.radians(self.step_deg))

    def action_select_joint(self, index: int) -> None:
        self.selected = max(0, min(len(P.JOINTS) - 1, index))
        self.query_one(ControlTab).set_selected(self.selected)

    def action_select_delta(self, delta: int) -> None:
        self.action_select_joint((self.selected + delta) % len(P.JOINTS))

    def action_toggle_grip(self) -> None:
        mid = (P.GRIP_OPEN + P.GRIP_CLOSED) / 2
        self.jog("hand", P.GRIP_OPEN if self.target.hand > mid else P.GRIP_CLOSED)

    def action_grip_open(self) -> None:
        self.jog("hand", P.GRIP_OPEN)

    def action_grip_close(self) -> None:
        self.jog("hand", P.GRIP_CLOSED)

    # --- arm actions --------------------------------------------------------
    def action_home(self) -> None:
        if not self.ready:
            self.notify(NOT_READY_MSG, severity="warning")
            self.query_one(ControlTab).sync_targets(self.target)
            return
        if not self.torque_on:
            self.notify("Torque is off — turn it on (t) first.", severity="warning")
            return
        self.stop_playback()
        self.device.clear_queue()
        self.target = P.HOME
        self.device.send(P.cmd_home())
        self.query_one(ControlTab).sync_targets(self.target)

    def action_estop(self) -> None:
        if self.player is not None:
            self.player.stop()
        self.device.clear_queue()
        if self.ready and self.state is not None and self.torque_on:
            self.target = self.state.pose
            self.device.send_now(P.cmd_joints(self.state.pose, spd=0, acc=0))
            self.query_one(ControlTab).sync_targets(self.target)
        self.notify("E-STOP — holding position", severity="error")
        self.query_one(TeachTab).refresh_status()

    def _set_torque(self, on: bool) -> None:
        self.device.send(P.cmd_torque(on))
        if on and self.state is not None:
            # hold where the hand left it — otherwise the servos can snap to a stale target
            self.device.send(P.cmd_joints(self.state.pose, spd=0, acc=10))
            self.target = self.state.pose
        self.torque_on = on
        self.needs_sync = True
        self.query_one(TeachTab).set_torque(on)
        self._refresh_status()

    def action_toggle_torque(self) -> None:
        if not self.torque_on:
            self._set_torque(True)
            return

        def confirmed(ok: bool | None) -> None:
            if ok:
                self.stop_playback()
                self._set_torque(False)

        self.push_screen(
            ConfirmScreen("Turn torque OFF?\nThe arm will go limp — support it with your hand first."),
            confirmed,
        )

    def action_toggle_led(self) -> None:
        self.led_on = not self.led_on
        self.device.send(P.cmd_led(255 if self.led_on else 0))
        self._refresh_status()

    def action_capture(self) -> None:
        if not self.ready:
            self.notify(NOT_READY_MSG, severity="warning")
            return
        if self.state is None:
            self.notify("No feedback yet — nothing to capture.", severity="warning")
            return
        n = self.query_one(TeachTab).capture(self.state.pose)
        self.notify(f"Captured waypoint {n}")

    # --- playback / recording -----------------------------------------------
    def start_playback(self, seq: Sequence, speed: float = 1.0, loop: bool = False) -> bool:
        if not seq.points:
            self.notify("Nothing to play — capture or load a sequence first.", severity="warning")
            return False
        if not self.ready:
            self.notify(NOT_READY_MSG, severity="warning")
            return False
        if self.recorder is not None:
            self.notify("Stop recording first", severity="warning")
            return False
        self.device.clear_queue()
        if not self.torque_on:
            self._set_torque(True)
        self.player = Player(seq, speed=speed, loop=loop)
        self.player.start(time.monotonic())
        self.query_one(TeachTab).refresh_status()
        return True

    def stop_playback(self) -> None:
        if self.player is not None and self.player.running:
            self.player.stop()
            self.device.clear_queue()
            self.needs_sync = True
        self.query_one(TeachTab).refresh_status()

    def start_recording(self) -> None:
        self.stop_playback()
        self.recorder = Recorder()
        self.query_one(TeachTab).refresh_status()

    def stop_recording(self) -> Sequence:
        rec, self.recorder = self.recorder, None
        self.query_one(TeachTab).refresh_status()
        return rec.to_sequence() if rec is not None else Sequence(kind="trajectory")
