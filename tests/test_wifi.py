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
    dev.send({"T": 102, "base": 0.5})
    dev.run_once()
    url = fake.requests[-1]
    assert url.startswith("/js?json=%7B%22T%22%3A102")
    assert "{" not in url and '"' not in url and " " not in url


def test_priority_queue_then_target_then_poll():
    dev, fake, clock = make()
    online(dev, fake)
    dev.set_target(Pose(base=0.5))
    dev.send({"T": 100})
    for _ in range(3):
        clock.advance(0.06)
        dev.run_once()
    sent = fake.sent()
    assert sent[0] == {"T": 100}
    assert sent[1]["T"] == 102 and sent[1]["base"] == 0.5
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
