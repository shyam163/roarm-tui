"""Waypoint / trajectory sequences: model, JSON persistence, recording and playback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from roarm import protocol as P

SEQUENCE_DIR = Path(__file__).resolve().parent.parent / "sequences"
KINDS = ("waypoints", "trajectory")
FILE_VERSION = 1


@dataclass
class Point:
    pose: P.Pose
    dwell: float = 0.5
    t: float | None = None

    def to_dict(self) -> dict:
        d: dict = self.pose.to_dict()
        if self.t is None:
            d["dwell"] = round(self.dwell, 3)
        else:
            d["t"] = round(self.t, 3)
        return d


@dataclass
class Sequence:
    name: str = "untitled"
    kind: str = "waypoints"
    points: list[Point] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"version": FILE_VERSION, "name": self.name, "kind": self.kind,
                "points": [p.to_dict() for p in self.points]}

    @classmethod
    def from_dict(cls, d: object) -> Sequence:
        if not isinstance(d, dict) or d.get("version") != FILE_VERSION:
            raise ValueError("not a version-1 sequence file")
        kind = d.get("kind")
        if kind not in KINDS:
            raise ValueError(f"unknown sequence kind {kind!r}")
        raw = d.get("points")
        if not isinstance(raw, list):
            raise ValueError("'points' must be a list")
        points = []
        for i, pd in enumerate(raw):
            try:
                pose = P.Pose.from_dict(pd)
                if kind == "trajectory":
                    points.append(Point(pose, dwell=0.0, t=float(pd["t"])))
                else:
                    points.append(Point(pose, dwell=max(0.0, float(pd.get("dwell", 0.5)))))
            except (KeyError, TypeError, ValueError) as e:
                raise ValueError(f"point {i + 1}: bad or missing value ({e})") from e
        if kind == "trajectory":
            times = [p.t for p in points]
            if times != sorted(times):
                raise ValueError("trajectory timestamps must not decrease")
        return cls(str(d.get("name", "untitled")), kind, points)


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    return cleaned[:64] or "sequence"


def save(seq: Sequence, directory: Path = SEQUENCE_DIR) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe_name(seq.name)}.json"
    path.write_text(json.dumps(seq.to_dict(), indent=2))
    return path


def load(path: Path) -> Sequence:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read {Path(path).name}: {e}") from e
    return Sequence.from_dict(data)


def list_sequences(directory: Path = SEQUENCE_DIR) -> list[Path]:
    directory = Path(directory)
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


class Recorder:
    """Collects feedback poses into a trajectory, at most one sample per `interval` seconds."""

    def __init__(self, interval: float = 0.04):
        self.interval = interval
        self.points: list[Point] = []
        self._start: float | None = None
        self._last: float | None = None

    def add(self, now: float, pose: P.Pose) -> None:
        if self._start is None:
            self._start = now
        if self._last is not None and now - self._last < self.interval:
            return
        self._last = now
        self.points.append(Point(pose, dwell=0.0, t=now - self._start))

    def to_sequence(self, name: str = "recording") -> Sequence:
        return Sequence(name, "trajectory", list(self.points))


class Player:
    """Plays a Sequence. Pure state machine: call tick() periodically and send what it returns."""

    def __init__(self, sequence: Sequence, speed: float = 1.0, loop: bool = False,
                 tolerance: float = 0.05, move_timeout: float = 6.0):
        if not sequence.points:
            raise ValueError("sequence is empty")
        self.sequence = sequence
        self.speed = min(4.0, max(0.1, float(speed)))
        self.loop = loop
        self.tolerance = tolerance
        self.move_timeout = move_timeout
        self.running = False
        self.index = 0
        self._phase = "move"        # waypoints: move | dwell ; trajectory: move (approach) | stream
        self._sent = False
        self._phase_start = 0.0
        self._t0 = 0.0

    def start(self, now: float) -> None:
        self.running = True
        self.index = 0
        self._phase = "move"
        self._sent = False
        self._phase_start = now

    def stop(self) -> None:
        self.running = False

    def tick(self, now: float, pose: P.Pose | None) -> list[dict]:
        if not self.running:
            return []
        if self.sequence.kind == "trajectory":
            return self._tick_trajectory(now, pose)
        return self._tick_waypoints(now, pose)

    def _spd(self) -> int:
        return min(4000, round(800 * self.speed))

    def _move(self, now: float, pose: P.Pose | None, target: P.Pose) -> tuple[list[dict], bool]:
        """Send the move once; report whether the target was reached (or timed out)."""
        if not self._sent:
            self._sent = True
            self._phase_start = now
            return [P.cmd_joints(target, spd=self._spd(), acc=10)], False
        reached = pose is not None and pose.max_diff(target.clamped()) <= self.tolerance
        return [], reached or now - self._phase_start >= self.move_timeout

    def _tick_waypoints(self, now: float, pose: P.Pose | None) -> list[dict]:
        point = self.sequence.points[self.index]
        if self._phase == "move":
            cmds, done = self._move(now, pose, point.pose)
            if done:
                self._phase = "dwell"
                self._phase_start = now
            else:
                return cmds
        if now - self._phase_start < point.dwell / self.speed:
            return []
        self.index += 1
        if self.index >= len(self.sequence.points):
            if not self.loop:
                self.running = False
                self.index = len(self.sequence.points) - 1
                return []
            self.index = 0
        self._phase = "move"
        self._sent = False
        return self._tick_waypoints(now, pose)

    def _tick_trajectory(self, now: float, pose: P.Pose | None) -> list[dict]:
        points = self.sequence.points
        if self._phase == "move":
            cmds, done = self._move(now, pose, points[0].pose)
            if not done:
                return cmds
            self._phase = "stream"
            self._t0 = now
            self.index = 0
        elapsed = (now - self._t0) * self.speed
        i = self.index
        while i < len(points) and points[i].t <= elapsed:
            i += 1
        out = []
        if i > self.index:
            out.append(P.cmd_joints(points[i - 1].pose, spd=0, acc=0))
            self.index = i
        if self.index >= len(points):
            if self.loop:
                self._phase = "move"
                self._sent = False
                self.index = 0
            else:
                self.running = False
                self.index = len(points) - 1
        return out
