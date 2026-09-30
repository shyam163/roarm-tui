"""Arm transports: ArmDevice (USB serial) and SimDevice (no hardware).

Both run a background thread and share one interface; callbacks fire on that thread.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from typing import Callable

import serial

from roarm import protocol as P

BOOT_MARKERS = ("RoArm-M2 started", "missionPlay finished")
MAX_LINE = 8192


def _noop(*_args) -> None:
    pass


def open_serial(port: str, baud: int) -> serial.Serial:
    s = serial.Serial()
    s.port = port
    s.baudrate = baud
    s.timeout = 0.01
    s.write_timeout = 1.0
    # Pre-set before open; on this board opening still resets the ESP32.
    s.dtr = False
    s.rts = False
    s.open()
    return s


class ArmDevice:
    def __init__(self, port: str, baud: int = 115200,
                 serial_factory: Callable[[str, int], object] = open_serial,
                 clock: Callable[[], float] = time.monotonic,
                 poll_interval: float = 0.05, tx_interval: float = 0.025,
                 boot_timeout: float = 15.0, boot_quiet: float = 2.0,
                 reconnect_delay: float = 2.0, jog_interval: float = 0.05):
        self.port = port
        self.baud = baud
        self.serial_factory = serial_factory
        self.clock = clock
        self.poll_interval = poll_interval
        self.tx_interval = tx_interval
        self.boot_timeout = boot_timeout
        self.boot_quiet = boot_quiet
        self.reconnect_delay = reconnect_delay
        self.jog_interval = jog_interval

        self.on_state: Callable[[P.ArmState], None] = _noop
        self.on_line: Callable[[str, str], None] = _noop
        self.on_status: Callable[[str], None] = _noop

        self.state: P.ArmState | None = None
        self.connected = False
        self.booting = False

        self._ser = None
        self._rxbuf = b""
        self._queue: deque[dict] = deque()
        self._target: dict | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tx = float("-inf")
        self._last_poll = float("-inf")
        self._last_target_tx = float("-inf")
        self._last_rx = 0.0
        self._boot_started = 0.0

    # --- public interface -------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="roarm-serial", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._close()
        self.connected = False

    def send(self, cmd: dict) -> None:
        with self._lock:
            self._queue.append(cmd)

    def send_now(self, cmd: dict) -> None:
        with self._lock:
            self._queue.appendleft(cmd)

    def set_target(self, pose: P.Pose, spd: int = 0, acc: int = 10) -> None:
        with self._lock:
            self._target = P.cmd_joints(pose, spd=spd, acc=acc)

    def clear_queue(self) -> None:
        with self._lock:
            self._queue.clear()
            self._target = None

    # --- loop ---------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_once()

    def run_once(self) -> None:
        if self._ser is None:
            if not self._open():
                self._stop.wait(self.reconnect_delay)
            return
        try:
            self.step()
        except (serial.SerialException, OSError) as e:
            self._lost(e)

    def step(self) -> None:
        """Read what's available, then transmit at most one line."""
        self._read()
        now = self.clock()
        if self.booting:
            if now - self._last_rx >= self.boot_quiet or now - self._boot_started >= self.boot_timeout:
                self._boot_done()
            return
        if now - self._last_tx < self.tx_interval:
            return
        cmd = self._next_cmd(now)
        if cmd is not None:
            self._write(cmd, now)

    # --- internals ----------------------------------------------------------
    def _open(self) -> bool:
        try:
            self._ser = self.serial_factory(self.port, self.baud)
        except (serial.SerialException, OSError) as e:
            self._ser = None
            self.on_status(f"cannot open {self.port}: {e} (use --sim to run without the arm)")
            return False
        now = self.clock()
        self._rxbuf = b""
        self._boot_started = now
        self._last_rx = now
        self.connected = True
        self.booting = True
        self.on_line("sys", f"opened {self.port} — the arm resets and homes on connect")
        self.on_status("booting")
        return True

    def _lost(self, err: Exception) -> None:
        self._close()
        self.connected = False
        self.booting = False
        self.clear_queue()
        self.on_line("sys", f"connection lost: {err}")
        self.on_status("disconnected")

    def _close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except (serial.SerialException, OSError):
                pass
            self._ser = None

    def _boot_done(self) -> None:
        self.booting = False
        self.on_status("ready")

    def _next_cmd(self, now: float) -> dict | None:
        with self._lock:
            if self._queue:
                return self._queue.popleft()
            if self._target is not None and now - self._last_target_tx >= self.jog_interval:
                cmd, self._target = self._target, None
                self._last_target_tx = now
                return cmd
        if now - self._last_poll >= self.poll_interval:
            self._last_poll = now
            return P.cmd_feedback()
        return None

    def _write(self, cmd: dict, now: float) -> None:
        self._ser.write(P.encode(cmd))
        self._last_tx = now
        self.on_line("tx", json.dumps(cmd, separators=(",", ":")))

    def _read(self) -> None:
        data = self._ser.read(self._ser.in_waiting or 1)
        if not data:
            return
        self._last_rx = self.clock()
        self._rxbuf += data
        *lines, self._rxbuf = self._rxbuf.split(b"\n")
        if len(self._rxbuf) > MAX_LINE:
            lines.append(self._rxbuf)
            self._rxbuf = b""
        for raw in lines:
            self._handle_line(raw.decode("utf-8", errors="replace").strip())

    def _handle_line(self, text: str) -> None:
        if not text:
            return
        self.on_line("rx", text)
        if self.booting and any(m in text for m in BOOT_MARKERS):
            self._boot_done()
        msg = P.parse_line(text)
        if msg is None:
            return
        state = P.parse_feedback(msg, self.clock())
        if state is not None:
            self.state = state
            self.on_state(state)
