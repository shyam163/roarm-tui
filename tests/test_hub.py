import pytest

from roarm import protocol as P
from roarm.device import SimDevice
from roarm.hub import DeviceHub


def make():
    usb, wifi = SimDevice(), SimDevice()
    hub = DeviceHub({"usb": usb, "wifi": wifi}, active="usb")
    hub.statuses, hub.states, hub.lines = [], [], []
    hub.on_status = hub.statuses.append
    hub.on_state = hub.states.append
    hub.on_line = lambda d, t: hub.lines.append((d, t))
    return hub, usb, wifi


def test_rejects_unknown_active():
    with pytest.raises(ValueError):
        DeviceHub({"usb": SimDevice()}, active="wifi")


def test_forwards_commands_to_active_only():
    hub, usb, wifi = make()
    hub.send(P.cmd_home())
    hub.set_target(P.HOME.with_joint("base", 1.0))
    assert list(usb._queue) == [P.cmd_home()] and usb._target is not None
    assert list(wifi._queue) == [] and wifi._target is None


def test_callbacks_only_from_active():
    hub, usb, wifi = make()
    usb.step(0.05)
    wifi.step(0.05)
    assert len(hub.states) == 1
    usb.on_status("ready")
    wifi.on_status("disconnected")
    assert hub.statuses == ["ready"]


def test_switch_clears_queues_and_reports():
    hub, usb, wifi = make()
    hub.send(P.cmd_home())
    wifi.send(P.cmd_home())
    assert hub.switch("wifi") is True
    assert hub.active == "wifi" and hub.label == "Wi-Fi"
    assert list(usb._queue) == [] and list(wifi._queue) == []
    assert hub.statuses[-1] == "switched:wifi"
    wifi.step(0.05)
    usb.step(0.05)
    assert len(hub.states) == 1                     # only wifi's frame got through


def test_switch_refuses_unknown_or_same():
    hub, usb, wifi = make()
    assert hub.switch("usb") is False
    assert hub.switch("bluetooth") is False
    assert hub.statuses == []


def test_other_and_properties():
    hub, usb, wifi = make()
    assert hub.other() == "wifi"
    usb.connected, wifi.connected = True, False
    assert hub.connected is True and hub.port == "sim"
    hub.switch("wifi")
    assert hub.connected is False and hub.other() == "usb"
    assert DeviceHub({"usb": SimDevice()}, "usb").other() is None


def test_add_wires_and_starts_when_running():
    hub = DeviceHub({"usb": SimDevice(boot_time=0.0, rate=100)}, "usb")
    hub.start()
    try:
        late = SimDevice(boot_time=0.0, rate=100)
        hub.add("wifi", late)
        assert late._thread is not None and late._thread.is_alive()
        with pytest.raises(ValueError):
            hub.add("wifi", SimDevice())
        seen = []
        hub.on_status = seen.append
        hub.switch("wifi")
        late.on_status("online")
        assert seen[0] == "switched:wifi" and "online" in seen
    finally:
        hub.stop()


def test_add_after_stop_does_not_start():
    hub = DeviceHub({"usb": SimDevice(boot_time=0.0, rate=100)}, "usb")
    hub.start()
    hub.stop()
    late = SimDevice(boot_time=0.0, rate=100)
    hub.add("wifi", late)
    assert late._thread is None
