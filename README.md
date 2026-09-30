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
follows the arm's reported pose until your next jog, home, playback or E-stop,
but the console itself will happily send anything valid JSON, including
out-of-range joint angles.

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

Note: `roarm --wifi` with the USB cable attached still opens USB, and opening USB resets
the arm (it homes itself). Use `--no-usb` to avoid that.

| Key | Action |
|---|---|
| `c` | switch between USB and Wi-Fi control |

Your PC must be on a network that can reach the arm. The arm's HTTP API has no
authentication — anyone on the same network can drive it.
