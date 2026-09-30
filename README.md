# RoArm-M2-S terminal UI

Mouse-driven terminal control for the Waveshare RoArm-M2-S over USB serial.

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/roarm            # real arm (auto-detects the CP210x port)
.venv/bin/roarm --sim      # no hardware
.venv/bin/python -m pytest
```

**Connecting resets the arm** — it reboots and moves to its home pose. Keep it clear.

| Key | Action |
|---|---|
| `1`–`4`, `↑↓`/`ws` | select joint |
| `←→`/`ad`, mouse wheel | jog selected joint |
| click / drag slider | move joint |
| `space` | capture waypoint |
| `h` | home |
| `t` | torque on/off (hand-guiding) |
| `g` | toggle gripper |
| `esc` | E-STOP (stop playback, hold position) |
| `q` | quit |

Sequences are saved in `sequences/*.json`.

The Diagnostics tab's JSON console is a **raw** tool: commands typed there are
sent to the arm unmodified (no clamping, no target tracking). The jog target
resyncs to the next feedback frame afterwards, but the console itself will
happily send anything valid JSON, including out-of-range joint angles.
