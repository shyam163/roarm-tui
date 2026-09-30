"""WifiDevice: drives the arm over its HTTP API (GET /js?json=…).

Same interface as ArmDevice. One request in flight at a time; connecting over
Wi-Fi does not reset the arm, so there is no boot phase.

Commands from the explicit send()/send_now() queue are absolute/idempotent
(poses, home, torque), so a single transient failure gets one retry before the
command is dropped. Jog targets and feedback polls are superseded by the next
one anyway, so they are never retried — only logged on failure. A retry is only
re-queued if clear_queue() (e.g. an E-stop) hasn't run since the command was
popped — tracked via a generation counter bumped inside clear_queue() — so a
stale command can never jump ahead of fresher commands sent after a clear.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.parse
from collections import deque
from typing import Callable

from roarm import protocol as P


def _noop(*_args) -> None:
    pass


def open_http(host: str, timeout: float) -> http.client.HTTPConnection:
    return http.client.HTTPConnection(host, 80, timeout=timeout)


class WifiDevice:
    def __init__(self, host: str, http_factory: Callable[[str, float], object] = open_http,
                 clock: Callable[[], float] = time.monotonic, poll_interval: float = 0.05,
                 jog_interval: float = 0.05, timeout: float = 1.0, max_failures: int = 3,
                 reconnect_delay: float = 2.0, idle_wait: float = 0.005):
        self.host = host
        self.http_factory = http_factory
        self.clock = clock
        self.poll_interval = poll_interval
        self.jog_interval = jog_interval
        self.timeout = timeout
        self.max_failures = max_failures
        self.reconnect_delay = reconnect_delay
        self.idle_wait = idle_wait

        self.on_state: Callable[[P.ArmState], None] = _noop
        self.on_line: Callable[[str, str], None] = _noop
        self.on_status: Callable[[str], None] = _noop

        self.state: P.ArmState | None = None
        self.connected = False
        self.booting = False

        self._conn = None
        self._host_changed = False
        self._failures = 0
        self._queue: deque[dict] = deque()
        self._target: dict | None = None
        self._retry_of: dict | None = None  # queue item currently allowed one retry
        self._urgent = 0  # send_now() items at the head of the queue; they outrank an overdue poll
        self._gen = 0  # bumped by clear_queue(); guards stale retries from a cleared queue
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_poll = float("-inf")
        self._last_target_tx = float("-inf")

    @property
    def port(self) -> str:
        return f"wifi {self.host}"

    # --- public interface -------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="roarm-wifi", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._close_conn()
        self.connected = False

    def send(self, cmd: dict) -> None:
        with self._lock:
            self._queue.append(cmd)

    def send_now(self, cmd: dict) -> None:
        with self._lock:
            self._queue.appendleft(cmd)
            self._urgent += 1

    def set_target(self, pose: P.Pose, spd: int = 0, acc: int = 10) -> None:
        with self._lock:
            self._target = P.cmd_joints(pose, spd=spd, acc=acc)

    def clear_queue(self) -> None:
        with self._lock:
            self._queue.clear()
            self._urgent = 0
            self._target = None
            self._retry_of = None
            self._gen += 1

    def set_host(self, host: str) -> None:
        """Point at a new IP; the worker drops its connection before the next request."""
        self.host = host
        self._host_changed = True

    # --- loop ---------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_once()

    def run_once(self) -> None:
        if self._host_changed:
            self._host_changed = False
            self._close_conn()
        if not self.connected:
            ok, _ = self._request(P.cmd_feedback())
            if ok:
                self.connected = True
                self.clear_queue()  # nothing issued while offline may reach the arm
                self.on_line("sys", f"reached the arm at {self.host}")
                self.on_status("online")
            else:
                self._stop.wait(self.reconnect_delay)
            return
        cmd, source, gen = self._next_cmd(self.clock())
        if cmd is None:
            self._stop.wait(self.idle_wait)
            return
        ok, err = self._request(cmd)
        if ok:
            if source == "queue":
                with self._lock:
                    if cmd is self._retry_of:
                        self._retry_of = None
        elif self.connected:
            self._handle_failed_send(cmd, source, gen, err)

    # --- internals ----------------------------------------------------------
    def _next_cmd(self, now: float) -> tuple[dict, str, int] | tuple[None, None, None]:
        with self._lock:
            gen = self._gen
            if self._urgent > 0 and self._queue:
                self._urgent -= 1
                return self._queue.popleft(), "queue", gen
            self._urgent = 0
            # An overdue poll goes before queued commands / jog targets, so a steady
            # command stream can never starve feedback (E-stop relies on a fresh pose).
            if now - self._last_poll >= self.poll_interval:
                self._last_poll = now
                return P.cmd_feedback(), "poll", gen
            if self._queue:
                return self._queue.popleft(), "queue", gen
            if self._target is not None and now - self._last_target_tx >= self.jog_interval:
                cmd, self._target = self._target, None
                self._last_target_tx = now
                return cmd, "target", gen
        return None, None, None

    def _handle_failed_send(self, cmd: dict, source: str, gen: int, err: Exception) -> None:
        """Called on the worker thread after a failed request that did not disconnect us."""
        if source == "queue":
            requeued = False
            with self._lock:
                if gen == self._gen:
                    # queue hasn't been cleared (e.g. by an E-stop) since we popped cmd
                    if cmd is self._retry_of:
                        # already retried once and failed again — drop it
                        self._retry_of = None
                    else:
                        self._queue.appendleft(cmd)
                        self._retry_of = cmd
                        requeued = True
                # else: clear_queue() ran while this request was in flight — cmd and
                # any retry state are stale, so drop it silently rather than let it
                # jump ahead of whatever was sent after the clear.
            if requeued:
                text = json.dumps(cmd, separators=(",", ":"))
                self.on_line("sys", f"request failed ({err}) — retrying {text}")
        elif source == "target":
            text = json.dumps(cmd, separators=(",", ":"))
            self.on_line("sys", f"jog target failed ({err}): {text}")

    def _request(self, cmd: dict) -> tuple[bool, Exception | None]:
        text = json.dumps(cmd, separators=(",", ":"))
        self.on_line("tx", text)
        try:
            if self._conn is None:
                self._conn = self.http_factory(self.host, self.timeout)
            self._conn.request("GET", "/js?json=" + urllib.parse.quote(text, safe=""))
            resp = self._conn.getresponse()
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status != 200:
                raise http.client.HTTPException(f"HTTP {resp.status}")
        except (OSError, http.client.HTTPException) as e:
            self._close_conn()
            self._failure(e)
            return False, e
        self._failures = 0
        for line in body.splitlines():
            self._handle_line(line.strip())
        return True, None

    def _failure(self, err: Exception) -> None:
        self._failures += 1
        if not self.connected:
            self.on_status(f"cannot open {self.port}: {err}")
        elif self._failures >= self.max_failures:
            self.connected = False
            self.clear_queue()
            self.on_line("sys", f"connection lost: {err}")
            self.on_status("disconnected")

    def _close_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def _handle_line(self, text: str) -> None:
        if not text:
            return
        self.on_line("rx", text)
        msg = P.parse_line(text)
        if msg is None:
            return
        state = P.parse_feedback(msg, self.clock())
        if state is not None:
            self.state = state
            self.on_state(state)
