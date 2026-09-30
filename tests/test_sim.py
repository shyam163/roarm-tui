import pytest

from roarm import protocol as P
from roarm.device import SimDevice


def make():
    sim = SimDevice(slew=2.0)
    sim.states, sim.lines = [], []
    sim.on_state = sim.states.append
    sim.on_line = lambda d, t: sim.lines.append((d, t))
    return sim


def test_emits_state_each_step():
    sim = make()
    sim.step(0.05)
    assert sim.state is not None and sim.states == [sim.state]
    assert sim.state.pose == P.HOME
    assert sim.state.x == pytest.approx(316.0, abs=1)


def test_slews_toward_target():
    sim = make()
    sim.send(P.cmd_joints(P.HOME.with_joint("base", 1.0)))
    sim.step(0.1)
    assert sim.state.pose.base == pytest.approx(0.2)
    for _ in range(20):
        sim.step(0.1)
    assert sim.state.pose.base == pytest.approx(1.0)


def test_set_target_single_joint_and_gripper():
    sim = make()
    sim.set_target(P.HOME.with_joint("elbow", 1.0))
    sim.send(P.cmd_joint("base", -0.5))
    sim.send(P.cmd_gripper(P.GRIP_OPEN))
    for _ in range(30):
        sim.step(0.1)
    pose = sim.state.pose
    assert pose.elbow == pytest.approx(1.0)
    assert pose.base == pytest.approx(-0.5)
    assert pose.hand == pytest.approx(P.GRIP_OPEN)


def test_home_command():
    sim = make()
    sim.send(P.cmd_joints(P.HOME.with_joint("base", 1.0)))
    for _ in range(10):
        sim.step(0.1)
    sim.send(P.cmd_home())
    for _ in range(10):
        sim.step(0.1)
    assert sim.state.pose == P.HOME


def test_torque_off_freezes_goal():
    sim = make()
    sim.send(P.cmd_joints(P.HOME.with_joint("base", 1.0)))
    sim.step(0.1)
    sim.send(P.cmd_torque(False))
    for _ in range(10):
        sim.step(0.1)
    assert sim.state.pose.base == pytest.approx(0.2)
    assert sim.torque is False
    assert sim.state.torque == {j: False for j in P.JOINTS}


def test_info_commands_and_bad_command():
    sim = make()
    sim.send(P.cmd_mac()); sim.send({"T": 102, "base": "x"})
    sim.step(0.05)
    assert ("rx", "F0:00:00:00:00:00") in sim.lines
    assert any(d == "sys" and "bad command" in t for d, t in sim.lines)
    assert any(d == "tx" for d, _ in sim.lines)


def test_clear_queue():
    sim = make()
    sim.send(P.cmd_joints(P.HOME.with_joint("base", 1.0)))
    sim.clear_queue()
    sim.step(0.1)
    assert sim.state.pose.base == 0.0


def test_start_boots_then_stops():
    sim = SimDevice(boot_time=0.0, rate=100)
    statuses = []
    sim.on_status = statuses.append
    sim.start()
    import time
    deadline = time.monotonic() + 2
    while sim.state is None and time.monotonic() < deadline:
        time.sleep(0.01)
    sim.stop()
    assert statuses[:2] == ["booting", "ready"]
    assert sim.state is not None
    assert not sim.connected
