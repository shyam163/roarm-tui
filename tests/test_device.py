import serial

from roarm.device import ArmDevice
from roarm.protocol import HOME, Pose
from tests.conftest import FakeClock, FakeSerial

FEEDBACK = ('{"T":1051,"x":315.2,"y":0,"z":221.9,"b":0,"s":0.02,"e":1.59,"t":3.14,'
            '"torB":0,"torS":76,"torE":72,"torH":0}\n')


def make(fake=None, clock=None, **kw):
    fake = fake or FakeSerial()
    clock = clock or FakeClock()
    dev = ArmDevice("/dev/fake", serial_factory=lambda p, b: fake, clock=clock, reconnect_delay=0, **kw)
    dev.statuses, dev.lines, dev.states = [], [], []
    dev.on_status = dev.statuses.append
    dev.on_line = lambda d, t: dev.lines.append((d, t))
    dev.on_state = dev.states.append
    return dev, fake, clock


def ready(dev, fake, clock):
    dev.run_once()                       # opens
    fake.feed("RoArm-M2 started.\n")
    dev.step()
    assert not dev.booting
    fake.tx.clear()


def test_open_starts_booting():
    dev, fake, clock = make()
    dev.run_once()
    assert dev.connected and dev.booting
    assert dev.statuses == ["booting"]
    assert any(d == "sys" and "resets" in t for d, t in dev.lines)


def test_boot_marker_ends_booting():
    dev, fake, clock = make()
    dev.run_once()
    fake.feed("ets Jul 29 2019\nMoving BASE_JOINT to initPos.\n")
    dev.step()
    assert dev.booting
    fake.feed("RoArm-M2 started.\n")
    dev.step()
    assert not dev.booting and dev.statuses[-1] == "ready"


def test_boot_ends_after_quiet_period():
    dev, fake, clock = make(boot_quiet=2.0)
    dev.run_once()
    clock.advance(1.0); dev.step()
    assert dev.booting
    clock.advance(1.1); dev.step()
    assert not dev.booting


def test_nothing_sent_while_booting():
    dev, fake, clock = make()
    dev.run_once()
    dev.send({"T": 100})
    clock.advance(0.5); dev.step()
    assert fake.tx == []


def test_polls_feedback_when_idle():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    clock.advance(0.1); dev.step()
    assert fake.sent() == [{"T": 105}]
    clock.advance(0.03); dev.step()      # tx allowed, but poll interval not elapsed
    assert fake.sent() == [{"T": 105}]


def test_feedback_parsed_to_state():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    fake.feed('{"T": 105}\n' + FEEDBACK)
    dev.step()
    assert dev.state.loads["shoulder"] == 76
    assert dev.states == [dev.state]
    assert ("rx", '{"T": 105}') in dev.lines


def test_partial_lines_are_buffered():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    fake.feed(FEEDBACK[:40])
    dev.step()
    assert dev.state is None
    fake.feed(FEEDBACK[40:])
    dev.step()
    assert dev.state is not None and dev.state.z == 221.9


def test_priority_queue_then_target_then_due_poll():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    dev._last_poll = clock()
    dev.set_target(Pose(base=0.5))
    dev.send({"T": 100})
    for _ in range(3):
        clock.advance(0.03); dev.step()
    sent = fake.sent()
    assert sent[0] == {"T": 100}
    assert sent[1]["T"] == 102 and sent[1]["base"] == 0.5    # a merely due poll waits behind queue and target
    assert sent[2] == {"T": 105}


def test_send_now_jumps_queue():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    dev.send({"T": 1}); dev.send_now({"T": 2})
    clock.advance(0.06); dev.step()
    assert fake.sent() == [{"T": 2}]


def test_set_target_coalesces():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    dev._last_poll = clock()
    dev.set_target(Pose(base=0.1), spd=300)
    dev.set_target(Pose(base=0.2), spd=300)
    clock.advance(0.03); dev.step()
    clock.advance(0.06); dev.step()
    sent = fake.sent()
    assert sent[0]["base"] == 0.2 and sent[0]["spd"] == 300
    assert sent[1] == {"T": 105}


def test_tx_interval_limits_rate():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    dev.send({"T": 1}); dev.send({"T": 2})
    clock.advance(0.06); dev.step()
    clock.advance(0.01); dev.step()
    assert len(fake.tx) == 1


