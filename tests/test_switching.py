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


T405_REPLY = '{"wifi_mode_on_boot":3,"ip":"192.168.1.99","rssi":-40}'


async def test_learning_ignored_while_wifi_active(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        hub.switch("wifi")
        await wait_for(pilot, lambda: app.ready)
        hub.devices["wifi"].on_line("rx", T405_REPLY)
        await pilot.pause(0.2)
        assert hub.devices["wifi"].host == "10.0.0.2"
        assert not (tmp_path / "config.json").exists()


async def test_learning_over_usb_ignores_non_ip(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        hub.devices["usb"].on_line("rx", '{"ip":"sim","rssi":0}')
        await pilot.pause(0.2)
        assert hub.devices["wifi"].host == "10.0.0.2"
        assert not (tmp_path / "config.json").exists()


async def test_probe_stops_on_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "WIFI_PROBE_EVERY", 0.1)
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        wifi_sent = []
        real_send = hub.devices["wifi"].send
        hub.devices["wifi"].send = lambda cmd: (wifi_sent.append(cmd), real_send(cmd))
        assert app.configure_wifi("home", "pw") is True
        assert app._wifi_probe_timer is not None
        await pilot.press("c")
        assert hub.active == "wifi" and app._wifi_probe_timer is None
        await pilot.pause(0.5)
        assert P.cmd_wifi_info() not in wifi_sent


async def test_probe_stops_itself_when_not_on_usb(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "WIFI_PROBE_EVERY", 0.1)
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        assert app.configure_wifi("home", "pw") is True
        hub.switch("wifi")                                # bypasses the action's own stop
        await wait_for(pilot, lambda: app._wifi_probe_timer is None)


async def test_configure_wifi_refused_while_usb_booting(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        usb = hub.devices["usb"]
        sent = []
        usb.send = lambda cmd: sent.append(cmd)
        usb.booting = True
        assert app.configure_wifi("home", "pw") is False
        assert sent == [] and app._wifi_probe_timer is None


async def test_background_usb_reset_while_on_wifi_stops_playback_and_toasts(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        await pilot.press("c")
        await wait_for(pilot, lambda: hub.active == "wifi" and app.ready)
        assert app.start_playback(Sequence("s", "waypoints", [Point(P.HOME.with_joint("base", 1.0), 5.0)]))
        notes = []
        real_notify = app.notify
        app.notify = lambda m, *a, **k: (notes.append((m, k.get("severity"))), real_notify(m, *a, **k))[1]
        hub.devices["usb"].on_status("booting")          # someone replugged / reopened USB
        await wait_for(pilot, lambda: not app.player.running)
        assert any("USB reconnected" in m and sev == "error" for m, sev in notes)
        app._handle_background_status("usb", "booting")     # synchronous view: not ready until fresh feedback
        assert not app.ready and app.state is None and app.needs_sync
        app.torque_on = False
        hub.devices["usb"].on_status("ready")
        await wait_for(pilot, lambda: app.torque_on)


async def test_background_cannot_open_is_ignored_and_other_statuses_only_logged(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        await pilot.press("c")
        await wait_for(pilot, lambda: hub.active == "wifi" and app.ready)
        notes = []
        app.notify = lambda m, *a, **k: notes.append(m)
        hub.devices["usb"].on_status("cannot open /dev/ttyUSB0: nope")
        hub.devices["usb"].on_status("disconnected")
        await pilot.pause(0.2)
        assert notes == [] and app.ready


def _state(torque):
    return P.ArmState(P.HOME, 0.0, 0.0, 0.0, {j: 0 for j in P.JOINTS}, time.monotonic(), torque=torque)


@pytest.mark.parametrize("event", ["online", "switched:wifi"])
async def test_torque_flag_reconciled_from_first_frame_after_online_or_switch(tmp_path, event):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        app.torque_on = True
        app._handle_status(event)
        app._handle_state(_state({j: False for j in P.JOINTS}))
        assert app.torque_on is False
        # one-shot: a later frame does not overwrite what the user chose
        app.torque_on = True
        app._handle_state(_state({j: False for j in P.JOINTS}))
        assert app.torque_on is True


async def test_torque_reconcile_flag_cleared_by_frame_without_torque_fields(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        app._handle_status("online")
        app._handle_state(_state(None))
        assert app.torque_on is True
        app._handle_state(_state({j: False for j in P.JOINTS}))
        assert app.torque_on is True


async def test_setup_probe_ignores_reply_for_other_ssid_and_reports_match(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "WIFI_PROBE_EVERY", 0.1)
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        notes = []
        app.notify = lambda m, *a, **k: notes.append(m)
        assert app.configure_wifi("home", "pw") is True
        notes.clear()
        usb = hub.devices["usb"]
        usb.on_line("rx", '{"sta_ssid":"old-net","ip":"192.168.1.5","rssi":-40}')   # stale reply
        await pilot.pause(0.2)
        assert app._wifi_probe_timer is not None and notes == []
        assert hub.devices["wifi"].host == "10.0.0.2"
        # same IP as the current host still gets reported
        usb.on_line("rx", '{"sta_ssid":"home","ip":"10.0.0.2","rssi":-40}')
        await wait_for(pilot, lambda: app._wifi_probe_timer is None)
        assert any("Arm joined home at 10.0.0.2" in m for m in notes)


async def test_switch_resets_state_synchronously(tmp_path):
    app, hub = make(tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(pilot, lambda: app.ready)
        sent = {"usb": [], "wifi": []}
        for n in sent:
            hub.devices[n].set_target = lambda *a, n=n, **k: sent[n].append(a)
            hub.devices[n].send = lambda cmd, n=n: sent[n].append(cmd)
        app.follow_feedback = True
        app.action_switch_transport()                    # no pilot yield: posted "switched:" not yet run
        assert app.state is None and app.needs_sync and not app.follow_feedback
        assert app._last_state_time == 0.0
        app.jog("base", 0.5)                              # same step: must be refused
        app.action_home()
        assert sent == {"usb": [], "wifi": []}
