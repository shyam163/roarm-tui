import json
import time

import pytest

from roarm import app as app_module
from roarm import protocol as P
from roarm.app import RoArmApp
from roarm.device import SimDevice
from roarm.hub import DeviceHub
from roarm.sequence import Point, Sequence

SIZE = (140, 45)


class SimWifi(SimDevice):
    """SimDevice standing in for WifiDevice (has host/set_host, reports 'online')."""

    def __init__(self, host="10.0.0.2"):
        super().__init__(boot_time=0.0, rate=10)
        self.host = host

    @property
    def port(self):
        return f"wifi {self.host}"

    def set_host(self, host):
        self.host = host


def make(tmp_path, with_wifi=True):
    usb = SimDevice(boot_time=0.0, rate=10)
    devices = {"usb": usb}
    if with_wifi:
        devices["wifi"] = SimWifi()
    hub = DeviceHub(devices, active="usb")
    app = RoArmApp(hub, sequence_dir=tmp_path, config_path=tmp_path / "config.json",
                   wifi_factory=SimWifi)
    return app, hub


async def wait_for(pilot, cond, timeout=4.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await pilot.pause(0.05)


def status(app):
    return str(app.query_one("#status").render())


async def test_switch_key_moves_control_to_wifi(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        await wait_for(pilot, lambda: "[USB]" in status(app))
        await pilot.press("c")
        await wait_for(pilot, lambda: hub.active == "wifi")
        await wait_for(pilot, lambda: app.ready)
        await wait_for(pilot, lambda: "[Wi-Fi]" in status(app))
        await pilot.press("1")
        await pilot.press("d")
        await wait_for(pilot, lambda: hub.devices["wifi"].state.pose.base > 0.05)
        assert abs(hub.devices["usb"].state.pose.base) < 1e-6


async def test_switch_refuses_motion_until_fresh_feedback(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        app.start_playback(Sequence("s", "waypoints", [Point(P.HOME.with_joint("base", 1.0), 5.0)]))
        hub.switch("wifi")
        app._handle_status("switched:wifi")               # run now; the posted copy runs later (idempotent)
        assert not app.player.running
        assert app.state is None and app.needs_sync
        sent = []
        hub.devices["wifi"].set_target = lambda *a, **k: sent.append(a)
        app.jog("base", 0.5)
        assert sent == []                                 # refused: not ready yet
        await wait_for(pilot, lambda: app.ready)


async def test_switch_refused_with_single_transport(tmp_path):
    app, hub = make(tmp_path, with_wifi=False)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        await pilot.press("c")
        assert hub.active == "usb"


async def test_switch_refused_while_recording(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        app.start_recording()
        await pilot.press("c")
        assert hub.active == "usb"


async def test_wifi_disconnect_stops_playback(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        hub.switch("wifi")
        await wait_for(pilot, lambda: app.ready)
        app.start_playback(Sequence("s", "waypoints", [Point(P.HOME, 5.0)]))
        hub.devices["wifi"].connected = False
        hub.devices["wifi"].on_status("disconnected")
        await wait_for(pilot, lambda: not app.player.running)
        assert not app.ready


async def test_learns_wifi_ip_from_t405_reply(tmp_path):
    app, hub = make(tmp_path, with_wifi=False)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        hub.devices["usb"].on_line("rx", '{"wifi_mode_on_boot":3,"sta_ssid":"home","sta_password":"pw",'
                                         '"ap_ssid":"RoArm-M2","ap_password":"12345678","ip":"192.168.1.59","rssi":-40}')
        await wait_for(pilot, lambda: "wifi" in hub.devices)
        assert hub.devices["wifi"].host == "192.168.1.59"
        assert hub.devices["wifi"]._thread is not None          # started
        cfg = json.loads((tmp_path / "config.json").read_text())
        assert cfg == {"wifi_host": "192.168.1.59"}
        await pilot.press("c")
        await wait_for(pilot, lambda: hub.active == "wifi")


async def test_learn_updates_existing_host_and_ignores_zero_ip(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        usb = hub.devices["usb"]
        usb.on_line("rx", '{"ip":"0.0.0.0","rssi":0}')
        await pilot.pause()
        assert hub.devices["wifi"].host == "10.0.0.2"
        usb.on_line("rx", '{"ip":"192.168.1.77","rssi":-50}')
        await wait_for(pilot, lambda: hub.devices["wifi"].host == "192.168.1.77")


async def test_configure_wifi_requires_usb_active(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        hub.switch("wifi")
        await pilot.pause()
        assert app.configure_wifi("home", "pw") is False
    plain = RoArmApp(SimDevice(boot_time=0.0, rate=10), sequence_dir=tmp_path)
    async with plain.run_test(size=SIZE) as pilot:
        assert plain.configure_wifi("home", "pw") is False


async def test_configure_wifi_sends_setup_then_probes(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "WIFI_PROBE_EVERY", 0.1)
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        usb = hub.devices["usb"]
        sent = []
        real_send = usb.send
        usb.send = lambda cmd: (sent.append(cmd), real_send(cmd))
        assert app.configure_wifi("", "pw") is False
        assert app.configure_wifi("home", "pw") is True
        assert sent[0] == P.cmd_wifi_config("home", "pw")
        assert sent[1] == P.cmd_wifi_apply("home", "pw")
        await wait_for(pilot, lambda: P.cmd_wifi_info() in sent)
        assert not (tmp_path / "config.json").exists() or "pw" not in (tmp_path / "config.json").read_text()


async def test_online_status_does_not_claim_torque(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        app.torque_on = False
        app._handle_status("online")                      # synchronously: a feedback frame would clear needs_sync
        assert app.torque_on is False and app.needs_sync
