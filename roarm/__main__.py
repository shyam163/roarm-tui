"""CLI entry point: python -m roarm [--port PORT] [--baud BAUD] [--sim]"""

from __future__ import annotations

import argparse
import glob

from roarm.app import RoArmApp
from roarm.device import ArmDevice, SimDevice


def default_port() -> str:
    matches = sorted(glob.glob("/dev/serial/by-id/*CP210*"))
    return matches[0] if matches else "/dev/ttyUSB0"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="roarm", description="Terminal UI for the Waveshare RoArm-M2-S")
    ap.add_argument("--port", default=None, help="serial port (default: auto-detect CP210x, else /dev/ttyUSB0)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--sim", action="store_true", help="run against a simulated arm")
    args = ap.parse_args(argv)
    device = SimDevice() if args.sim else ArmDevice(args.port or default_port(), args.baud)
    RoArmApp(device).run()


if __name__ == "__main__":
    main()
