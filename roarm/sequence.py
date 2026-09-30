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
