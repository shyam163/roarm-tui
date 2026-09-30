import argparse

import pytest

from roarm import __main__ as cli
from roarm.device import ArmDevice
from roarm.hub import DeviceHub
from roarm.wifi import WifiDevice


def args(**kw):
    base = dict(port=None, baud=115200, sim=False, wifi=None, no_usb=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def usb_present(monkeypatch):
    monkeypatch.setattr(cli, "default_port", lambda: "/dev/fake-usb")
    monkeypatch.setattr(cli, "port_exists", lambda p: p == "/dev/fake-usb")


@pytest.fixture
def usb_absent(monkeypatch):
    monkeypatch.setattr(cli, "default_port", lambda: "/dev/fake-usb")
    monkeypatch.setattr(cli, "port_exists", lambda p: False)


def test_usb_only_without_config(usb_present):
    hub = cli.build_device(args(), {})
    assert isinstance(hub, DeviceHub) and list(hub.devices) == ["usb"] and hub.active == "usb"
    assert isinstance(hub.devices["usb"], ArmDevice)


def test_config_host_adds_inactive_wifi(usb_present):
    hub = cli.build_device(args(), {"wifi_host": "192.168.1.59"})
    assert set(hub.devices) == {"usb", "wifi"} and hub.active == "usb"
    assert isinstance(hub.devices["wifi"], WifiDevice) and hub.devices["wifi"].host == "192.168.1.59"


def test_wifi_flag_with_host_starts_on_wifi(usb_present):
    hub = cli.build_device(args(wifi="10.0.0.5"), {"wifi_host": "192.168.1.59"})
    assert hub.active == "wifi" and hub.devices["wifi"].host == "10.0.0.5"
    assert "usb" in hub.devices


def test_wifi_flag_without_host_uses_config_then_ap(usb_absent):
    assert cli.build_device(args(wifi=""), {"wifi_host": "192.168.1.59"}).devices["wifi"].host == "192.168.1.59"
    hub = cli.build_device(args(wifi=""), {})
    assert hub.devices["wifi"].host == cli.DEFAULT_AP_HOST
    assert list(hub.devices) == ["wifi"]                  # no USB plugged in → Wi-Fi only


def test_no_usb(usb_present):
    hub = cli.build_device(args(no_usb=True, wifi=""), {"wifi_host": "h"})
    assert list(hub.devices) == ["wifi"]
    with pytest.raises(SystemExit):
        cli.build_device(args(no_usb=True), {})


def test_explicit_port_always_used(usb_absent):
    hub = cli.build_device(args(port="/dev/ttyACM9"), {})
    assert hub.devices["usb"].port == "/dev/ttyACM9"


def test_no_transport_found_falls_back_to_usb_retry(usb_absent):
    hub = cli.build_device(args(), {})
    assert list(hub.devices) == ["usb"]                   # keeps retrying + shows "cannot open"


def test_main_sim_and_hub(monkeypatch, usb_present):
    ran = {}
    monkeypatch.setattr(cli.RoArmApp, "run", lambda self: ran.setdefault("app", self))
    monkeypatch.setattr(cli, "load_config", lambda: {})
    cli.main(["--sim"])
    assert type(ran.pop("app").device).__name__ == "SimDevice"
    cli.main(["--wifi", "10.0.0.5"])
    app = ran.pop("app")
    assert isinstance(app.device, DeviceHub) and app.device.active == "wifi"
    assert app.config_path == cli.CONFIG_PATH
