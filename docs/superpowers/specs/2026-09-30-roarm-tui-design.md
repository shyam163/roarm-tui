# RoArm-M2-S Terminal Control UI — Design

Date: 2026-09-30
Status: Draft for review

## Goal

A polished, mouse-driven terminal UI for the Waveshare RoArm-M2-S connected over
USB serial, used for:

1. **Manual jogging / playing** — move joints and gripper live, see the pose.
2. **Teach & replay** — pose the arm (by hand with torque off, or by jogging),
   capture waypoints or continuous trajectories, replay them.
3. **Diagnostics / tuning** — raw serial log, custom JSON console, load graphs.

Out of scope (YAGNI): Wi-Fi/HTTP transport, Cartesian XYZ target entry panel,
ESP-NOW, multi-arm, flashing firmware, editing on-device mission files.

## Hardware facts (verified on this machine)

- Port `/dev/ttyUSB0` (CP2102N, by-id path
  `/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_*`),
  115200 baud, newline-terminated JSON. User is in `dialout`.
- **Opening the port resets the ESP32** even with DTR/RTS pre-set false. On boot
  the arm homes itself and prints a log ending with `RoArm-M2 started.` /
  `[missionPlay finished.]` (~8–10 s). Consequence: open the port once per
  session; treat open as "arm will reboot and move to home".
- The firmware echoes each command back (`{"T": 105}`) before the reply.
- `{"T":105}` reply (this firmware):
  `{"T":1051,"x":315.2,"y":0,"z":221.9,"b":0,"s":0.023,"e":1.592,"t":3.140,"torB":0,"torS":76,"torE":72,"torH":0}`
  — no `torswitch*` or `v` fields; parse them only if present.

## Protocol subset used

| Purpose | Command |
|---|---|
| Home | `{"T":100}` |
| All joints (rad) | `{"T":102,"base":b,"shoulder":s,"elbow":e,"hand":h,"spd":0,"acc":10}` |
| Single joint (rad) | `{"T":101,"joint":1-4,"rad":r,"spd":0,"acc":10}` |
| Gripper | `{"T":106,"cmd":rad,"spd":0,"acc":0}` |
| Feedback | `{"T":105}` → `T:1051` |
| Torque on/off | `{"T":210,"cmd":1|0}` |
| LED | `{"T":114,"led":0-255}` (unverified on this unit — probe at first use) |
| Info | `{"T":302}` (MAC), `{"T":405}` (Wi-Fi) |

Joint limits (rad): base −3.14…3.14, shoulder −1.57…1.57, elbow 0…3.14,
hand (clamp) 1.08…3.14. The UI shows degrees; the wire uses radians.

"Stop" for E-stop: the firmware has no global stop, so E-stop = abort playback,
clear the outgoing command queue, and command all joints to the **current
feedback pose** (T:102) so the arm holds where it is.

## Architecture

```
roarm/
  protocol.py     pure functions: build commands, parse feedback, limits, deg<->rad
  device.py       ArmDevice: serial I/O thread, boot wait, poll loop, command queue,
                  reconnect; SimDevice with the same interface (--sim)
  sequence.py     Waypoint / Sequence model, JSON save/load, Player (async playback)
  app.py          Textual App: header/footer, tabs, key bindings, device wiring
  ui/
    widgets.py    JointSlider (mouse drag/click/scroll), LoadBar, ArmView (braille)
    control.py    Control tab
    teach.py      Teach & Replay tab
    diag.py       Diagnostics tab
    theme.tcss    styles
  __main__.py     `python -m roarm [--port P] [--sim]`
sequences/        saved sequences (*.json)
tests/            pytest: protocol, sequence, device (against a fake serial)
```

### Boundaries

- **protocol.py** has no I/O; fully unit-tested.
- **ArmDevice** interface (the only thing the UI touches):
  - `start()` / `stop()`
  - `state: ArmState | None` — latest pose (joint rads, xyz, loads, timestamp)
  - `connected: bool`, `booting: bool`
  - `send(cmd: dict)` — enqueue; `send_now(cmd)` bypasses queue (E-stop)
  - `clear_queue()`
  - callbacks: `on_state(ArmState)`, `on_line(direction, text)` for the log,
    `on_status(str)`
  - Runs in a background thread; the app marshals callbacks onto the UI via
    `app.call_from_thread`.
