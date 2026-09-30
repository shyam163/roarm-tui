import time

from roarm.protocol import HOME, Pose
from roarm.wifi import WifiDevice
from tests.conftest import FakeClock, FakeHTTP


def make(fake=None, clock=None, **kw):
    fake = fake or FakeHTTP()
    clock = clock or FakeClock()
    hosts = []

    def factory(host, timeout):
        hosts.append(host)
        return fake

    dev = WifiDevice("10.0.0.2", http_factory=factory, clock=clock, reconnect_delay=0, idle_wait=0, **kw)
    dev.statuses, dev.lines, dev.states, dev.hosts = [], [], [], hosts
    dev.on_status = dev.statuses.append
    dev.on_line = lambda d, t: dev.lines.append((d, t))
    dev.on_state = dev.states.append
    return dev, fake, clock


def online(dev, fake):
    dev.run_once()
    assert dev.connected
    fake.requests.clear()


def mute_polls(dev, clock):
    """Suppress feedback polls (an overdue poll outranks queued commands) to isolate other behaviour."""
    dev._last_poll = clock()
    dev.poll_interval = 1e9


def unmute_polls(dev, interval=0.05):
    dev.poll_interval = interval


def test_first_contact_goes_online_with_state():
    dev, fake, clock = make()
    dev.run_once()
    assert dev.connected and not dev.booting
    assert dev.statuses == ["online"]
    assert fake.sent() == [{"T": 105}]
    assert dev.state is not None and dev.state.loads["shoulder"] == 76
    assert dev.port == "wifi 10.0.0.2"


def test_url_is_percent_encoded():
    dev, fake, clock = make()
    online(dev, fake)
    mute_polls(dev, clock)
    dev.send({"T": 102, "base": 0.5})
    dev.run_once()
    url = fake.requests[-1]
    assert url.startswith("/js?json=%7B%22T%22%3A102")
    assert "{" not in url and '"' not in url and " " not in url


def test_priority_queue_then_target_then_due_poll():
    dev, fake, clock = make()
    online(dev, fake)
    dev._last_poll = clock()
    dev.set_target(Pose(base=0.5))
    dev.send({"T": 100})
    for _ in range(3):
        clock.advance(0.03)
        dev.run_once()
    sent = fake.sent()
    assert sent[0] == {"T": 100}
    assert sent[1]["T"] == 102 and sent[1]["base"] == 0.5    # a merely due poll waits behind queue and target
    assert sent[2] == {"T": 105}


def test_jog_target_limited_to_20hz():
    dev, fake, clock = make()
    online(dev, fake)
    for i in range(6):
        dev.set_target(Pose(base=0.1 * i))
        clock.advance(0.03)
        dev.run_once()
    assert [c["T"] for c in fake.sent()] == [102, 105, 102, 105, 102, 105]


def test_idle_when_nothing_due():
    dev, fake, clock = make()
    online(dev, fake)
    clock.advance(0.06)
    dev.run_once()                     # first poll
    fake.requests.clear()
    dev.run_once()                     # same instant: poll not due, nothing queued
    assert fake.requests == []


def test_unreachable_reports_cannot_open():
    fake = FakeHTTP()
    fake.fail = True
    dev, fake, clock = make(fake)
    dev.run_once()
    assert not dev.connected
    assert dev.statuses[-1].startswith("cannot open wifi 10.0.0.2")


def test_three_failures_disconnect_then_reconnect():
    dev, fake, clock = make()
    online(dev, fake)
    dev.send({"T": 1})
    fake.fail = True
    for _ in range(2):
        clock.advance(0.06)
        dev.run_once()
    assert dev.connected                              # two failures tolerated
    dev.send({"T": 2})
    clock.advance(0.06)
    dev.run_once()
    assert not dev.connected and dev.statuses[-1] == "disconnected"
    assert list(dev._queue) == [] and dev._target is None
    fake.fail = False
    dev.run_once()
    assert dev.connected and dev.statuses[-1] == "online"


def test_http_error_status_counts_as_failure():
    dev, fake, clock = make(max_failures=1)
    online(dev, fake)
    fake.status = 500
    clock.advance(0.06)
    dev.run_once()
    assert not dev.connected


