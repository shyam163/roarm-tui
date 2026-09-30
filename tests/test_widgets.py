import math

import pytest
from textual.app import App, ComposeResult

from roarm import protocol as P
from roarm.ui.widgets import (ArmView, BrailleCanvas, ConfirmScreen, JointSlider, LoadBars, load_color,
                              render_side, render_top, slider_index, slider_value)


def test_braille_canvas():
    c = BrailleCanvas(2, 1)
    assert (c.w, c.h) == (4, 4)
    assert c.lines() == ["⠀⠀"]
    c.set(0, 0)
    c.set(3, 3)
    c.set(99, 99)                           # out of range ignored
    assert c.lines() == [chr(0x2801) + chr(0x2880)]


def test_braille_line_touches_both_ends():
    c = BrailleCanvas(4, 2)
    c.line(0, 0, 7, 7)
    rows = c.lines()
    assert rows[0][0] != "⠀" and rows[1][3] != "⠀"


@pytest.mark.parametrize("fn", [render_side, render_top])
def test_render_shapes(fn):
    for pose in (P.HOME, P.Pose(1.0, -1.2, 0.3, 2.0), P.Pose(-3.14, 1.57, 3.14, 1.08)):
        rows = fn(pose, 30, 10)
        assert len(rows) == 10
        assert all(len(r) == 30 for r in rows)
        assert any(ch != "⠀" for r in rows for ch in r)


def test_slider_math_clamps():
    assert slider_value(0, 21, -1.0, 1.0) == -1.0
    assert slider_value(20, 21, -1.0, 1.0) == 1.0
    assert slider_value(10, 21, -1.0, 1.0) == pytest.approx(0.0)
    assert slider_value(-50, 21, -1.0, 1.0) == -1.0      # dragged past left edge
    assert slider_value(500, 21, -1.0, 1.0) == 1.0       # dragged past right edge
    assert slider_index(0.0, 21, -1.0, 1.0) == 10
    assert slider_index(9.0, 21, -1.0, 1.0) == 20


def test_load_color():
    assert load_color(0) == "#4ade80"
    assert load_color(-200) == "#fbbf24"
    assert load_color(900) == "#f87171"


class SliderApp(App):
    def __init__(self):
        super().__init__()
        self.changes = []

    def compose(self) -> ComposeResult:
        yield JointSlider("base", "Base", id="s")
        yield LoadBars(id="loads")
        yield ArmView(id="view")

    def on_joint_slider_changed(self, msg):
        self.changes.append((msg.joint, msg.value))


async def test_slider_click_and_scroll():
    app = SliderApp()
    async with app.run_test(size=(80, 20)) as pilot:
        s = app.query_one(JointSlider)
        end_x = s.LABEL_W + s.bar_width - 1
        await pilot.click("#s", offset=(end_x, 0))
        await pilot.pause()
        assert s.target == pytest.approx(3.14)
        assert app.changes[-1] == ("base", pytest.approx(3.14))
        await pilot.click("#s", offset=(1, 0))            # on the label -> clamps to lo
        await pilot.pause()
        assert s.target == pytest.approx(-3.14)
        s.step_deg = 10
        s.nudge(1)
        assert s.target == pytest.approx(-3.14 + math.radians(10))


async def test_slider_set_target_without_emit():
    app = SliderApp()
    async with app.run_test(size=(80, 20)) as pilot:
        s = app.query_one(JointSlider)
        s.set_target(1.0, emit=False)
        await pilot.pause()
        assert s.target == 1.0 and app.changes == []


async def test_views_render():
    app = SliderApp()
    async with app.run_test(size=(80, 20)) as pilot:
        view = app.query_one(ArmView)
        view.pose = P.HOME
        view.xyz = (315.2, 0.0, 221.9)
        app.query_one(LoadBars).loads = {"base": 0, "shoulder": 76, "elbow": 400, "hand": 0}
        await pilot.pause()
        assert "315.2" in str(view.render())


class ConfirmApp(App):
    result = None

    def on_mount(self):
        self.push_screen(ConfirmScreen("Sure?"), lambda ok: setattr(self, "result", ok))


async def test_confirm_screen_yes():
    app = ConfirmApp()
    async with app.run_test() as pilot:
        await pilot.click("#confirm-yes")
        await pilot.pause()
        assert app.result is True
