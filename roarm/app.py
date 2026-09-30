"""RoArmApp: wires the device to the tabs, owns jog target, playback and recording."""

from __future__ import annotations

import asyncio
import ipaddress
import math
import time
from pathlib import Path
from typing import Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, TabbedContent, TabPane

from roarm import protocol as P
from roarm.config import load_config, save_config
from roarm.hub import DeviceHub
from roarm.sequence import SEQUENCE_DIR, Player, Recorder, Sequence
from roarm.ui.control import ControlTab
from roarm.ui.diag import DiagTab
from roarm.ui.teach import TeachTab
from roarm.ui.widgets import ConfirmScreen, StatusBar
from roarm.wifi import WifiDevice

STALE_AFTER = 1.0
HOLD_MAX_AGE = 0.25       # E-stop only holds a pose at least this fresh
NOT_READY_MSG = "Arm not ready — waiting for connection/feedback"
WIFI_PROBES = 20          # T:405 polls after sending new Wi-Fi settings
WIFI_PROBE_EVERY = 1.5    # seconds between polls


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
        Binding("c", "switch_transport", "USB/Wi-Fi"),
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

    def __init__(self, device, sequence_dir: Path = SEQUENCE_DIR, config_path: Path | None = None,
                 wifi_factory: Callable[[str], object] = WifiDevice):
        super().__init__()
        self.device = device
        self.sequence_dir = Path(sequence_dir)
        self.config_path = config_path
        self.wifi_factory = wifi_factory
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
        self.follow_feedback = False
        self._last_warn: dict[str, float] = {}
        self._wifi_probe_timer = None
        self._wifi_probes_left = 0

    @property
    def fresh(self) -> bool:
        """A feedback frame arrived within STALE_AFTER seconds."""
        return self.state is not None and time.monotonic() - self._last_state_time < STALE_AFTER

    @property
    def ready(self) -> bool:
        """Connected, past boot, resynced, and receiving fresh feedback.

        Commands sent while not ready could replay against a stale target after a
        reboot or reconnect — every motion-issuing action must refuse while this
        is False.
        """
        return (self.device.connected and not self.device.booting
                and not self.needs_sync and self.fresh)

    def _warn(self, message: str) -> None:
        """Warning toast, at most once per message every 2 s (slider drags fire many times)."""
        now = time.monotonic()
        if now - self._last_warn.get(message, float("-inf")) >= 2.0:
            self._last_warn[message] = now
            self.notify(message, severity="warning")

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
        if isinstance(self.device, DeviceHub):
            self.device.on_background_status = lambda n, t: self._post(self._handle_background_status, n, t)
        self.device.start()
        self.set_interval(0.05, self._tick)
        self.set_interval(0.25, self._refresh_status)
        self._refresh_status()

    def on_unmount(self) -> None:
        self._stop_wifi_probe()
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
        if self.needs_sync or self.follow_feedback or not self.torque_on or playing:
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
        if direction == "rx":
            self._maybe_learn_wifi(text)

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
        elif text == "online":
            # Wi-Fi reachable: the arm did NOT reboot, so torque is whatever it was
            self.needs_sync = True
            self.notify("Arm reachable over Wi-Fi")
        elif text.startswith("switched:"):
            name = text.split(":", 1)[1]
            if self.player is not None and self.player.running:
                self.stop_playback()
            self.follow_feedback = False
            self.state = None
            self.needs_sync = True
            self._last_state_time = 0.0
            self.notify(f"Now controlling the arm over {DeviceHub.LABELS.get(name, name)}")
        elif text.startswith("cannot open") and text != previous:
            self.notify(text, severity="error", timeout=6, markup=False)
        self._refresh_status()

    def _handle_background_status(self, name: str, text: str) -> None:
        """Status from a transport that is not the active one."""
        if text.startswith("cannot open"):
            return
        if name == "usb" and text == "booting":
            # the USB port was (re)opened, which resets the arm — whatever we were doing is void
            if self.player is not None and self.player.running:
                self.stop_playback()
            self.state = None
            self.needs_sync = True
            self.notify("USB reconnected — the arm is resetting and will home itself", severity="error")
        elif name == "usb" and text == "ready":
            self.torque_on = True       # the arm rebooted with torque on
            try:
                self.query_one(TeachTab).set_torque(True)
            except NoMatches:
                pass
        self._handle_line("sys", f"[{name}] {text}")
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
        if isinstance(d, DeviceHub):
            t.append(f"  [{d.label}]", style="bold #bb9af7")
        t.append(f"  {d.port}", style="#a9b1d6")
        if isinstance(d, DeviceHub) and d.other() is not None:
            t.append("  c: switch", style="#565f89")
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

    # --- transports / Wi-Fi ---------------------------------------------------
    def action_switch_transport(self) -> None:
        hub = self.device
        other = hub.other() if isinstance(hub, DeviceHub) else None
        if other is None:
            self._warn("Only one connection available — start with --wifi, or set up Wi-Fi in Diagnostics")
            return
        if self.recorder is not None:
            self._warn("Stop recording first")
            return
        self._stop_wifi_probe()
        hub.switch(other)

    def _maybe_learn_wifi(self, text: str) -> None:
        """A T:405 reply with a real IP teaches us (and the config) where the arm is on Wi-Fi.

        Only trusted over USB: retargeting the live Wi-Fi link would bypass the switch protocol.
        """
        hub = self.device
        if not isinstance(hub, DeviceHub) or hub.active != "usb":
            return
        msg = P.parse_line(text)
        ip = msg.get("ip") if msg is not None else None
        if not isinstance(ip, str) or ip in ("", "0.0.0.0"):
            return
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return
        self._stop_wifi_probe()
        wifi = hub.devices.get("wifi")
        if wifi is not None and wifi.host == ip:
            return
        if self.config_path is not None:
            cfg = load_config(self.config_path)
            cfg["wifi_host"] = ip
            save_config(cfg, self.config_path)
        if wifi is None:
            hub.add("wifi", self.wifi_factory(ip))
        else:
            wifi.clear_queue()      # inactive: nothing may replay against the new address later
            wifi.set_host(ip)
        self.notify(f"Arm is on Wi-Fi at {ip} — press c to switch", markup=False)

    def configure_wifi(self, ssid: str, password: str) -> bool:
        """Send new STA credentials over USB (AP stays on as a fallback), then poll for the IP."""
        hub = self.device
        if not isinstance(hub, DeviceHub) or "usb" not in hub.devices or hub.active != "usb":
            self._warn("Wi-Fi setup needs the USB connection active — plug in USB and press c")
            return False
        ssid = ssid.strip()
        if not ssid:
            self.notify("Enter the network name (SSID)", severity="error")
            return False
        usb = hub.devices["usb"]
        if not usb.connected or usb.booting:
            self._warn("USB is still connecting — try again in a few seconds")
            return False
        usb.send(P.cmd_wifi_config(ssid, password))
        usb.send(P.cmd_wifi_apply(ssid, password))
        self._stop_wifi_probe()
        self._wifi_probes_left = WIFI_PROBES
        self._wifi_probe_timer = self.set_interval(WIFI_PROBE_EVERY, self._wifi_probe)
        self.notify(f"Sent Wi-Fi settings for {ssid} — waiting for the arm to join…", markup=False)
        return True

    def _wifi_probe(self) -> None:
        hub = self.device
        if not isinstance(hub, DeviceHub) or hub.active != "usb":
            self._stop_wifi_probe()
            return
        if self._wifi_probes_left <= 0:
            self._stop_wifi_probe()
            self.notify("The arm didn't report a Wi-Fi IP — check the SSID and password", severity="error")
            return
        self._wifi_probes_left -= 1
        self.device.send(P.cmd_wifi_info())

    def _stop_wifi_probe(self) -> None:
        if self._wifi_probe_timer is not None:
            self._wifi_probe_timer.stop()
            self._wifi_probe_timer = None

    # --- jogging ------------------------------------------------------------
    def jog(self, joint: str, rad: float) -> None:
        control = self.query_one(ControlTab)
        if not self.ready:
            self._warn(NOT_READY_MSG)
            control.sync_targets(self.target)
            return
        if not self.torque_on:
            self._warn("Torque is off — turn it on (t) to jog.")
            control.sync_targets(self.target)
            return
        self.follow_feedback = False
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
            self._warn(NOT_READY_MSG)
            self.query_one(ControlTab).sync_targets(self.target)
            return
        if not self.torque_on:
            self.notify("Torque is off — turn it on (t) first.", severity="warning")
            return
        self.follow_feedback = False
        self.stop_playback()
        self.device.clear_queue()
        self.target = P.HOME
        self.device.send(P.cmd_home())
        self.query_one(ControlTab).sync_targets(self.target)

    def action_estop(self) -> None:
        if self.player is not None:
            self.player.stop()
        self.follow_feedback = False
        self.device.clear_queue()
        live = self.device.connected and not self.device.booting
        if live and self.torque_on:
            # send_now prepends: queue ends up [torque on, hold] — re-assert torque in case
            # an E-stop right after torque-on just cleared the queued T:210
            if self.state is not None and time.monotonic() - self._last_state_time < HOLD_MAX_AGE:
                self.target = self.state.pose
                self.device.send_now(P.cmd_joints(self.state.pose, spd=0, acc=0))
                self.device.send_now(P.cmd_torque(True))
                self.query_one(ControlTab).sync_targets(self.target)
            else:
                # the pose may be seconds old — holding it would slam the arm there at
                # full speed; keep torque and let the next frame become the target
                self.device.send_now(P.cmd_torque(True))
                self.needs_sync = True
        self.notify("E-STOP — holding position", severity="error")
        self.query_one(TeachTab).refresh_status()

    def _set_torque(self, on: bool) -> None:
        self.follow_feedback = False
        self.device.send(P.cmd_torque(on))
        if on and self.fresh:
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
            self._warn(NOT_READY_MSG)
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
            self._warn(NOT_READY_MSG)
            return False
        if self.recorder is not None:
            self.notify("Stop recording first", severity="warning")
            return False
        self.follow_feedback = False
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