def test_failure_recreates_connection():
    dev, fake, clock = make()
    online(dev, fake)
    fake.fail = True
    clock.advance(0.06)
    dev.run_once()
    fake.fail = False
    clock.advance(0.06)
    dev.run_once()
    assert fake.closed >= 1 and len(dev.hosts) == 2


def test_set_host_reconnects_to_new_host():
    dev, fake, clock = make()
    online(dev, fake)
    dev.set_host("10.0.0.9")
    clock.advance(0.06)
    dev.run_once()
    assert dev.hosts[-1] == "10.0.0.9" and dev.port == "wifi 10.0.0.9"


def test_clear_queue():
    dev, fake, clock = make()
    online(dev, fake)
    dev.send({"T": 1})
    dev.set_target(HOME)
    dev.clear_queue()
    clock.advance(0.06)
    dev.run_once()
    assert fake.sent() == [{"T": 105}]


def test_rx_lines_logged():
    dev, fake, clock = make()
    dev.run_once()
    assert ("tx", '{"T":105}') in dev.lines
    assert any(d == "rx" and t.startswith('{"T":1051') for d, t in dev.lines)


def test_queued_command_retried_once_after_transient_failure():
    dev, fake, clock = make()
    online(dev, fake)
    mute_polls(dev, clock)
    dev.send({"T": 100})
    fake.fail_next = 1
    clock.advance(0.06)
    dev.run_once()
    assert dev.connected
    assert list(dev._queue) == [{"T": 100}]
    assert any(d == "sys" and "retrying" in t for d, t in dev.lines)
    clock.advance(0.06)
    dev.run_once()
    assert fake.sent()[-1] == {"T": 100}
    assert list(dev._queue) == []


def test_queued_command_not_retried_twice():
    dev, fake, clock = make()
    online(dev, fake)
    mute_polls(dev, clock)
    dev.send({"T": 1})
    fake.fail_next = 2
    clock.advance(0.06)
    dev.run_once()                     # first failure -> requeued as a retry
    assert dev.connected
    clock.advance(0.06)
    dev.run_once()                     # second failure (the retry itself) -> dropped
    assert dev.connected
    assert list(dev._queue) == [] and dev._target is None
    unmute_polls(dev)
    clock.advance(0.06)
    dev.run_once()
    assert fake.sent()[-1] == {"T": 105}   # next request is a poll, not another retry


def test_commands_sent_while_offline_are_discarded():
    dev, fake, clock = make()
    online(dev, fake)
    dev.send({"T": 999})
    fake.fail = True
    for _ in range(3):
        clock.advance(0.06)
        dev.run_once()
    assert not dev.connected and dev.statuses[-1] == "disconnected"
    dev.send({"T": 1})
    dev.set_target(HOME)
    fake.fail = False
    clock.advance(0.06)
    dev.run_once()
    assert dev.connected
    assert fake.sent() == [{"T": 105}]


def test_jog_target_failure_is_not_retried_but_logged():
    dev, fake, clock = make()
    online(dev, fake)
    mute_polls(dev, clock)
    dev.set_target(Pose(base=0.5))
    fake.fail_next = 1
    clock.advance(0.06)
    dev.run_once()
    assert dev.connected and dev._target is None
    assert any(d == "sys" and "jog target failed" in t for d, t in dev.lines)
    unmute_polls(dev)
    clock.advance(0.06)
    dev.run_once()
    assert fake.sent()[-1] == {"T": 105}   # not retried — poll goes out next


def test_retry_dropped_if_queue_cleared_during_request():
    dev, fake, clock = make()
    online(dev, fake)
    mute_polls(dev, clock)              # otherwise the request in flight is a poll, not T:100
    dev.send({"T": 100})

    def estop_mid_flight():
        # simulate app.py's action_estop firing while T:100's request is in flight
        dev.clear_queue()
        dev.send_now({"T": 999})

    fake.on_request = estop_mid_flight
    fake.fail_next = 1
    clock.advance(0.06)
    dev.run_once()
    fake.on_request = None
    assert dev.connected                       # one failure tolerated
    assert list(dev._queue) == [{"T": 999}]     # T:100 not re-inserted ahead of the E-stop


def test_start_stop_thread():
    fake = FakeHTTP()
    dev = WifiDevice("10.0.0.2", http_factory=lambda h, t: fake, reconnect_delay=0)
    dev.start()
    deadline = time.monotonic() + 2
    while not dev.connected and time.monotonic() < deadline:
        time.sleep(0.01)
    dev.stop()
    assert not dev._thread.is_alive()
    assert not dev.connected


