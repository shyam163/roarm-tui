# RoArm Wi-Fi Transport & USB/Wi-Fi Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Control the RoArm-M2-S over Wi-Fi (HTTP) as well as USB, switch between them in the app, configure the arm's Wi-Fi from the app — plus close the safety follow-ups left by the previous plan's final review.

**Architecture:** A new `WifiDevice` (HTTP, same interface as `ArmDevice`) and a `DeviceHub` that holds several transports to the same arm and exposes the active one through that same interface, so `RoArmApp` keeps talking to one `self.device`. A small `config.py` remembers the arm's Wi-Fi IP (never passwords). The CLI builds the hub.

**Tech Stack:** Python 3.14, textual 8.2.8, pyserial 3.5, stdlib `http.client` (no new dependency), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-30-roarm-wifi-design.md` (addendum) and `docs/superpowers/specs/2026-09-30-roarm-tui-design.md` (base) — read both.

## Global Constraints

- Project root `/home/shyam/claudescodes/roarm`, branch `feat/roarm-tui`, venv `.venv` (run `.venv/bin/python -m pytest`).
- NEVER open `/dev/ttyUSB0` (it physically resets the arm) and NEVER contact the arm over the network (`192.168.1.59`, `192.168.4.1`) in tests or scripts. All tests use fakes / `SimDevice`.
- HTTP API: `GET http://<host>/js?json=<urllib.parse.quote(compact_json, safe="")>`; response body = reply JSON line(s). One request in flight at a time. Timeout 1.0 s.
- Wi-Fi setup commands: `{"T":407,"mode":3,"ap_ssid":"RoArm-M2","ap_password":"12345678","sta_ssid":S,"sta_password":P}` (persist) then `{"T":404,"ap_ssid":"RoArm-M2","ap_password":"12345678","sta_ssid":S,"sta_password":P}` (apply now). `{"T":405}` reply: `{"wifi_mode_on_boot":3,"sta_ssid":..,"sta_password":..,"ap_ssid":..,"ap_password":..,"ip":"192.168.1.59","rssi":-40}`. `ip` `"0.0.0.0"` or `""` means not joined.
- Passwords are never written to the repo, config file, logs or sequence files; any `*password`/`*pawword` JSON value shown in the Diagnostics log is masked as `"***"`.
- Config file: `~/.config/roarm/config.json` (honour `XDG_CONFIG_HOME`), mode 0600, JSON object, only key used: `wifi_host`.
- Status strings the app understands: `"booting"`, `"ready"` (serial reboot finished → torque is on), `"online"` (Wi-Fi reachable — no reboot, torque unchanged), `"disconnected"`, `"cannot open …"`, `"switched:<name>"` (hub changed active transport).
- Transport names: `"usb"`, `"wifi"`; labels `USB`, `Wi-Fi`.
- Commit after each task with the given message ending in a blank line then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Stage files explicitly; never `git add -A`.

## Review Focus

1. **Wi-Fi drops mid-jog / mid-playback** (router hiccup, arm out of range) → after 3 consecutive failed requests the device reports `"disconnected"`, the queue is cleared, playback stops, motion is refused until it's back online and resynced. Tests: Task 3 `test_three_failures_disconnect_then_reconnect`, Task 5 `test_wifi_disconnect_stops_playback`.
2. **Switching transport while the arm is moving** → playback stops, both queues are cleared, and no motion is accepted until a fresh feedback frame arrives from the newly active transport (no jump to a pose known only to the other link). Test: Task 5 `test_switch_refuses_motion_until_fresh_feedback`.
3. **Password leakage** — T:405 replies and the T:407/T:404 commands carry passwords in clear text → masked in the log, never in config. Tests: Task 2 `test_mask_secrets`, `test_save_config_drops_secret_keys`; Task 6 `test_log_masks_passwords`.
4. **Arm not reachable at startup** (`--wifi` with LAN off, wrong IP) → a single "cannot open wifi …" toast (deduped), the app keeps running, retries every 2 s. Test: Task 3 `test_unreachable_reports_cannot_open`.
5. **Console motion followed by a jog** → the jog starts from where the arm actually is, not from a pose captured mid-motion. Test: Task 1 `test_console_motion_then_jog_starts_from_actual_pose`.

---

## File Structure

```
roarm/protocol.py     + cmd_wifi_config, cmd_wifi_apply, mask_secrets            (Task 2)
roarm/config.py       NEW load_config / save_config / CONFIG_PATH               (Task 2)
roarm/wifi.py         NEW WifiDevice (HTTP transport)                            (Task 3)
roarm/hub.py          NEW DeviceHub (several transports, one active)            (Task 4)
roarm/app.py          safety follow-ups (Task 1); switching, Wi-Fi learning, configure_wifi (Task 5)
roarm/__main__.py     build_device(): --wifi [HOST], --no-usb, config          (Task 5)
roarm/ui/diag.py      console follow-feedback + float check (Task 1); Wi-Fi panel + masking (Task 6)
roarm/ui/theme.tcss   diag side panel scrolls; Wi-Fi panel styles              (Task 6)
README.md             console note (Task 1); Wi-Fi section (Task 6)
tests/test_safety.py  NEW (Task 1)
tests/test_config.py  NEW (Task 2)       tests/test_protocol.py + (Task 2)
tests/test_wifi.py    NEW (Task 3)       tests/conftest.py + FakeHTTP (Task 3)
tests/test_hub.py     NEW (Task 4)
tests/test_switching.py NEW (Task 5)    tests/test_cli.py NEW (Task 5)
tests/test_diag.py    + (Task 6)
```

---

### Task 1: Safety follow-ups from the previous final review

**Files:**
- Modify: `roarm/app.py`, `roarm/ui/diag.py`, `README.md`
- Create: `tests/test_safety.py`

**Interfaces:**
- Consumes: existing `RoArmApp` (`ready`, `needs_sync`, `_handle_state`, `jog`, `action_home`, `action_estop`, `_set_torque`, `start_playback`, `_last_state_time`, `STALE_AFTER`, `NOT_READY_MSG`), `DiagTab.on_input_submitted`.
- Produces: `RoArmApp.follow_feedback: bool`, `RoArmApp.fresh` property, `RoArmApp._warn(msg: str)` (throttled warning), `ready` now also requires `fresh`. Task 5 relies on `fresh`, `follow_feedback` and `_warn`.

Rulings being implemented (from the ledger):
1. **I1** — after a raw console send, the jog target *follows feedback continuously* until the user's next motion command (jog/slider/grip, home, playback, E-stop, torque toggle), instead of a single-shot resync.
2. `ready` also requires feedback fresher than `STALE_AFTER` (1 s) — covers the missing RX watchdog and I5's freshness check.
3. `_set_torque(True)` sends the hold command only when feedback is fresh.
4. E-stop: always stop the player and clear the queue; if connected, not booting, feedback fresh and `torque_on`, re-send torque-on and then the hold (torque first), so an E-stop right after torque-on can't leave the servos limp.
5. "Not ready" / "torque is off" warnings are throttled to one per message per 2 s (slider drags no longer flood toasts).
6. The console also rejects overflowing float literals (`1e999`).

- [ ] **Step 1: Write the failing tests** — `tests/test_safety.py`:

