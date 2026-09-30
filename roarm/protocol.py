"""RoArm-M2-S JSON protocol: joint limits, command builders, feedback parsing, kinematics.

Pure functions only — no I/O. Angles on the wire are radians.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace

JOINTS = ("base", "shoulder", "elbow", "hand")
JOINT_LABELS = {"base": "Base", "shoulder": "Shoulder", "elbow": "Elbow", "hand": "Gripper"}
JOINT_IDS = {"base": 1, "shoulder": 2, "elbow": 3, "hand": 4}
LIMITS = {
    "base": (-3.14, 3.14),
    "shoulder": (-1.57, 1.57),
    "elbow": (0.0, 3.14),
    "hand": (1.08, 3.14),
}
GRIP_OPEN = 1.08
GRIP_CLOSED = 3.14

_POSE_KEYS = {"base": "b", "shoulder": "s", "elbow": "e", "hand": "h"}
_FEEDBACK_KEYS = {"base": "b", "shoulder": "s", "elbow": "e", "hand": "t"}
_LOAD_KEYS = {"base": "torB", "shoulder": "torS", "elbow": "torE", "hand": "torH"}
_TORQUE_KEYS = {"base": "torswitchB", "shoulder": "torswitchS", "elbow": "torswitchE", "hand": "torswitchH"}

# Approximate link lengths (mm) chosen so the home pose lands near the arm's
# reported home point (x≈315, z≈222). Used for drawing and the simulator only.
L_UPPER = 238.0
L_FORE = 316.0
REACH = L_UPPER + L_FORE


def clamp_joint(joint: str, rad: float) -> float:
    rad = float(rad)
    if not math.isfinite(rad):
        raise ValueError(f"{joint}: angle must be finite, got {rad}")
    lo, hi = LIMITS[joint]
    return min(hi, max(lo, rad))


@dataclass(frozen=True)
class Pose:
    base: float = 0.0
    shoulder: float = 0.0
    elbow: float = 1.57
    hand: float = 3.14

    def get(self, joint: str) -> float:
        return getattr(self, joint)

    def with_joint(self, joint: str, rad: float) -> Pose:
        return replace(self, **{joint: clamp_joint(joint, rad)})

    def clamped(self) -> Pose:
        return Pose(**{j: clamp_joint(j, self.get(j)) for j in JOINTS})

    def max_diff(self, other: Pose) -> float:
        return max(abs(self.get(j) - other.get(j)) for j in JOINTS)

    def to_dict(self) -> dict[str, float]:
        return {_POSE_KEYS[j]: round(self.get(j), 4) for j in JOINTS}

    @classmethod
    def from_dict(cls, d: dict) -> Pose:
        return cls(**{j: clamp_joint(j, d[_POSE_KEYS[j]]) for j in JOINTS})


HOME = Pose()


@dataclass(frozen=True)
class ArmState:
    pose: Pose
    x: float
    y: float
    z: float
    loads: dict[str, int]
    timestamp: float
    torque: dict[str, bool] | None = None
    voltage: float | None = None


def cmd_home() -> dict:
    return {"T": 100}


def cmd_joints(pose: Pose, spd: int = 0, acc: int = 10) -> dict:
    p = pose.clamped()
    return {"T": 102, "base": round(p.base, 4), "shoulder": round(p.shoulder, 4),
            "elbow": round(p.elbow, 4), "hand": round(p.hand, 4), "spd": int(spd), "acc": int(acc)}


def cmd_joint(joint: str, rad: float, spd: int = 0, acc: int = 10) -> dict:
    return {"T": 101, "joint": JOINT_IDS[joint], "rad": round(clamp_joint(joint, rad), 4),
            "spd": int(spd), "acc": int(acc)}


def cmd_gripper(rad: float, spd: int = 0, acc: int = 0) -> dict:
    return {"T": 106, "cmd": round(clamp_joint("hand", rad), 4), "spd": int(spd), "acc": int(acc)}


def cmd_feedback() -> dict:
    return {"T": 105}


def cmd_torque(on: bool) -> dict:
    return {"T": 210, "cmd": 1 if on else 0}


def cmd_led(level: int) -> dict:
    return {"T": 114, "led": max(0, min(255, int(level)))}


def cmd_mac() -> dict:
    return {"T": 302}


def cmd_wifi_info() -> dict:
    return {"T": 405}


def encode(cmd: dict) -> bytes:
    return (json.dumps(cmd, separators=(",", ":")) + "\n").encode()


def parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_feedback(msg: dict, timestamp: float) -> ArmState | None:
    if msg.get("T") != 1051:
        return None
    try:
        pose = Pose(**{j: float(msg[k]) for j, k in _FEEDBACK_KEYS.items()})
        x, y, z = float(msg["x"]), float(msg["y"]), float(msg["z"])
        loads = {j: int(msg.get(k, 0)) for j, k in _LOAD_KEYS.items()}
    except (KeyError, TypeError, ValueError):
        return None
    torque = None
    if all(k in msg for k in _TORQUE_KEYS.values()):
        torque = {j: bool(msg[k]) for j, k in _TORQUE_KEYS.items()}
    voltage = float(msg["v"]) / 100 if isinstance(msg.get("v"), (int, float)) else None
    return ArmState(pose, x, y, z, loads, timestamp, torque, voltage)


def planar_points(pose: Pose) -> list[tuple[float, float]]:
    """(reach, height) of shoulder, elbow and gripper tip in the arm's vertical plane.

    Shoulder angle is measured from vertical (positive = forward); the elbow angle is
    relative to the upper arm (pi/2 = forearm horizontal at home, larger = down).
    """
    r1 = L_UPPER * math.sin(pose.shoulder)
    z1 = L_UPPER * math.cos(pose.shoulder)
    a = pose.shoulder + pose.elbow
    return [(0.0, 0.0), (r1, z1), (r1 + L_FORE * math.sin(a), z1 + L_FORE * math.cos(a))]


def forward_kinematics(pose: Pose) -> tuple[float, float, float]:
    r, z = planar_points(pose)[2]
    return r * math.cos(pose.base), r * math.sin(pose.base), z


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
_SECRET_RE = re.compile(r'("[A-Za-z_]*pa(?:ss|w)word"\s*:\s*)"(?:[^"\\]|\\.)*"', re.IGNORECASE)


def mask_secrets(text: str) -> str:
    return _SECRET_RE.sub(r'\1"***"', text)
