import pytest

from roarm.protocol import HOME
from roarm.sequence import Player, Point, Sequence

A = HOME
B = HOME.with_joint("base", 1.0)


def waypoints(dwell=0.5):
    return Sequence("w", "waypoints", [Point(A, dwell=dwell), Point(B, dwell=dwell)])


def trajectory():
    return Sequence("t", "trajectory", [Point(A, 0.0, 0.0), Point(A.with_joint("base", 0.1), 0.0, 0.1),
                                        Point(A.with_joint("base", 0.2), 0.0, 0.2)])


def test_empty_sequence_rejected():
    with pytest.raises(ValueError):
        Player(Sequence())


def test_waypoints_move_wait_dwell_advance():
    p = Player(waypoints(), speed=1.0)
    p.start(0.0)
    cmds = p.tick(0.0, B)
    assert cmds == [{"T": 102, "base": 0.0, "shoulder": 0.0, "elbow": 1.57, "hand": 3.14, "spd": 800, "acc": 10}]
    assert p.tick(0.05, B) == []            # not reached yet (at B, target A)
    assert p.tick(0.10, A) == []            # reached -> dwell starts
    assert p.tick(0.50, A) == []            # dwell 0.5 not done
    cmds = p.tick(0.61, A)                  # dwell done -> next point sent
    assert p.index == 1 and cmds[0]["base"] == 1.0
    p.tick(0.7, B)                          # reached
    assert p.tick(1.3, B) == []             # dwell done -> finished
    assert not p.running


def test_speed_scales_dwell_and_spd():
    p = Player(waypoints(dwell=1.0), speed=2.0)
    p.start(0.0)
    assert p.tick(0.0, A)[0]["spd"] == 1600
    p.tick(0.05, A)                         # reached, dwell = 0.5 s
    assert p.tick(0.50, A) == []
    assert p.tick(0.56, A)[0]["base"] == 1.0


def test_loop_restarts():
    p = Player(waypoints(dwell=0.0), loop=True)
    p.start(0.0)
    p.tick(0.0, A); p.tick(0.1, A)          # point 0 reached, dwell 0 -> sends point 1
    p.tick(0.2, B)                          # point 1 reached; dwell 0 -> loops, sends point 0
    assert p.running and p.index == 0


def test_waypoint_timeout_without_feedback():
    p = Player(waypoints(dwell=0.0), move_timeout=1.0)
    p.start(0.0)
    p.tick(0.0, None)
    assert p.tick(0.5, None) == []
    cmds = p.tick(1.01, None)               # timeout -> dwell 0 -> next point
    assert p.index == 1 and cmds


def test_stop():
    p = Player(waypoints())
    p.start(0.0)
    p.stop()
    assert not p.running
    assert p.tick(1.0, A) == []


def test_trajectory_approach_then_stream():
    p = Player(trajectory())
    p.start(0.0)
    approach = p.tick(0.0, B)
    assert approach[0]["spd"] == 800 and approach[0]["base"] == 0.0
    assert p.tick(0.05, B) == []            # still approaching
    first = p.tick(1.0, A)                  # reached -> streaming starts at t0=1.0, sends t=0 point
    assert first == [{"T": 102, "base": 0.0, "shoulder": 0.0, "elbow": 1.57, "hand": 3.14, "spd": 0, "acc": 0}]
    assert p.tick(1.05, A) == []
    assert p.tick(1.25, A)[0]["base"] == 0.2   # skips to newest due point (0.1 and 0.2 both due) -> only newest
    assert not p.running


def test_trajectory_speed():
    p = Player(trajectory(), speed=2.0)
    p.start(0.0)
    p.tick(0.0, A); p.tick(0.01, A)         # approach reached at 0.01 -> t0
    assert p.tick(0.07, A)[0]["base"] == 0.1   # elapsed 0.12 at 2x