```python
import math
import time

import pytest
from textual.widgets import Input

from roarm import protocol as P
from roarm.app import STALE_AFTER, RoArmApp
from roarm.device import SimDevice

SIZE = (140, 45)


def make_app(tmp_path, rate=10):
    return RoArmApp(SimDevice(boot_time=0.0, rate=rate), sequence_dir=tmp_path)


async def wait_for(pilot, cond, timeout=4.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await pilot.pause(0.05)


async def console_send(pilot, app, text):
    app.query_one("TabbedContent").active = "tab-diag"
    await pilot.pause()
    console = app.query_one("#console", Input)
    console.focus()
    console.value = text
    await pilot.press("enter")
    app.query_one("TabbedContent").active = "tab-control"
    await pilot.pause()


async def test_console_motion_then_jog_starts_from_actual_pose(tmp_path):
    app = make_app(tmp_path, rate=50)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        await console_send(pilot, app, '{"T":102,"base":1.0,"shoulder":0,"elbow":1.57,"hand":3.14,"spd":0,"acc":10}')
        await wait_for(pilot, lambda: app.state.pose.base > 0.99)
        await wait_for(pilot, lambda: app.target.base > 0.99)   # target followed the arm all the way
        app.query_one("#slider-base").focus()
        await pilot.press("1")
        await pilot.press("d")
        assert app.target.base == pytest.approx(app.state.pose.base + math.radians(app.step_deg), abs=0.02)
        assert app.follow_feedback is False


async def test_follow_feedback_stops_on_home_and_estop(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        app.follow_feedback = True
        app.action_home()
        assert app.follow_feedback is False
        app.follow_feedback = True
        await pilot.press("escape")
        assert app.follow_feedback is False


async def test_ready_requires_fresh_feedback(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        sim = app.device
        sim._stop.set()                                     # feedback stops arriving…
        sim._thread.join()
        sim.connected = True                                # …but the link still looks up
        app._last_state_time = time.monotonic() - STALE_AFTER - 0.5
        assert app.fresh is False
        assert app.ready is False


async def test_torque_on_with_stale_feedback_sends_no_hold(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        app.device.stop()
        app.torque_on = False
        app._last_state_time = time.monotonic() - STALE_AFTER - 0.5
        sent = []
        app.device.send = sent.append
        app.action_toggle_torque()                          # off -> on needs no confirmation
        assert sent == [P.cmd_torque(True)]


async def test_estop_after_torque_on_resends_torque_then_hold(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        front = []
        app.device.send_now = lambda cmd: front.insert(0, cmd)   # mimic appendleft ordering
        app.action_estop()
        assert front[0] == P.cmd_torque(True)
        assert front[1]["T"] == 102


async def test_not_ready_warning_is_throttled(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        app.device.booting = True
        notes = []
        app.notify = lambda *a, **k: notes.append(a[0] if a else k.get("message"))
        for _ in range(10):
            app.jog("base", 0.5)
        assert len(notes) == 1


async def test_console_rejects_overflowing_float(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        sent = []
        app.device.send = sent.append
        await console_send(pilot, app, '{"T":102,"base":1e999}')
        assert sent == []
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/python -m pytest tests/test_safety.py -q`
Expected: failures (`follow_feedback`/`fresh` missing, overflow float accepted, E-stop sends no torque command, many notifications).

- [ ] **Step 3: Implement in `roarm/app.py`**

a) In `__init__` add:
```python
        self.follow_feedback = False
        self._last_warn: dict[str, float] = {}
```

b) Replace the `ready` property and add `fresh` and `_warn`:
```python
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
```

c) In `_handle_state`, make the target follow feedback while `follow_feedback` is set:
```python
        if self.needs_sync or self.follow_feedback or not self.torque_on or playing:
            self.target = state.pose
            self.needs_sync = False
```

d) In `jog()`: replace both `self.notify(..., severity="warning")` calls with `self._warn(...)` (same message strings), and right after the two refusal checks add `self.follow_feedback = False`.

e) In `action_home()`: replace the not-ready `notify` with `self._warn(NOT_READY_MSG)`; after the torque check add `self.follow_feedback = False`.

f) Replace `action_estop` with:
```python
    def action_estop(self) -> None:
        if self.player is not None:
            self.player.stop()
        self.follow_feedback = False
        self.device.clear_queue()
        holdable = self.device.connected and not self.device.booting and self.fresh
        if holdable and self.torque_on:
            self.target = self.state.pose
            # send_now prepends: queue ends up [torque on, hold] — re-assert torque in case
            # an E-stop right after torque-on just cleared the queued T:210
            self.device.send_now(P.cmd_joints(self.state.pose, spd=0, acc=0))
            self.device.send_now(P.cmd_torque(True))
            self.query_one(ControlTab).sync_targets(self.target)
        self.notify("E-STOP — holding position", severity="error")
        self.query_one(TeachTab).refresh_status()
```

g) In `_set_torque`: change `if on and self.state is not None:` to `if on and self.fresh:` and add `self.follow_feedback = False` at the top of the method.

h) In `start_playback`: replace the not-ready `notify` with `self._warn(NOT_READY_MSG)`; add `self.follow_feedback = False` just before `self.device.clear_queue()`.

i) In `action_capture`: replace the not-ready `notify` with `self._warn(NOT_READY_MSG)`.

- [ ] **Step 4: Implement in `roarm/ui/diag.py`**

Add next to `_reject_non_finite`:
```python
def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f"{token} is not a finite number")
    return value
```
(import `math`), change the parse call to
`json.loads(text, parse_constant=_reject_non_finite, parse_float=_finite_float)`, and replace
`self.app.needs_sync = True  # …` with
```python
        self.app.follow_feedback = True  # console is RAW — track the arm until the user's next move
```
Update any existing test in `tests/test_diag.py` that asserts `needs_sync` after a console send to assert `follow_feedback` instead (keep its intent).

- [ ] **Step 5: README** — replace the console paragraph's sentence "The jog target resyncs to the next feedback frame afterwards" with "The jog target follows the arm's reported pose until your next jog, home, playback or E-stop".

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_safety.py tests/test_app.py tests/test_diag.py tests/test_teach.py tests/test_layout.py -q` (×3 for flakiness), then the full suite once.
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add roarm/app.py roarm/ui/diag.py README.md tests/test_safety.py tests/test_diag.py
git commit -m "fix: follow feedback after console moves, require fresh feedback, harden E-stop"
```

---

### Task 2: Wi-Fi protocol helpers, secret masking, config file

**Files:**
- Modify: `roarm/protocol.py`, `tests/test_protocol.py`
- Create: `roarm/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `P.DEFAULT_AP_SSID = "RoArm-M2"`, `P.DEFAULT_AP_PASSWORD = "12345678"`, `P.cmd_wifi_config(ssid, password, ap_ssid=..., ap_password=...) -> dict` (T:407 mode 3), `P.cmd_wifi_apply(ssid, password, ap_ssid=..., ap_password=...) -> dict` (T:404), `P.mask_secrets(text: str) -> str`; `roarm.config.CONFIG_PATH: Path`, `load_config(path=CONFIG_PATH) -> dict`, `save_config(cfg: dict, path=CONFIG_PATH) -> None`.

- [ ] **Step 1: Failing tests** — append to `tests/test_protocol.py`:

```python
def test_wifi_commands():
    assert P.cmd_wifi_config("net", "pw") == {
        "T": 407, "mode": 3, "ap_ssid": "RoArm-M2", "ap_password": "12345678",
        "sta_ssid": "net", "sta_password": "pw"}
    assert P.cmd_wifi_apply("net", "pw") == {
        "T": 404, "ap_ssid": "RoArm-M2", "ap_password": "12345678",
        "sta_ssid": "net", "sta_password": "pw"}