def test_jog_target_limited_to_20hz():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    for i in range(6):
        dev.set_target(Pose(base=round(i * 0.1, 1)))
        clock.advance(0.03); dev.step()      # above tx_interval, below jog_interval
    sent = fake.sent()
    jogs = [s for s in sent if s["T"] == 102]
    polls = [s for s in sent if s["T"] == 105]
    assert [round(j["base"], 1) for j in jogs] == [0.0, 0.2, 0.4]
    assert len(polls) == 3
    assert [s["T"] for s in sent] == [102, 105, 102, 105, 102, 105]


def test_clear_queue():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    dev.send({"T": 1}); dev.set_target(HOME)
    dev.clear_queue()
    clock.advance(0.06); dev.step()
    assert fake.sent() == [{"T": 105}]


def test_tx_lines_logged():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    clock.advance(0.1); dev.step()
    assert ("tx", '{"T":105}') in dev.lines


def test_disconnect_reports_and_reconnects():
    fakes = [FakeSerial(), FakeSerial()]
    dev, _, clock = make()
    dev.serial_factory = lambda p, b: fakes.pop(0)
    dev.run_once()
    first = dev._ser
    first.fail = True
    dev.send({"T": 1})
    dev.run_once()
    assert not dev.connected and dev.statuses[-1] == "disconnected"
    assert first.closed
    dev.run_once()                       # reconnect with second fake
    assert dev.connected and dev.booting
    assert dev._queue == type(dev._queue)()   # queue was cleared on disconnect


def test_open_failure_reports_status():
    dev, _, _ = make()
    def boom(p, b):
        raise serial.SerialException("[Errno 2] No such file")
    dev.serial_factory = boom
    dev.run_once()
    assert not dev.connected
    assert dev.statuses[-1].startswith("cannot open /dev/fake")


def test_boot_done_clears_commands_queued_during_boot():
    dev, fake, clock = make()
    dev.run_once()                       # opens, booting
    dev.send({"T": 100})
    dev.set_target(Pose(base=0.5))
    fake.feed("RoArm-M2 started.\n")
    dev.step()                           # boot marker -> boot done
    assert not dev.booting
    fake.tx.clear()
    clock.advance(0.1); dev.step()
    sent = fake.sent()
    assert all(s.get("T") not in (100, 102) for s in sent)
    assert sent == [{"T": 105}]


def test_open_clears_commands_queued_while_disconnected():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    fake.fail = True
    dev.send({"T": 1})
    dev.run_once()                       # detects lost -> disconnected
    assert not dev.connected
    # commands queued while nothing is open (app didn't notice yet)
    dev.send({"T": 100})
    dev.set_target(Pose(base=0.5))
    fake2 = FakeSerial()
    dev.serial_factory = lambda p, b: fake2
    dev.run_once()                       # reopens -> booting; stale queue must be dropped
    fake2.feed("RoArm-M2 started.\n")
    dev.step()                           # boot marker -> boot done
    fake2.tx.clear()
    clock.advance(0.1); dev.step()
    sent = fake2.sent()
    assert all(s.get("T") not in (100, 102) for s in sent)
    assert sent == [{"T": 105}]


def test_start_stop_thread():
    dev, fake, clock = make()
    dev.start()
    dev.stop()
    assert not dev._thread.is_alive()
    assert fake.closed


def test_feedback_polls_not_starved_by_queued_commands():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    poll_times = []
    for _ in range(200):
        dev.send({"T": 102, "base": 0.1, "shoulder": 0, "elbow": 1.57, "hand": 3.14})
        clock.advance(0.05)
        before = len(fake.tx)
        dev.step()
        if len(fake.tx) > before and fake.sent()[-1]["T"] == 105:
            poll_times.append(clock())
    assert len(poll_times) * 4 >= len(fake.tx)
    gaps = [b - a for a, b in zip(poll_times, poll_times[1:])]
    assert max(gaps) <= 0.25


def test_send_now_beats_an_overdue_poll():
    dev, fake, clock = make()
    ready(dev, fake, clock)
    clock.advance(1.0)
    dev.send({"T": 102, "base": 0.1})
    dev.send_now({"T": 210, "cmd": 1})
    dev.step()
    assert fake.sent()[-1] == {"T": 210, "cmd": 1}
    clock.advance(0.05)
    dev.step()
    assert fake.sent()[-1] == {"T": 105}
    clock.advance(0.05)
    dev.step()
    assert fake.sent()[-1]["T"] == 102
