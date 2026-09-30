import time

import pytest
from textual.widgets import Button, DataTable, Input, Select

from roarm import protocol as P
from roarm.app import RoArmApp
from roarm.device import SimDevice
from roarm.sequence import Point, Sequence, save
from roarm.ui.teach import TeachTab
from roarm.ui.widgets import ConfirmScreen

SIZE = (140, 45)


async def wait_for(pilot, cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await pilot.pause(0.05)


async def click_again(pilot, app, selector):
    """Textual ignores clicks on a Button for 0.2 s after a press (-active); wait that out."""
    await wait_for(pilot, lambda: not app.query_one(selector, Button).has_class("-active"))
    await pilot.click(selector)


async def open_teach(pilot, app):
    await wait_for(pilot, lambda: app.ready)
    app.query_one("TabbedContent").active = "tab-teach"
    await pilot.pause()
    return app.query_one(TeachTab)


def make_app(tmp_path):
    return RoArmApp(SimDevice(boot_time=0.0, rate=10), sequence_dir=tmp_path)


async def test_capture_button_fills_table(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        await pilot.click("#wp-capture")
        await click_again(pilot, app, "#wp-capture")
        assert tab.query_one(DataTable).row_count == 2


async def test_delete_and_reorder(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        a, b = P.HOME, P.HOME.with_joint("base", 1.0)
        tab.sequence = Sequence("x", "waypoints", [Point(a), Point(b)])
        tab.refresh_table()
        table = tab.query_one(DataTable)
        table.move_cursor(row=1)
        await pilot.click("#wp-up")
        assert tab.sequence.points[0].pose == b
        assert table.cursor_row == 0
        await pilot.click("#wp-delete")
        assert [p.pose for p in tab.sequence.points] == [a]


async def test_set_dwell_validates(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        tab.capture(P.HOME)
        tab.query_one("#wp-dwell", Input).value = "2.5"
        await pilot.click("#wp-set-dwell")
        assert tab.sequence.points[0].dwell == 2.5
        tab.query_one("#wp-dwell", Input).value = "-1"
        await click_again(pilot, app, "#wp-set-dwell")
        assert tab.sequence.points[0].dwell == 2.5


async def test_play_empty_sequence_warns(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await open_teach(pilot, app)
        await pilot.click("#teach-play")
        assert app.player is None


async def test_play_and_stop(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        tab.sequence = Sequence("x", "waypoints", [Point(P.HOME.with_joint("base", 1.0), 2.0)])
        tab.refresh_table()
        await pilot.click("#teach-play")
        assert app.player is not None and app.player.running
        await wait_for(pilot, lambda: app.state.pose.base > 0.3)
        await pilot.click("#teach-stop")
        assert not app.player.running


async def test_record_creates_trajectory(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        await pilot.click("#teach-record")
        assert app.recorder is not None
        app.jog("base", 0.5)
        await pilot.pause(0.5)
        await click_again(pilot, app, "#teach-record")
        assert app.recorder is None
        assert tab.sequence.kind == "trajectory"
        assert len(tab.sequence.points) >= 3


async def test_play_while_recording_is_refused(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        tab.sequence = Sequence("x", "waypoints", [Point(P.HOME.with_joint("base", 1.0), 0.2)])
        tab.refresh_table()
        await pilot.click("#teach-record")
        assert app.recorder is not None
        await pilot.click("#teach-play")
        assert app.player is None
        assert app.recorder is not None
        assert str(tab.query_one("#teach-record", Button).label) == "⏹ Stop recording path"


async def test_save_and_load(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        tab.capture(P.HOME.with_joint("base", 0.7))
        tab.query_one("#seq-name", Input).value = "my seq!"
        await pilot.click("#seq-save")
        assert (tmp_path / "my_seq.json").exists()
        await pilot.click("#seq-new")
        assert tab.sequence.points == []
        tab.query_one("#seq-select", Select).value = str(tmp_path / "my_seq.json")
        await pilot.click("#seq-load")
        assert tab.sequence.points[0].pose.base == pytest.approx(0.7)


async def test_load_corrupt_file_keeps_current(tmp_path):
    (tmp_path / "bad.json").write_text("{nope")
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        tab.capture(P.HOME)
        tab.refresh_files()
        tab.query_one("#seq-select", Select).value = str(tmp_path / "bad.json")
        await pilot.click("#seq-load")
        assert len(tab.sequence.points) == 1


async def test_save_load_name_with_markup_chars_does_not_raise(tmp_path):
    # "x [/oops" is valid text but invalid Textual content-markup (an unmatched
    # closing tag) — Content.from_markup("x [/oops") raises MarkupError. Any
    # notify() that renders a name/filename/exception as markup (the default)
    # is one untrusted sequence name away from crashing the render pass.
    from textual.content import Content
    from textual.markup import MarkupError
    with pytest.raises(MarkupError):
        Content.from_markup("x [/oops")

    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        tab.capture(P.HOME)
        tab.query_one("#seq-name", Input).value = "x [/oops"
        calls = []
        orig_notify = app.notify

        def spy(message, **kw):
            calls.append((message, kw))
            return orig_notify(message, **kw)

        app.notify = spy
        await pilot.click("#seq-save")                 # notifies "Saved <file>"
        await pilot.click("#seq-new")
        tab.refresh_files()
        await pilot.pause()
        tab.query_one("#seq-select", Select).value = str(next(tmp_path.glob("*.json")))
        await pilot.click("#seq-load")                 # notifies "Loaded x [/oops (...)"
        assert tab.sequence.name == "x [/oops"
        loaded = [kw for msg, kw in calls if "x [/oops" in msg]
        assert loaded and all(kw.get("markup") is False for kw in loaded)


async def test_torque_button_label_follows_state(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        tab.set_torque(False)
        assert "on" in str(tab.query_one("#teach-torque").label).lower()


def _three_points(tab):
    for i in range(3):
        tab.capture(P.HOME.with_joint("base", 0.1 * i))


async def test_clear_all_confirm_yes_empties_points(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        tab.sequence.name = "keepme"
        _three_points(tab)
        await pilot.click("#wp-clear")
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.click("#confirm-yes")
        await wait_for(pilot, lambda: not isinstance(app.screen, ConfirmScreen))
        assert tab.sequence.points == []
        assert tab.query_one(DataTable).row_count == 0
        assert tab.sequence.name == "keepme"
        assert tab.sequence.kind == "waypoints"


async def test_clear_all_cancel_keeps_points(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        _three_points(tab)
        await pilot.click("#wp-clear")
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.click("#confirm-no")
        await wait_for(pilot, lambda: not isinstance(app.screen, ConfirmScreen))
        assert len(tab.sequence.points) == 3
        assert tab.query_one(DataTable).row_count == 3


async def test_clear_all_when_empty_does_not_open_confirm(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        await pilot.click("#wp-clear")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, ConfirmScreen)
        assert tab.sequence.points == []


async def test_clear_all_stops_playback_of_this_sequence(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        tab.sequence = Sequence("x", "waypoints", [Point(P.HOME.with_joint("base", 1.0), 5.0),
                                                   Point(P.HOME, 5.0)])
        tab.refresh_table()
        await pilot.click("#teach-play")
        assert app.player is not None and app.player.running
        await pilot.click("#wp-clear")
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.click("#confirm-yes")
        await wait_for(pilot, lambda: not isinstance(app.screen, ConfirmScreen))
        assert not app.player.running
        assert tab.sequence.points == []


HINT = "Press space or ● Capture point to add a waypoint"


async def test_labels_and_empty_hint(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        tab = await open_teach(pilot, app)
        assert str(tab.query_one("#wp-capture", Button).label) == "● Capture point"
        assert str(tab.query_one("#teach-record", Button).label) == "⏺ Record path"
        assert HINT in str(tab.query_one("#seq-info").render())
        await pilot.click("#wp-capture")
        await wait_for(pilot, lambda: len(tab.sequence.points) == 1)
        assert HINT not in str(tab.query_one("#seq-info").render())
        await pilot.click("#teach-record")
        await wait_for(pilot, lambda: app.recorder is not None)
        assert str(tab.query_one("#teach-record", Button).label) == "⏹ Stop recording path"