def test_mask_secrets():
    text = '{"sta_ssid":"home","sta_password":"example-pass","ap_pawword":"x","password": "a\\"b","ip":"1.2.3.4"}'
    masked = P.mask_secrets(text)
    assert "example-pass" not in masked and '"x"' not in masked and 'a\\"b' not in masked
    assert masked.count('"***"') == 3
    assert '"sta_ssid":"home"' in masked and '"ip":"1.2.3.4"' in masked
    assert P.mask_secrets("Moving BASE_JOINT") == "Moving BASE_JOINT"
```

`tests/test_config.py`:
```python
import json
import stat

from roarm.config import load_config, save_config


def test_missing_or_corrupt_config_is_empty(tmp_path):
    assert load_config(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert load_config(bad) == {}
    bad.write_text("[1, 2]")
    assert load_config(bad) == {}


def test_round_trip_and_permissions(tmp_path):
    path = tmp_path / "sub" / "config.json"
    save_config({"wifi_host": "192.168.1.59"}, path)
    assert load_config(path) == {"wifi_host": "192.168.1.59"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_config_drops_secret_keys(tmp_path):
    path = tmp_path / "config.json"
    save_config({"wifi_host": "h", "sta_password": "pw", "Password": "x", "ap_pawword": "y"}, path)
    assert json.loads(path.read_text()) == {"wifi_host": "h"}
```

- [ ] **Step 2: Run, verify failure** — `.venv/bin/python -m pytest tests/test_protocol.py tests/test_config.py -q` → ImportError / AttributeError.

- [ ] **Step 3: Implement** — append to `roarm/protocol.py` (add `import re` at the top):

```python
DEFAULT_AP_SSID = "RoArm-M2"
DEFAULT_AP_PASSWORD = "12345678"


def cmd_wifi_config(ssid: str, password: str, ap_ssid: str = DEFAULT_AP_SSID,
                    ap_password: str = DEFAULT_AP_PASSWORD) -> dict:
    """Persist AP+STA mode (the arm keeps its own hotspot as a fallback)."""
    return {"T": 407, "mode": 3, "ap_ssid": ap_ssid, "ap_password": ap_password,
            "sta_ssid": ssid, "sta_password": password}


def cmd_wifi_apply(ssid: str, password: str, ap_ssid: str = DEFAULT_AP_SSID,
                   ap_password: str = DEFAULT_AP_PASSWORD) -> dict:
    """Switch to AP+STA now, without waiting for a reboot."""
    return {"T": 404, "ap_ssid": ap_ssid, "ap_password": ap_password,
            "sta_ssid": ssid, "sta_password": password}


# "sta_password", "ap_password", "password", and the firmware docs' "ap_pawword" typo
_SECRET_RE = re.compile(r'("[A-Za-z_]*pa(?:ss|w)word"\s*:\s*)"(?:[^"\\]|\\.)*"')


def mask_secrets(text: str) -> str:
    return _SECRET_RE.sub(r'\1"***"', text)
```

`roarm/config.py`:
```python
"""User config (~/.config/roarm/config.json). Holds the arm's Wi-Fi host — never passwords."""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "roarm" / "config.json"
_SECRET_WORDS = ("password", "pawword")


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    clean = {k: v for k, v in cfg.items() if not any(w in k.lower() for w in _SECRET_WORDS)}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(clean, f, indent=2)
    os.replace(tmp, path)
```

- [ ] **Step 4: Run** — same command → PASS; full suite once.

- [ ] **Step 5: Commit**

```bash
git add roarm/protocol.py roarm/config.py tests/test_protocol.py tests/test_config.py
git commit -m "feat: Wi-Fi setup commands, secret masking, and config file"
```

---

### Task 3: WifiDevice (HTTP transport)

**Files:**
- Create: `roarm/wifi.py`, `tests/test_wifi.py`
- Modify: `tests/conftest.py` (add `FakeHTTP`)

**Interfaces:**
- Consumes: `roarm.protocol` (`cmd_feedback`, `cmd_joints`, `parse_line`, `parse_feedback`, `ArmState`, `Pose`).
- Produces: `roarm.wifi.open_http(host, timeout)`, `WifiDevice(host, http_factory=open_http, clock=time.monotonic, poll_interval=0.05, jog_interval=0.05, timeout=1.0, max_failures=3, reconnect_delay=2.0, idle_wait=0.005)` with the ArmDevice interface (`start stop send send_now set_target clear_queue`, `state connected booting port`, `on_state on_line on_status`) plus `host`, `set_host(host)`, `run_once()`. `port == f"wifi {host}"`, `booting` is always False. Status: `"online"` on first successful contact (and after every reconnect), `"disconnected"` after `max_failures` consecutive failures while connected, `f"cannot open {port}: {err}"` on each failed probe while not connected.

- [ ] **Step 1: Add the fake** — append to `tests/conftest.py`:

```python
import urllib.parse

FEEDBACK_JSON = ('{"T":1051,"x":315.2,"y":0,"z":221.9,"b":0,"s":0.02,"e":1.59,"t":3.14,'
                 '"torB":0,"torS":76,"torE":72,"torH":0}')


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body.encode()

    def read(self):
        return self._body


class FakeHTTP:
    """Stands in for http.client.HTTPConnection. Replies to T:105 with feedback."""

    def __init__(self):
        self.requests: list[str] = []
        self.fail = False
        self.status = 200
        self.closed = 0
        self.replies = {105: FEEDBACK_JSON}

    def request(self, method, url):
        if self.fail:
            raise ConnectionRefusedError("connection refused")
        assert method == "GET"
        self.requests.append(url)

    def getresponse(self):
        cmd = self.sent()[-1]
        return FakeResponse(self.status, self.replies.get(cmd.get("T"), ""))

    def close(self):
        self.closed += 1

    def sent(self) -> list[dict]:
        return [json.loads(urllib.parse.unquote(u.split("json=", 1)[1])) for u in self.requests]
```

- [ ] **Step 2: Failing tests** — `tests/test_wifi.py`:

```python
import time

from roarm.protocol import HOME, Pose
from roarm.wifi import WifiDevice
from tests.conftest import FakeClock, FakeHTTP


def make(fake=None, clock=None, **kw):
    fake = fake or FakeHTTP()
    clock = clock or FakeClock()
    hosts = []

    def factory(host, timeout):
        hosts.append(host)
        return fake

    dev = WifiDevice("10.0.0.2", http_factory=factory, clock=clock, reconnect_delay=0, idle_wait=0, **kw)
    dev.statuses, dev.lines, dev.states, dev.hosts = [], [], [], hosts
    dev.on_status = dev.statuses.append
    dev.on_line = lambda d, t: dev.lines.append((d, t))
    dev.on_state = dev.states.append
    return dev, fake, clock


def online(dev, fake):
    dev.run_once()
    assert dev.connected
    fake.requests.clear()


def test_first_contact_goes_online_with_state():
    dev, fake, clock = make()
    dev.run_once()
    assert dev.connected and not dev.booting
    assert dev.statuses == ["online"]
    assert fake.sent() == [{"T": 105}]
    assert dev.state is not None and dev.state.loads["shoulder"] == 76
    assert dev.port == "wifi 10.0.0.2"


def test_url_is_percent_encoded():
    dev, fake, clock = make()
    online(dev, fake)
    dev.send({"T": 102, "base": 0.5})
    dev.run_once()
    url = fake.requests[-1]
    assert url.startswith("/js?json=%7B%22T%22%3A102")
    assert "{" not in url and '"' not in url and " " not in url


def test_priority_queue_then_target_then_poll():
    dev, fake, clock = make()
    online(dev, fake)
    dev.set_target(Pose(base=0.5))
    dev.send({"T": 100})
    for _ in range(3):
        clock.advance(0.06)
        dev.run_once()
    sent = fake.sent()
    assert sent[0] == {"T": 100}
    assert sent[1]["T"] == 102 and sent[1]["base"] == 0.5
    assert sent[2] == {"T": 105}


def test_jog_target_limited_to_20hz():
    dev, fake, clock = make()
    online(dev, fake)
    for i in range(6):
        dev.set_target(Pose(base=0.1 * i))
        clock.advance(0.03)
        dev.run_once()
    assert [c["T"] for c in fake.sent()] == [102, 105, 102, 105, 102, 105]


def test_idle_when_nothing_due():
    dev, fake, clock = make()
    online(dev, fake)
    clock.advance(0.06)
    dev.run_once()                     # first poll
    fake.requests.clear()
    dev.run_once()                     # same instant: poll not due, nothing queued
    assert fake.requests == []


def test_unreachable_reports_cannot_open():
    fake = FakeHTTP()
    fake.fail = True
    dev, fake, clock = make(fake)
    dev.run_once()
    assert not dev.connected
    assert dev.statuses[-1].startswith("cannot open wifi 10.0.0.2")


def test_three_failures_disconnect_then_reconnect():
    dev, fake, clock = make()
    online(dev, fake)
    dev.send({"T": 1})
    fake.fail = True
    for _ in range(2):
        clock.advance(0.06)
        dev.run_once()
    assert dev.connected                              # two failures tolerated
    dev.send({"T": 2})
    clock.advance(0.06)
    dev.run_once()
    assert not dev.connected and dev.statuses[-1] == "disconnected"
    assert list(dev._queue) == [] and dev._target is None
    fake.fail = False
    dev.run_once()
    assert dev.connected and dev.statuses[-1] == "online"


def test_http_error_status_counts_as_failure():
    dev, fake, clock = make(max_failures=1)
    online(dev, fake)
    fake.status = 500
    clock.advance(0.06)
    dev.run_once()
    assert not dev.connected


def test_failure_recreates_connection():
    dev, fake, clock = make()
    online(dev, fake)
    fake.fail = True
    clock.advance(0.06)
    dev.run_once()
    fake.fail = False
    clock.advance(0.06)
    dev.run_once()
    assert fake.closed >= 1 and len(dev.hosts) == 2


def test_set_host_reconnects_to_new_host():
    dev, fake, clock = make()
    online(dev, fake)
    dev.set_host("10.0.0.9")
    clock.advance(0.06)
    dev.run_once()
    assert dev.hosts[-1] == "10.0.0.9" and dev.port == "wifi 10.0.0.9"


def test_clear_queue():
    dev, fake, clock = make()
    online(dev, fake)
    dev.send({"T": 1})
    dev.set_target(HOME)
    dev.clear_queue()
    clock.advance(0.06)
    dev.run_once()
    assert fake.sent() == [{"T": 105}]


def test_rx_lines_logged():
    dev, fake, clock = make()
    dev.run_once()
    assert ("tx", '{"T":105}') in dev.lines
    assert any(d == "rx" and t.startswith('{"T":1051') for d, t in dev.lines)


def test_start_stop_thread():
    fake = FakeHTTP()
    dev = WifiDevice("10.0.0.2", http_factory=lambda h, t: fake, reconnect_delay=0)
    dev.start()
    deadline = time.monotonic() + 2
    while not dev.connected and time.monotonic() < deadline:
        time.sleep(0.01)
    dev.stop()
    assert not dev._thread.is_alive()
    assert not dev.connected
```

- [ ] **Step 3: Run, verify failure** — `.venv/bin/python -m pytest tests/test_wifi.py -q` → ImportError.

- [ ] **Step 4: Implement `roarm/wifi.py`**

```python
"""WifiDevice: drives the arm over its HTTP API (GET /js?json=…).

Same interface as ArmDevice. One request in flight at a time; connecting over
Wi-Fi does not reset the arm, so there is no boot phase.
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

    def set_target(self, pose: P.Pose, spd: int = 0, acc: int = 10) -> None:
        with self._lock:
            self._target = P.cmd_joints(pose, spd=spd, acc=acc)

    def clear_queue(self) -> None:
        with self._lock:
            self._queue.clear()
            self._target = None

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
            if self._request(P.cmd_feedback()):
                self.connected = True
                self.clear_queue()  # nothing issued while offline may reach the arm
                self.on_line("sys", f"reached the arm at {self.host}")
                self.on_status("online")
            else:
                self._stop.wait(self.reconnect_delay)
            return
        cmd = self._next_cmd(self.clock())
        if cmd is None:
            self._stop.wait(self.idle_wait)
            return
        self._request(cmd)

    # --- internals ----------------------------------------------------------
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

    def _request(self, cmd: dict) -> bool:
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
            return False
        self._failures = 0
        for line in body.splitlines():
            self._handle_line(line.strip())
        return True

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
```

- [ ] **Step 5: Run** — `.venv/bin/python -m pytest tests/test_wifi.py -q` → PASS; full suite once.

- [ ] **Step 6: Commit**

```bash
git add roarm/wifi.py tests/test_wifi.py tests/conftest.py
git commit -m "feat: WifiDevice HTTP transport"
```

---

### Task 4: DeviceHub

**Files:**
- Create: `roarm/hub.py`, `tests/test_hub.py`

**Interfaces:**
- Consumes: any device with the ArmDevice interface.
- Produces: `DeviceHub(devices: dict[str, device], active: str)` (ValueError if `active` not in devices) with the device interface forwarded to the active device (`start` / `stop` act on **all** devices), attributes `devices`, `active`, properties `device`, `connected`, `booting`, `state`, `port`, `label`, plus `LABELS = {"usb": "USB", "wifi": "Wi-Fi", "sim": "Sim"}`, `other() -> str | None`, `switch(name) -> bool` (clears both queues, emits `on_status(f"switched:{name}")`), `add(name, device)` (wires it, starts it if the hub is started). Callbacks from inactive devices are dropped.

- [ ] **Step 1: Failing tests** — `tests/test_hub.py`:

```python
import pytest

from roarm import protocol as P
from roarm.device import SimDevice
from roarm.hub import DeviceHub


def make():
    usb, wifi = SimDevice(), SimDevice()
    hub = DeviceHub({"usb": usb, "wifi": wifi}, active="usb")
    hub.statuses, hub.states, hub.lines = [], [], []
    hub.on_status = hub.statuses.append
    hub.on_state = hub.states.append
    hub.on_line = lambda d, t: hub.lines.append((d, t))
    return hub, usb, wifi


def test_rejects_unknown_active():
    with pytest.raises(ValueError):
        DeviceHub({"usb": SimDevice()}, active="wifi")


def test_forwards_commands_to_active_only():
    hub, usb, wifi = make()
    hub.send(P.cmd_home())
    hub.set_target(P.HOME.with_joint("base", 1.0))
    assert list(usb._queue) == [P.cmd_home()] and usb._target is not None
    assert list(wifi._queue) == [] and wifi._target is None


def test_callbacks_only_from_active():
    hub, usb, wifi = make()
    usb.step(0.05)
    wifi.step(0.05)
    assert len(hub.states) == 1
    usb.on_status("ready")
    wifi.on_status("disconnected")
    assert hub.statuses == ["ready"]


def test_switch_clears_queues_and_reports():
    hub, usb, wifi = make()
    hub.send(P.cmd_home())
    wifi.send(P.cmd_home())
    assert hub.switch("wifi") is True
    assert hub.active == "wifi" and hub.label == "Wi-Fi"
    assert list(usb._queue) == [] and list(wifi._queue) == []
    assert hub.statuses[-1] == "switched:wifi"
    wifi.step(0.05)
    usb.step(0.05)
    assert len(hub.states) == 1                     # only wifi's frame got through


def test_switch_refuses_unknown_or_same():
    hub, usb, wifi = make()
    assert hub.switch("usb") is False
    assert hub.switch("bluetooth") is False
    assert hub.statuses == []


def test_other_and_properties():
    hub, usb, wifi = make()
    assert hub.other() == "wifi"
    usb.connected, wifi.connected = True, False
    assert hub.connected is True and hub.port == "sim"
    hub.switch("wifi")
    assert hub.connected is False and hub.other() == "usb"
    assert DeviceHub({"usb": SimDevice()}, "usb").other() is None


def test_add_wires_and_starts_when_running():
    hub = DeviceHub({"usb": SimDevice(boot_time=0.0, rate=100)}, "usb")
    hub.start()
    try:
        late = SimDevice(boot_time=0.0, rate=100)
        hub.add("wifi", late)
        assert late._thread is not None and late._thread.is_alive()
        with pytest.raises(ValueError):
            hub.add("wifi", SimDevice())
        seen = []
        hub.on_status = seen.append
        hub.switch("wifi")
        late.on_status("online")
        assert seen[0] == "switched:wifi" and "online" in seen
    finally:
        hub.stop()
```

- [ ] **Step 2: Run, verify failure** — ImportError.

- [ ] **Step 3: Implement `roarm/hub.py`**

```python
"""DeviceHub: several transports to the same arm behind the single-device interface.

Exactly one transport is active: commands go to it and only its callbacks reach the
app. Inactive transports keep running (the USB port must stay open — reopening it
resets the arm) but are ignored.
"""

from __future__ import annotations

from typing import Callable


def _noop(*_args) -> None:
    pass


class DeviceHub:
    LABELS = {"usb": "USB", "wifi": "Wi-Fi", "sim": "Sim"}

    def __init__(self, devices: dict[str, object], active: str):
        if active not in devices:
            raise ValueError(f"unknown transport {active!r}")
        self.devices: dict[str, object] = {}
        self.active = active
        self.on_state: Callable = _noop
        self.on_line: Callable = _noop
        self.on_status: Callable = _noop
        self._started = False
        for name, dev in devices.items():
            self._attach(name, dev)

    def _attach(self, name: str, dev) -> None:
        self.devices[name] = dev
        dev.on_state = lambda s, n=name: self._relay(n, "on_state", s)
        dev.on_line = lambda d, t, n=name: self._relay(n, "on_line", d, t)
        dev.on_status = lambda t, n=name: self._relay(n, "on_status", t)

    def _relay(self, name: str, callback: str, *args) -> None:
        if name == self.active:
            getattr(self, callback)(*args)

    # --- the active device's view ---------------------------------------------
    @property
    def device(self):
        return self.devices[self.active]

    @property
    def connected(self) -> bool:
        return self.device.connected

    @property
    def booting(self) -> bool:
        return self.device.booting

    @property
    def state(self):
        return self.device.state

    @property
    def port(self) -> str:
        return self.device.port

    @property
    def label(self) -> str:
        return self.LABELS.get(self.active, self.active)

    # --- device interface -----------------------------------------------------
    def start(self) -> None:
        self._started = True
        for dev in self.devices.values():
            dev.start()

    def stop(self) -> None:
        for dev in self.devices.values():
            dev.stop()

    def send(self, cmd: dict) -> None:
        self.device.send(cmd)

    def send_now(self, cmd: dict) -> None:
        self.device.send_now(cmd)

    def set_target(self, pose, spd: int = 0, acc: int = 10) -> None:
        self.device.set_target(pose, spd=spd, acc=acc)

    def clear_queue(self) -> None:
        self.device.clear_queue()

    # --- switching --------------------------------------------------------------
    def other(self) -> str | None:
        names = list(self.devices)
        if len(names) < 2:
            return None
        return names[(names.index(self.active) + 1) % len(names)]

    def switch(self, name: str) -> bool:
        if name not in self.devices or name == self.active:
            return False
        self.device.clear_queue()
        self.active = name
        self.device.clear_queue()
        self.on_status(f"switched:{name}")
        return True

    def add(self, name: str, dev) -> None:
        if name in self.devices:
            raise ValueError(f"transport {name!r} already exists")
        self._attach(name, dev)
        if self._started:
            dev.start()
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_hub.py -q` → PASS; full suite once.

- [ ] **Step 5: Commit**

```bash
git add roarm/hub.py tests/test_hub.py
git commit -m "feat: DeviceHub for switching between transports"
```

---

### Task 5: App integration + CLI

**Files:**
- Modify: `roarm/app.py`, `roarm/__main__.py`, `tests/test_app.py` (only `test_cli_parses`, see Step 5)
- Create: `tests/test_switching.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `DeviceHub` (Task 4), `WifiDevice` (Task 3), `load_config/save_config/CONFIG_PATH`, `P.cmd_wifi_config/cmd_wifi_apply/cmd_wifi_info` (Task 2), `fresh/follow_feedback/_warn` (Task 1).
- Produces: `RoArmApp(device, sequence_dir=SEQUENCE_DIR, config_path: Path | None = None, wifi_factory: Callable[[str], object] = WifiDevice)`; `action_switch_transport()` bound to `c`; `configure_wifi(ssid: str, password: str) -> bool` (used by Task 6); status handling for `"online"` and `"switched:<name>"`; module constants `WIFI_PROBES = 20`, `WIFI_PROBE_EVERY = 1.5`. `roarm.__main__.build_device(args, cfg) -> DeviceHub`, `DEFAULT_AP_HOST = "192.168.4.1"`.

- [ ] **Step 1: Failing tests** — `tests/test_switching.py`:

```python
import json
import time

import pytest

from roarm import app as app_module
from roarm import protocol as P
from roarm.app import RoArmApp
from roarm.device import SimDevice
from roarm.hub import DeviceHub
from roarm.sequence import Point, Sequence

SIZE = (140, 45)


class SimWifi(SimDevice):
    """SimDevice standing in for WifiDevice (has host/set_host, reports 'online')."""

    def __init__(self, host="10.0.0.2"):
        super().__init__(boot_time=0.0, rate=10)
        self.host = host

    @property
    def port(self):
        return f"wifi {self.host}"

    def set_host(self, host):
        self.host = host


def make(tmp_path, with_wifi=True):
    usb = SimDevice(boot_time=0.0, rate=10)
    devices = {"usb": usb}
    if with_wifi:
        devices["wifi"] = SimWifi()
    hub = DeviceHub(devices, active="usb")
    app = RoArmApp(hub, sequence_dir=tmp_path, config_path=tmp_path / "config.json",
                   wifi_factory=SimWifi)
    return app, hub


async def wait_for(pilot, cond, timeout=4.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await pilot.pause(0.05)


def status(app):
    return str(app.query_one("#status").render())


async def test_switch_key_moves_control_to_wifi(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        await wait_for(pilot, lambda: "[USB]" in status(app))
        await pilot.press("c")
        await wait_for(pilot, lambda: hub.active == "wifi")
        await wait_for(pilot, lambda: app.ready)
        await wait_for(pilot, lambda: "[Wi-Fi]" in status(app))
        await pilot.press("1")
        await pilot.press("d")
        await wait_for(pilot, lambda: hub.devices["wifi"].state.pose.base > 0.05)
        assert abs(hub.devices["usb"].state.pose.base) < 1e-6


async def test_switch_refuses_motion_until_fresh_feedback(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        app.start_playback(Sequence("s", "waypoints", [Point(P.HOME.with_joint("base", 1.0), 5.0)]))
        hub.switch("wifi")
        app._handle_status("switched:wifi")               # run now; the posted copy runs later (idempotent)
        assert not app.player.running
        assert app.state is None and app.needs_sync
        sent = []
        hub.devices["wifi"].set_target = lambda *a, **k: sent.append(a)
        app.jog("base", 0.5)
        assert sent == []                                 # refused: not ready yet
        await wait_for(pilot, lambda: app.ready)


async def test_switch_refused_with_single_transport(tmp_path):
    app, hub = make(tmp_path, with_wifi=False)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        await pilot.press("c")
        assert hub.active == "usb"


async def test_switch_refused_while_recording(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        app.start_recording()
        await pilot.press("c")
        assert hub.active == "usb"


async def test_wifi_disconnect_stops_playback(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        hub.switch("wifi")
        await wait_for(pilot, lambda: app.ready)
        app.start_playback(Sequence("s", "waypoints", [Point(P.HOME, 5.0)]))
        hub.devices["wifi"].connected = False
        hub.devices["wifi"].on_status("disconnected")
        await wait_for(pilot, lambda: not app.player.running)
        assert not app.ready


async def test_learns_wifi_ip_from_t405_reply(tmp_path):
    app, hub = make(tmp_path, with_wifi=False)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        hub.devices["usb"].on_line("rx", '{"wifi_mode_on_boot":3,"sta_ssid":"home","sta_password":"pw",'
                                         '"ap_ssid":"RoArm-M2","ap_password":"12345678","ip":"192.168.1.59","rssi":-40}')
        await wait_for(pilot, lambda: "wifi" in hub.devices)
        assert hub.devices["wifi"].host == "192.168.1.59"
        assert hub.devices["wifi"]._thread is not None          # started
        cfg = json.loads((tmp_path / "config.json").read_text())
        assert cfg == {"wifi_host": "192.168.1.59"}
        await pilot.press("c")
        await wait_for(pilot, lambda: hub.active == "wifi")


async def test_learn_updates_existing_host_and_ignores_zero_ip(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        usb = hub.devices["usb"]
        usb.on_line("rx", '{"ip":"0.0.0.0","rssi":0}')
        await pilot.pause()
        assert hub.devices["wifi"].host == "10.0.0.2"
        usb.on_line("rx", '{"ip":"192.168.1.77","rssi":-50}')
        await wait_for(pilot, lambda: hub.devices["wifi"].host == "192.168.1.77")


async def test_configure_wifi_requires_usb_active(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        hub.switch("wifi")
        await pilot.pause()
        assert app.configure_wifi("home", "pw") is False
    plain = RoArmApp(SimDevice(boot_time=0.0, rate=10), sequence_dir=tmp_path)
    async with plain.run_test(size=SIZE) as pilot:
        assert plain.configure_wifi("home", "pw") is False


async def test_configure_wifi_sends_setup_then_probes(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "WIFI_PROBE_EVERY", 0.1)
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        usb = hub.devices["usb"]
        sent = []
        real_send = usb.send
        usb.send = lambda cmd: (sent.append(cmd), real_send(cmd))
        assert app.configure_wifi("", "pw") is False
        assert app.configure_wifi("home", "pw") is True
        assert sent[0] == P.cmd_wifi_config("home", "pw")
        assert sent[1] == P.cmd_wifi_apply("home", "pw")
        await wait_for(pilot, lambda: P.cmd_wifi_info() in sent)
        assert not (tmp_path / "config.json").exists() or "pw" not in (tmp_path / "config.json").read_text()


async def test_online_status_does_not_claim_torque(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        app.torque_on = False
        app._handle_status("online")                      # synchronously: a feedback frame would clear needs_sync
        assert app.torque_on is False and app.needs_sync
```

`tests/test_cli.py`:
```python
import argparse

import pytest

from roarm import __main__ as cli
from roarm.device import ArmDevice
from roarm.hub import DeviceHub
from roarm.wifi import WifiDevice


def args(**kw):
    base = dict(port=None, baud=115200, sim=False, wifi=None, no_usb=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def usb_present(monkeypatch):
    monkeypatch.setattr(cli, "default_port", lambda: "/dev/fake-usb")
    monkeypatch.setattr(cli, "port_exists", lambda p: p == "/dev/fake-usb")


@pytest.fixture
def usb_absent(monkeypatch):
    monkeypatch.setattr(cli, "default_port", lambda: "/dev/fake-usb")
    monkeypatch.setattr(cli, "port_exists", lambda p: False)


def test_usb_only_without_config(usb_present):
    hub = cli.build_device(args(), {})
    assert isinstance(hub, DeviceHub) and list(hub.devices) == ["usb"] and hub.active == "usb"
    assert isinstance(hub.devices["usb"], ArmDevice)


def test_config_host_adds_inactive_wifi(usb_present):
    hub = cli.build_device(args(), {"wifi_host": "192.168.1.59"})
    assert set(hub.devices) == {"usb", "wifi"} and hub.active == "usb"
    assert isinstance(hub.devices["wifi"], WifiDevice) and hub.devices["wifi"].host == "192.168.1.59"


def test_wifi_flag_with_host_starts_on_wifi(usb_present):
    hub = cli.build_device(args(wifi="10.0.0.5"), {"wifi_host": "192.168.1.59"})
    assert hub.active == "wifi" and hub.devices["wifi"].host == "10.0.0.5"
    assert "usb" in hub.devices


def test_wifi_flag_without_host_uses_config_then_ap(usb_absent):
    assert cli.build_device(args(wifi=""), {"wifi_host": "192.168.1.59"}).devices["wifi"].host == "192.168.1.59"
    hub = cli.build_device(args(wifi=""), {})
    assert hub.devices["wifi"].host == cli.DEFAULT_AP_HOST
    assert list(hub.devices) == ["wifi"]                  # no USB plugged in → Wi-Fi only


def test_no_usb(usb_present):
    hub = cli.build_device(args(no_usb=True, wifi=""), {"wifi_host": "h"})
    assert list(hub.devices) == ["wifi"]
    with pytest.raises(SystemExit):
        cli.build_device(args(no_usb=True), {})


def test_explicit_port_always_used(usb_absent):
    hub = cli.build_device(args(port="/dev/ttyACM9"), {})
    assert hub.devices["usb"].port == "/dev/ttyACM9"


def test_no_transport_found_falls_back_to_usb_retry(usb_absent):
    hub = cli.build_device(args(), {})
    assert list(hub.devices) == ["usb"]                   # keeps retrying + shows "cannot open"


def test_main_sim_and_hub(monkeypatch, usb_present):
    ran = {}
    monkeypatch.setattr(cli.RoArmApp, "run", lambda self: ran.setdefault("app", self))
    monkeypatch.setattr(cli, "load_config", lambda: {})
    cli.main(["--sim"])
    assert type(ran.pop("app").device).__name__ == "SimDevice"
    cli.main(["--wifi", "10.0.0.5"])
    app = ran.pop("app")
    assert isinstance(app.device, DeviceHub) and app.device.active == "wifi"
    assert app.config_path == cli.CONFIG_PATH
```

- [ ] **Step 2: Run, verify failure** — `.venv/bin/python -m pytest tests/test_switching.py tests/test_cli.py -q`.

- [ ] **Step 3: Implement in `roarm/app.py`**

Imports:
```python
from typing import Callable

from roarm.config import load_config, save_config
from roarm.hub import DeviceHub
from roarm.wifi import WifiDevice
```
Module constants (next to `STALE_AFTER`):
```python
WIFI_PROBES = 20          # T:405 polls after sending new Wi-Fi settings
WIFI_PROBE_EVERY = 1.5    # seconds between polls
```
Binding (after the `g` binding): `Binding("c", "switch_transport", "USB/Wi-Fi"),`

`__init__` signature and additions:
```python
    def __init__(self, device, sequence_dir: Path = SEQUENCE_DIR, config_path: Path | None = None,
                 wifi_factory: Callable[[str], object] = WifiDevice):
        ...existing...
        self.config_path = config_path
        self.wifi_factory = wifi_factory
        self._wifi_probe_timer = None
        self._wifi_probes_left = 0
```

`_handle_line` — after passing the line to DiagTab, call `self._maybe_learn_wifi(text)` (outside the NoMatches try so it runs even if the tab is gone):
```python
    def _handle_line(self, direction: str, text: str) -> None:
        try:
            self.query_one(DiagTab).add_line(direction, text)
        except NoMatches:
            pass
        if direction == "rx":
            self._maybe_learn_wifi(text)
```

`_handle_status` — add two branches before the `"cannot open"` branch:
```python
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
```

`_refresh_status` — right after the dot/label append, before the port:
```python
        if isinstance(d, DeviceHub):
            t.append(f"  [{d.label}]", style="bold #bb9af7")
```
and after the port:
```python
        if isinstance(d, DeviceHub) and d.other() is not None:
            t.append("  c: switch", style="#565f89")
```

New methods (put them in a `# --- transports / Wi-Fi ---` section):
```python
    def action_switch_transport(self) -> None:
        hub = self.device
        other = hub.other() if isinstance(hub, DeviceHub) else None
        if other is None:
            self._warn("Only one connection available — start with --wifi, or set up Wi-Fi in Diagnostics")
            return
        if self.recorder is not None:
            self._warn("Stop recording first")
            return
        hub.switch(other)

    def _maybe_learn_wifi(self, text: str) -> None:
        """A T:405 reply with a real IP teaches us (and the config) where the arm is on Wi-Fi."""
        hub = self.device
        if not isinstance(hub, DeviceHub):
            return
        msg = P.parse_line(text)
        ip = msg.get("ip") if msg is not None else None
        if not isinstance(ip, str) or ip in ("", "0.0.0.0"):
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
        usb.send(P.cmd_wifi_config(ssid, password))
        usb.send(P.cmd_wifi_apply(ssid, password))
        self._stop_wifi_probe()
        self._wifi_probes_left = WIFI_PROBES
        self._wifi_probe_timer = self.set_interval(WIFI_PROBE_EVERY, self._wifi_probe)
        self.notify(f"Sent Wi-Fi settings for {ssid} — waiting for the arm to join…", markup=False)
        return True

    def _wifi_probe(self) -> None:
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
```
Note: `WIFI_PROBE_EVERY` must be read at call time (module global), so the test's monkeypatch works.

- [ ] **Step 4: Implement `roarm/__main__.py`**

```python
"""CLI entry point: python -m roarm [--port PORT] [--wifi [HOST]] [--no-usb] [--sim]"""

from __future__ import annotations

import argparse
import glob
import os

from roarm.app import RoArmApp
from roarm.config import CONFIG_PATH, load_config
from roarm.device import ArmDevice, SimDevice
from roarm.hub import DeviceHub
from roarm.wifi import WifiDevice

DEFAULT_AP_HOST = "192.168.4.1"   # the arm's own hotspot


def default_port() -> str:
    matches = sorted(glob.glob("/dev/serial/by-id/*CP210*"))
    return matches[0] if matches else "/dev/ttyUSB0"


def port_exists(path: str) -> bool:
    return os.path.exists(path)


def build_device(args: argparse.Namespace, cfg: dict) -> DeviceHub:
    saved_host = cfg.get("wifi_host") if isinstance(cfg.get("wifi_host"), str) else None
    if args.wifi is not None:
        wifi_host = args.wifi or saved_host or DEFAULT_AP_HOST
    else:
        wifi_host = saved_host
    devices: dict[str, object] = {}
    if not args.no_usb:
        port = args.port or default_port()
        # opening a port resets the arm — only open one that exists, unless it's our only option
        if args.port or port_exists(port) or wifi_host is None:
            devices["usb"] = ArmDevice(port, args.baud)
    if wifi_host:
        devices["wifi"] = WifiDevice(wifi_host)
    if not devices:
        raise SystemExit("--no-usb needs a Wi-Fi host: use --wifi HOST")
    active = "wifi" if "wifi" in devices and (args.wifi is not None or "usb" not in devices) else "usb"
    return DeviceHub(devices, active)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="roarm", description="Terminal UI for the Waveshare RoArm-M2-S")
    ap.add_argument("--port", default=None, help="serial port (default: auto-detect CP210x, else /dev/ttyUSB0)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--wifi", nargs="?", const="", default=None, metavar="HOST",
                    help="control over Wi-Fi (HOST, else the saved IP, else the arm's hotspot 192.168.4.1)")
    ap.add_argument("--no-usb", action="store_true", help="don't open the serial port (avoids the reset)")
    ap.add_argument("--sim", action="store_true", help="run against a simulated arm")
    args = ap.parse_args(argv)
    if args.sim:
        RoArmApp(SimDevice()).run()
        return
    RoArmApp(build_device(args, load_config()), config_path=CONFIG_PATH).run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Update the old CLI test** — in `tests/test_app.py`, `test_cli_parses` now receives a `DeviceHub` for `--port /dev/nothing`: change its last assertion to `assert ran["device"].devices["usb"].port == "/dev/nothing"` and monkeypatch `cli.load_config` to return `{}` (so a real `~/.config/roarm/config.json` can't add Wi-Fi). If the test body differs, keep its intent: `--sim` → SimDevice; `--port X` → USB device on port X.

- [ ] **Step 6: Run** — `.venv/bin/python -m pytest tests/test_switching.py tests/test_cli.py tests/test_app.py -q` (×3), then the full suite.

- [ ] **Step 7: Commit**

```bash
git add roarm/app.py roarm/__main__.py tests/test_switching.py tests/test_cli.py tests/test_app.py
git commit -m "feat: switch between USB and Wi-Fi control, learn the arm's Wi-Fi IP"
```

---

### Task 6: Diagnostics Wi-Fi panel, log masking, README

**Files:**
- Modify: `roarm/ui/diag.py`, `roarm/ui/theme.tcss`, `README.md`, `tests/test_diag.py`, `tests/test_layout.py` (only if the scroll container changes a region assertion)

**Interfaces:**
- Consumes: `RoArmApp.configure_wifi(ssid, password) -> bool` (Task 5), `P.mask_secrets` (Task 2).
- Produces: widget ids `#wifi-ssid` (Input), `#wifi-password` (Input, `password=True`), `#wifi-join` (Button); `#diag-side` becomes a `VerticalScroll`; device info shows `ssid` when present.

- [ ] **Step 1: Failing tests** — append to `tests/test_diag.py` (reuse its `wait_for`, `open_diag`, `make_app` helpers):

```python
def log_text(tab):
    return "\n".join("".join(seg.text for seg in strip) for strip in tab.query_one(RichLog).lines)


async def test_log_masks_passwords(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        tab.add_line("tx", '{"T":407,"mode":3,"sta_ssid":"home","sta_password":"example-pass"}')
        tab.add_line("rx", '{"wifi_mode_on_boot":3,"sta_ssid":"home","sta_password":"example-pass","ip":"1.2.3.4","rssi":-40}')
        await pilot.pause()
        text = log_text(tab)
        assert "example-pass" not in text and '"***"' in text
        info = str(tab.query_one("#dev-info").render())
        assert "ssid home" in info and "example-pass" not in info


async def test_join_button_calls_configure_wifi_and_clears_password(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        calls = []
        app.configure_wifi = lambda ssid, pw: calls.append((ssid, pw)) or True
        tab.query_one("#wifi-ssid", Input).value = "home"
        tab.query_one("#wifi-password", Input).value = "secret"
        tab.query_one("#wifi-join", Button).press()
        await pilot.pause()
        assert calls == [("home", "secret")]
        assert tab.query_one("#wifi-password", Input).value == ""
        assert tab.query_one("#wifi-password", Input).password is True


async def test_join_failure_keeps_inputs(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        app.configure_wifi = lambda ssid, pw: False
        tab.query_one("#wifi-ssid", Input).value = "home"
        tab.query_one("#wifi-password", Input).value = "secret"
        tab.query_one("#wifi-join", Button).press()
        await pilot.pause()
        assert tab.query_one("#wifi-password", Input).value == "secret"
```
(Add `Button` to the `textual.widgets` import in the test file if missing.)

- [ ] **Step 2: Run, verify failure** — `.venv/bin/python -m pytest tests/test_diag.py -q`.

- [ ] **Step 3: Implement in `roarm/ui/diag.py`**

- Import `VerticalScroll` from `textual.containers`.
- In `compose`, change `with Vertical(id="diag-side", classes="panel"):` to `with VerticalScroll(id="diag-side", classes="panel"):` and append after the "Query device info" button:
```python
                yield Label("Wi-Fi setup (over USB)", classes="section-title")
                yield Input(placeholder="network name (SSID)", id="wifi-ssid")
                yield Input(placeholder="password", password=True, id="wifi-password")
                yield Button("Join network", id="wifi-join", variant="primary")
```
- At the top of `add_line`: `text = P.mask_secrets(text)`.
- In `_capture_info`, build the Wi-Fi line with the SSID when present:
```python
            if msg is not None:
                wifi = f"ip {msg.get('ip')}  rssi {msg.get('rssi')}"
                if msg.get("sta_ssid"):
                    wifi += f"  ssid {msg['sta_ssid']}"
                self._info["Wi-Fi"] = wifi
```
- In `on_button_pressed` add:
```python
        elif event.button.id == "wifi-join":
            event.stop()
            ssid = self.query_one("#wifi-ssid", Input).value
            password_input = self.query_one("#wifi-password", Input)
            if self.app.configure_wifi(ssid, password_input.value):
                password_input.value = ""  # don't keep the password around in the UI
```

- [ ] **Step 4: Styles** — append to `roarm/ui/theme.tcss`:
```css
.section-title {
    margin-top: 1;
    text-style: bold;
    color: $accent;
}

#diag-side Input, #diag-side Button {
    width: 100%;
}
```

- [ ] **Step 5: README** — add a section after the key table:

````markdown
## Wi-Fi

The arm runs its own hotspot `RoArm-M2` (password `12345678`, arm at `192.168.4.1`) and can
also join your network. Set that up once over USB: **Diagnostics → Wi-Fi setup → Join
network**. The arm keeps its hotspot as a fallback; the IP it gets is saved to
`~/.config/roarm/config.json` (passwords are never saved).

```bash
.venv/bin/roarm                 # USB, plus Wi-Fi if an IP is saved — press c to switch
.venv/bin/roarm --wifi          # start on Wi-Fi (saved IP, else the hotspot)
.venv/bin/roarm --wifi 192.168.1.59 --no-usb   # Wi-Fi only; never opens USB (no reset)
```

| Key | Action |
|---|---|
| `c` | switch between USB and Wi-Fi control |

Your PC must be on a network that can reach the arm. The arm's HTTP API has no
authentication — anyone on the same network can drive it.
````

- [ ] **Step 6: Run** — `.venv/bin/python -m pytest tests/test_diag.py tests/test_layout.py -q` (×3), full suite once. Take sim screenshots of the Diagnostics tab at 140×45 and 80×24 (SimDevice only) to `/tmp/roarm-screenshots/` and confirm the Wi-Fi panel is reachable by scrolling and nothing overlaps the footer.

- [ ] **Step 7: Commit**

```bash
git add roarm/ui/diag.py roarm/ui/theme.tcss README.md tests/test_diag.py tests/test_layout.py
git commit -m "feat: Wi-Fi setup panel in Diagnostics, mask passwords in the log"
```

---

### Task 7: Supervised hardware check (controller + user only — not a subagent task)

Combines the previous plan's Task 10 with the Wi-Fi checks.

- [ ] Ask the user to re-enable LAN (PC on `shinjitham`), confirm the workspace around the arm is clear, warn that USB connect resets the arm.
- [ ] User runs `.venv/bin/roarm` in their own terminal. Status: `[USB]` connected, `c: switch` shown.
- [ ] Jog each joint 5° on USB; grip open/close (swap `GRIP_OPEN/GRIP_CLOSED` if reversed); LED button (T:114 unverified).
- [ ] Press `c` → `[Wi-Fi]`, connected; jog each joint 5°; check responsiveness.
- [ ] Unplug USB while on Wi-Fi → Wi-Fi control keeps working. Replug → USB reconnects in the background (arm resets! warn first).
- [ ] Torque off (supported), hand-guide, capture 3 waypoints, torque on (arm must not snap), play at ×0.5; record ~3 s and replay; `esc` mid-playback holds.
- [ ] `roarm --wifi --no-usb` from a fresh start → no reset, arm controllable.
- [ ] Hardware notes from the previous final review: boot timing vs `boot_quiet`, polls during `h` homing, waypoint tolerance under load.