def test_feedback_polls_not_starved_by_20hz_stream():
    dev, fake, clock = make()
    online(dev, fake)
    fake.on_request = lambda: clock.advance(0.05)      # ~50 ms HTTP round trip
    poll_times = []
    for _ in range(200):
        dev.send({"T": 102, "base": 0.1, "shoulder": 0, "elbow": 1.57, "hand": 3.14})
        before = len(fake.requests)
        dev.run_once()
        if len(fake.requests) > before and fake.sent()[-1]["T"] == 105:
            poll_times.append(clock())
    total = len(fake.requests)
    assert len(poll_times) * 4 >= total                  # >= 1 poll per 4 requests
    gaps = [b - a for a, b in zip(poll_times, poll_times[1:])]
    assert max(gaps) <= 0.25


def test_send_now_beats_an_overdue_poll():
    dev, fake, clock = make()
    online(dev, fake)
    clock.advance(1.0)                                   # poll long overdue
    dev.send({"T": 102, "base": 0.1})
    dev.send_now({"T": 210, "cmd": 1})
    dev.run_once()
    assert fake.sent()[-1] == {"T": 210, "cmd": 1}
    dev.run_once()
    assert fake.sent()[-1] == {"T": 105}                 # then the overdue poll
    dev.run_once()
    assert fake.sent()[-1]["T"] == 102


def test_set_idle_slows_polling_and_restores():
    dev, fake, clock = make(poll_interval=0.05)
    dev.set_idle(True)
    assert dev.poll_interval == 1.0
    dev.set_idle(False)
    assert dev.poll_interval == 0.05


def test_trajectory_stream_over_slow_link_builds_no_backlog():
    dev, fake, clock = make()
    online(dev, fake)
    fake.on_request = lambda: clock.advance(0.05)         # ~50 ms round trip
    poll_times, target_times, max_queue = [], [], 0
    t_next = clock()
    end = clock() + 10.0
    while clock() < end:
        if clock() >= t_next:                              # the app's 20 Hz playback tick
            dev.set_target(Pose(base=0.1), spd=100, acc=10)
            t_next += 0.05
        before = len(fake.requests)
        dev.run_once()
        max_queue = max(max_queue, len(dev._queue))
        if len(fake.requests) > before:
            (poll_times if fake.sent()[-1]["T"] == 105 else target_times).append(clock())
        else:
            clock.advance(0.005)                           # idle slot
    assert max_queue <= 1
    assert len(target_times) / 10.0 >= 8                   # targets delivered at >= 8 Hz
    assert max(b - a for a, b in zip(poll_times, poll_times[1:])) <= 0.25
    # playback over: at most the one pending target, then only polls
    fake.requests.clear()
    dev.clear_queue()
    for _ in range(10):
        dev.run_once()
    assert {c["T"] for c in fake.sent()} <= {105}


def test_torque_off_after_trajectory_goes_out_on_the_next_slot():
    dev, fake, clock = make()
    online(dev, fake)
    fake.on_request = lambda: clock.advance(0.05)
    for _ in range(400):                                   # until the last request was a poll:
        dev.set_target(Pose(base=0.1))                     # feedback is then not overdue
        n = len(fake.requests)
        dev.run_once()
        if len(fake.requests) == n:
            clock.advance(0.005)
        elif fake.sent()[-1]["T"] == 105 and clock() > 100.5:
            break
    assert fake.sent()[-1]["T"] == 105
    dev.set_target(Pose(base=0.2))                         # a pending target
    dev.send({"T": 210, "cmd": 0})
    dev.run_once()
    assert fake.sent()[-1] == {"T": 210, "cmd": 0}


def test_retried_urgent_command_stays_urgent():
    dev, fake, clock = make()
    online(dev, fake)
    mute_polls(dev, clock)
    dev.send({"T": 1})
    dev.send_now({"T": 210, "cmd": 1})
    fake.fail_next = 1
    dev.run_once()                                         # urgent fails -> retried
    assert list(dev._queue)[0] == {"T": 210, "cmd": 1} and dev._urgent == 1
    clock.advance(1.0)
    dev.poll_interval = 0.05                               # poll now badly overdue
    dev.run_once()
    assert fake.sent()[-1] == {"T": 210, "cmd": 1}
