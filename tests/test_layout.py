"""Layout regression tests: nothing important is clipped or hidden behind the footer."""

import time

import pytest
from textual.widgets import Footer

from roarm.app import RoArmApp
from roarm.device import SimDevice

SIZES = [(80, 24), (140, 45)]


def make_app(tmp_path):
    return RoArmApp(SimDevice(boot_time=0.0, rate=10), sequence_dir=tmp_path)


async def wait_for(pilot, cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await pilot.pause(0.05)


def assert_onscreen(app, selector, screen_size):
    widget = app.query_one(selector)
    footer_h = app.query_one(Footer).size.height
    screen_w, screen_h = screen_size
    region = widget.region
    assert region.width > 0 and region.height > 0, f"{selector} has no visible area"
    assert region.x >= 0, f"{selector} starts off the left edge: {region}"
    assert region.x + region.width <= screen_w, f"{selector} runs off the right edge: {region} at {screen_size}"
    assert region.y + region.height <= screen_h - footer_h, (
        f"{selector} overlaps the footer: {region} at {screen_size} (footer height {footer_h})")


@pytest.mark.parametrize("size", SIZES)
async def test_estop_button_fully_onscreen(tmp_path, size):
    app = make_app(tmp_path)
    async with app.run_test(size=size) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        assert_onscreen(app, "#btn-estop", size)


@pytest.mark.parametrize("size", SIZES)
async def test_estop_is_first_control_button(tmp_path, size):
    app = make_app(tmp_path)
    async with app.run_test(size=size) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        buttons = list(app.query("#control-buttons Button"))
        assert buttons[0].id == "btn-estop"


@pytest.mark.parametrize("size", SIZES)
async def test_teach_capture_row_fully_onscreen(tmp_path, size):
    app = make_app(tmp_path)
    async with app.run_test(size=size) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        app.query_one("TabbedContent").active = "tab-teach"
        await pilot.pause()
        assert_onscreen(app, "#wp-capture", size)
        assert_onscreen(app, "#wp-clear", size)


@pytest.mark.parametrize("size", SIZES)
async def test_diag_console_fully_onscreen(tmp_path, size):
    app = make_app(tmp_path)
    async with app.run_test(size=size) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        app.query_one("TabbedContent").active = "tab-diag"
        await pilot.pause()
        assert_onscreen(app, "#console", size)


@pytest.mark.parametrize("size", SIZES)
async def test_teach_stop_and_loop_row_fully_onscreen(tmp_path, size):
    app = make_app(tmp_path)
    async with app.run_test(size=size) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        app.query_one("TabbedContent").active = "tab-teach"
        await pilot.pause()
        for sel in ("#teach-play", "#teach-stop", "#teach-loop", "#teach-speed"):
            assert_onscreen(app, sel, size)


@pytest.mark.parametrize("sel", ["#seq-save", "#seq-load"])
async def test_teach_save_and_load_reachable_at_80x24(tmp_path, sel):
    size = (80, 24)
    app = make_app(tmp_path)
    async with app.run_test(size=size) as pilot:
        await wait_for(pilot, lambda: app.state is not None)
        app.query_one("TabbedContent").active = "tab-teach"
        await pilot.pause()
        app.query_one(sel).scroll_visible(animate=False)
        await pilot.pause(0.2)
        assert_onscreen(app, sel, size)
