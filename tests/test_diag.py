import math
import time

import pytest
from textual.widgets import Button, Checkbox, Input, RichLog

from roarm import protocol as P
from roarm.app import RoArmApp
from roarm.device import SimDevice
from roarm.ui.diag import DiagTab, HistoryInput, is_poll

SIZE = (140, 45)


async def wait_for(pilot, cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await pilot.pause(0.05)


def test_is_poll():
    assert is_poll('{"T":105}')
    assert is_poll('{"T": 105}')
    assert is_poll('{"T":1051,"x":1}')
    assert not is_poll('{"T":102,"base":0}')
    assert not is_poll("Moving BASE_JOINT")


async def open_diag(pilot, app):
    await wait_for(pilot, lambda: app.state is not None)
    app.query_one("TabbedContent").active = "tab-diag"
    await pilot.pause()
    return app.query_one(DiagTab)


def make_app(tmp_path):
    return RoArmApp(SimDevice(boot_time=0.0, rate=10), sequence_dir=tmp_path)


async def test_log_filters_polling(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        log = tab.query_one(RichLog)
        n = len(log.lines)
        tab.add_line("rx", '{"T":1051,"x":1}')
        assert len(log.lines) == n
        tab.add_line("rx", "hello")
        await pilot.pause()
        assert len(log.lines) > n
        tab.query_one("#hide-poll", Checkbox).value = False
        await pilot.pause()
        m = len(log.lines)
        tab.add_line("rx", '{"T":1051,"x":1}')
        await pilot.pause()
        assert len(log.lines) > m


async def test_pause_log(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        tab.query_one("#pause-log", Checkbox).value = True
        await pilot.pause()
        log = tab.query_one(RichLog)
        n = len(log.lines)
        tab.add_line("rx", "hello")
        await pilot.pause()
        assert len(log.lines) == n


async def test_console_sends_valid_json_only(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        sent = []
        app.device.send = sent.append
        console = tab.query_one("#console", Input)
        console.focus()
        console.value = "{bad"
        await pilot.press("enter")
        assert sent == [] and console.value == "{bad"
        console.value = "[1]"
        await pilot.press("enter")
        assert sent == []
        console.value = '{"T":302}'
        await pilot.press("enter")
        assert sent == [{"T": 302}] and console.value == ""
        await pilot.press("up")
        assert console.value == '{"T":302}'
        await pilot.press("down")
        assert console.value == ""


async def test_invalid_json_notifies_without_markup(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        calls = []
        orig_notify = app.notify

        def spy(message, **kw):
            calls.append((message, kw))
            return orig_notify(message, **kw)

        app.notify = spy
        console = tab.query_one("#console", Input)
        console.focus()
        console.value = "{not json [/oops"
        await pilot.press("enter")
        assert calls and calls[0][1].get("markup") is False


async def test_console_rejects_nan_and_infinity(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        sent = []
        app.device.send = sent.append
        console = tab.query_one("#console", Input)
        console.focus()
        for bad in ('{"T":102,"base":NaN}', '{"T":102,"base":Infinity}', '{"T":102,"base":-Infinity}'):
            console.value = bad
            await pilot.press("enter")
            assert sent == [] and console.value == bad


async def test_device_info_captured(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        await pilot.click("#diag-info")
        await wait_for(pilot, lambda: "F0:00:00:00:00:00" in str(tab.query_one("#dev-info").render()))


async def test_sparklines_update(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        for _ in range(10):
            tab.add_loads({"base": 0, "shoulder": 100, "elbow": 50, "hand": 0})
        await pilot.pause()
        assert 100 in list(tab.query_one("#spark-shoulder").data)


async def test_console_send_resyncs_jog_target(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        app.device.stop()                # halt the sim thread; drive feedback manually below
        app.device.connected = True      # still "connected" for readiness purposes
        assert app.follow_feedback is False   # already synced from the initial connect
        console = tab.query_one("#console", Input)
        console.focus()
        console.value = '{"T":102,"base":1.0,"shoulder":0,"elbow":1.57,"hand":3.14,"spd":0,"acc":10}'
        await pilot.press("enter")
        assert app.follow_feedback is True    # raw console send tracks the arm until the next move
        # the next feedback frame reports the arm has moved to the commanded pose
        app._handle_state(P.ArmState(P.HOME.with_joint("base", 1.0), 0, 0, 0, {}, time.monotonic()))
        assert app.follow_feedback is True    # still tracking — no motion command has cancelled it
        assert app.target.base == pytest.approx(1.0)
        app.set_focus(None)               # console keeps focus after submit
        before = app.target.base
        await pilot.press("d")            # jog + on base (selected by default) cancels follow_feedback
        assert app.follow_feedback is False
        assert app.target.base == pytest.approx(before + math.radians(app.step_deg))


def test_history_input_standalone():
    h = HistoryInput()
    h.add_history("a")
    h.add_history("a")                       # duplicates collapse
    h.add_history("b")
    assert h.history == ["a", "b"]


def log_text(tab):
    return "\n".join("".join(seg.text for seg in strip) for strip in tab.query_one(RichLog).lines)


async def test_log_masks_passwords(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        tab.add_line("tx", '{"T":407,"mode":3,"sta_ssid":"home","sta_password":"example-pass"}')
        tab.add_line("rx", '{"wifi_mode_on_boot":3,"sta_ssid":"home","sta_password":"example-pass","ip":"1.2.3.4","rssi":-40}')
        await pilot.pause()
        text = log_text(tab)
        assert "example-pass" not in text and '"***"' in text
        info = str(tab.query_one("#dev-info").render())
        assert "ssid home" in info and "example-pass" not in info


async def test_join_button_calls_configure_wifi_and_clears_password(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        calls = []
        app.configure_wifi = lambda ssid, pw: calls.append((ssid, pw)) or True
        tab.query_one("#wifi-ssid", Input).value = "home"
        tab.query_one("#wifi-password", Input).value = "secret"
        tab.query_one("#wifi-join", Button).press()
        await pilot.pause()
        assert calls == [("home", "secret")]
        assert tab.query_one("#wifi-password", Input).value == ""
        assert tab.query_one("#wifi-password", Input).password is True


async def test_join_failure_keeps_inputs(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_diag(pilot, app)
        app.configure_wifi = lambda ssid, pw: False
        tab.query_one("#wifi-ssid", Input).value = "home"
        tab.query_one("#wifi-password", Input).value = "secret"
        tab.query_one("#wifi-join", Button).press()
        await pilot.pause()
        assert tab.query_one("#wifi-password", Input).value == "secret"