- **Poll loop**: T:105 every 50 ms (20 Hz) when idle between queued commands;
  lines that aren't JSON (boot log, echoes) go to the log only.
- **Jog coalescing**: slider drags generate many targets; the device keeps only
  the latest pending joint target (last-write-wins) and sends at most ~20 Hz, so
  dragging never floods the serial link.
- **SimDevice** integrates targets toward commanded positions over time and
  synthesizes loads, so the entire UI runs without hardware.

## UI

Textual app, dark theme with an accent colour, rounded panels.

- **Header**: title, connection dot (green/amber booting/red), port, torque
  state, LED toggle.
- **Control tab**
  - Four `JointSlider`s (Base, Shoulder, Elbow, Gripper): click to jump, drag,
    mouse-wheel fine step, value in degrees, limits enforced. Slider shows
    commanded target and a ghost marker for actual feedback position.
  - Step selector (1°/5°/15°) and speed selector.
  - `ArmView`: braille-canvas side view (shoulder/elbow/gripper links) and top
    view (base rotation), drawn from feedback joint angles; XYZ readout.
  - Load bars per joint (colour shifts green→amber→red with |load|).
  - Buttons: Home, Torque on/off, Open grip, Close grip, **E-STOP** (red).
- **Teach & Replay tab**
  - Waypoint table (index, b/s/e/h degrees, dwell s); select, delete, move
    up/down, edit dwell.
  - Capture (`space`) adds current feedback pose as a waypoint.
  - Record toggle: captures feedback at 20 Hz into a trajectory sequence.
  - Torque toggle for hand-guiding, with a confirmation modal ("support the arm").
  - Playback: Play / Stop, Loop, speed ×0.25…×2. Waypoint playback sends T:102
    then waits until feedback is within tolerance (or timeout) + dwell.
    Trajectory playback streams T:102 at recorded timing ÷ speed.
  - Save / Load (name input, list of `sequences/*.json`).
- **Diagnostics tab**
  - Scrolling RichLog of TX/RX lines (colour by direction), pause toggle.
  - JSON input with up/down history; validates JSON before send.
  - Sparkline history of loads per joint.
  - Device info panel (MAC, Wi-Fi, firmware reply lines).
- **Keys**: arrows/WASD jog selected joint, `1-4` select joint, `space`
  capture, `h` home, `t` torque, `g` toggle grip, `esc` E-stop, `q` quit.
  Footer lists them.

## Safety

- Commands are clamped to joint limits in `protocol.py` regardless of caller.
- Torque-off requires confirmation. Torque is re-enabled before playback starts.
- Before sending any motion after torque-on or connect, the slider targets are
  synced to feedback so the arm never jumps to a stale target.
- E-stop always available (key + button), works during playback.
- On disconnect: banner, playback aborted, reconnect attempts every 2 s; after
  reconnect, wait for boot, resync targets.
- On quit: stop playback; leave torque as-is (don't drop the arm).

## Sequence file format

```json
{"version": 1, "name": "pick", "kind": "waypoints",
 "points": [{"b": 0.0, "s": 0.1, "e": 1.5, "h": 3.1, "dwell": 0.5}]}
```
`kind: "trajectory"` points carry `"t"` (seconds from start) instead of `dwell`.
Angles in radians.

## Error handling

- Serial open failure → status banner with the reason (permission / not found),
  retry loop; `--sim` suggested in the message.
- Malformed JSON lines → logged, ignored.
- Feedback stale > 1 s → status amber "no feedback".
- Invalid custom JSON in console → inline error, not sent.
- Sequence load errors → notification, nothing changes.

## Testing

- pytest unit tests: command builders + clamping, feedback parsing (with and
  without optional fields), deg/rad, sequence save/load round-trip, player
  timing logic (with a fake clock), device line handling + coalescing against a
  fake serial object.
- UI smoke test with Textual's `App.run_test()` pilot against SimDevice:
  app starts, slider drag changes target, capture adds waypoint, E-stop halts
  playback.
- Manual hardware check: small jogs per joint, capture/replay of 3 waypoints,
  torque off/on with the arm supported.

## Dependencies

Python 3.14, `textual` (8.x), `pyserial` (installed), `pytest`. Project uses a
local venv (`.venv`) and `pyproject.toml`.
