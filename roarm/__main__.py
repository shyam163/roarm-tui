"""CLI entry point: python -m roarm [--port PORT] [--wifi [HOST]] [--no-usb] [--sim]"""

from __future__ import annotations

import argparse
import glob
import os

from roarm.app import RoArmApp
from roarm.config import CONFIG_PATH, load_config
from roarm.device import ArmDevice, SimDevice
from roarm.hub import DeviceHub
from roarm.wifi import WifiDevice

DEFAULT_AP_HOST = "192.168.4.1"   # the arm's own hotspot


def default_port() -> str:
    matches = sorted(glob.glob("/dev/serial/by-id/*CP210*"))
    return matches[0] if matches else "/dev/ttyUSB0"


def port_exists(path: str) -> bool:
    return os.path.exists(path)


def build_device(args: argparse.Namespace, cfg: dict) -> DeviceHub:
    saved_host = cfg.get("wifi_host") if isinstance(cfg.get("wifi_host"), str) else None
    if args.wifi is not None:
        wifi_host = args.wifi or saved_host or DEFAULT_AP_HOST
    else:
        wifi_host = saved_host
    devices: dict[str, object] = {}
    if not args.no_usb:
        port = args.port or default_port()
        # opening a port resets the arm — only open one that exists, unless it's our only option
        if args.port or port_exists(port) or wifi_host is None:
            devices["usb"] = ArmDevice(port, args.baud)
    if wifi_host:
        devices["wifi"] = WifiDevice(wifi_host)
    if not devices:
        raise SystemExit("--no-usb needs a Wi-Fi host: use --wifi HOST")
    active = "wifi" if "wifi" in devices and (args.wifi is not None or "usb" not in devices) else "usb"
    return DeviceHub(devices, active)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="roarm", description="Terminal UI for the Waveshare RoArm-M2-S")
    ap.add_argument("--port", default=None, help="serial port (default: auto-detect CP210x, else /dev/ttyUSB0)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--wifi", nargs="?", const="", default=None, metavar="HOST",
                    help="control over Wi-Fi (HOST, else the saved IP, else the arm's hotspot 192.168.4.1)")
    ap.add_argument("--no-usb", action="store_true", help="don't open the serial port (avoids the reset)")
    ap.add_argument("--sim", action="store_true", help="run against a simulated arm")
    args = ap.parse_args(argv)
    if args.sim:
        RoArmApp(SimDevice()).run()
        return
    RoArmApp(build_device(args, load_config()), config_path=CONFIG_PATH).run()


if __name__ == "__main__":
    main()
