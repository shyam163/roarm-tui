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
        before = app.state.pose.base          # settled at ~1.0, captured before the jog moves it
        await pilot.press("d")
        assert app.target.base == pytest.approx(before + math.radians(app.step_deg), abs=0.02)
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
