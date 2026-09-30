import math
import time

import pytest

from roarm import protocol as P
from roarm.app import RoArmApp
from roarm.device import SimDevice
from roarm.sequence import Point, Sequence
from roarm.ui.teach import TeachTab
from roarm.ui.widgets import ConfirmScreen, JointSlider

SIZE = (140, 45)


def make_app(tmp_path):
    return RoArmApp(SimDevice(boot_time=0.0, rate=50), sequence_dir=tmp_path)


async def wait_for(pilot, cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await pilot.pause(0.05)


async def test_starts_and_shows_feedback(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        await wait_for(pilot, lambda: app.query_one("#slider-elbow", JointSlider).actual is not None)
        await wait_for(pilot, lambda: "● connected" in str(app.query_one("#status").render()))


async def test_slider_click_moves_arm(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        s = app.query_one("#slider-base", JointSlider)
        await pilot.click("#slider-base", offset=(s.LABEL_W + s.bar_width - 1, 0))
        await wait_for(pilot, lambda: app.target.base == pytest.approx(3.14))
        await wait_for(pilot, lambda: app.state.pose.base > 1.0)


async def test_keyboard_jog_and_select(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        await pilot.press("3")                     # elbow
        assert app.selected == 2
        before = app.target.elbow
        await pilot.press("d")
        assert app.target.elbow == pytest.approx(before + math.radians(app.step_deg))
        await pilot.press("w")
        assert app.selected == 1


async def test_capture_adds_waypoint(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        await pilot.press("space")
        assert len(app.query_one(TeachTab).sequence.points) == 1


async def test_estop_stops_playback_and_holds(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        seq = Sequence("s", "waypoints", [Point(P.HOME.with_joint("base", 2.0), 0.5), Point(P.HOME, 0.5)])
        assert app.start_playback(seq)
        await wait_for(pilot, lambda: app.state.pose.base > 0.3)
        await pilot.press("escape")
        assert not app.player.running
        held = app.state.pose.base
        await pilot.pause(0.3)
        assert app.state.pose.base == pytest.approx(held, abs=0.15)


async def test_torque_off_requires_confirmation(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        await pilot.press("t")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click("#confirm-no")
        assert app.torque_on
        await pilot.press("t")
        await pilot.click("#confirm-yes")
        await pilot.pause()
        assert not app.torque_on
        await wait_for(pilot, lambda: app.device.torque is False)
        before = app.target
        await pilot.press("d")                     # jog refused while torque off
        assert app.target == before
        await pilot.press("t")                     # turning back on needs no confirm
        assert app.torque_on


async def test_playback_from_torque_off_reenables_torque(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        await pilot.press("t")
        await pilot.click("#confirm-yes")
        await pilot.pause()
        await wait_for(pilot, lambda: app.device.torque is False)
        seq = Sequence("s", "waypoints", [Point(P.HOME.with_joint("base", 1.0), 0.2)])
        assert app.start_playback(seq)
        await wait_for(pilot, lambda: app.device.torque is True)
        await wait_for(pilot, lambda: app.state.pose.base > 0.3)


async def test_disconnect_stops_playback(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        app.start_playback(Sequence("s", "waypoints", [Point(P.HOME, 5.0)]))
        app.device.connected = False
        app.device.on_status("disconnected")
        await wait_for(pilot, lambda: not app.player.running)
        assert "disconnected" in str(app.query_one("#status").render())


async def test_jog_refused_when_not_ready(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        before = app.target
        calls = []
        app.device.set_target = lambda *a, **kw: calls.append(("set_target", a, kw))
        app.device.send = lambda *a, **kw: calls.append(("send", a, kw))
        app.device.booting = True
        await pilot.press("d")
        assert app.target == before
        assert calls == []


async def test_disconnect_resets_state_and_needs_sync(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        app.device.stop()                   # halt the sim thread so it can't overwrite state
        app._handle_status("disconnected")
        assert app.state is None
        assert app.needs_sync is True
        assert app.ready is False


async def test_estop_sends_no_motion_when_not_ready(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        calls = []
        app.device.send_now = lambda *a, **kw: calls.append(("send_now", a, kw))
        app.device.clear_queue = lambda: calls.append(("clear_queue",))
        app.device.booting = True
        await pilot.press("escape")
        assert calls == [("clear_queue",)]


async def test_home_button(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        app.jog("base", 1.0)
        await wait_for(pilot, lambda: app.state.pose.base > 0.5)
        await pilot.click("#btn-home")
        await wait_for(pilot, lambda: abs(app.state.pose.base) < 0.01)
        assert app.target == P.HOME


def test_cli_parses(monkeypatch):
    from roarm import __main__ as cli
    ran = {}
    monkeypatch.setattr(cli.RoArmApp, "run", lambda self: ran.setdefault("device", self.device))
    cli.main(["--sim"])
    assert isinstance(ran["device"], SimDevice)
    ran.clear()
    cli.main(["--port", "/dev/nothing"])
    assert ran["device"].port == "/dev/nothing"
