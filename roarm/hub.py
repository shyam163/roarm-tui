"""DeviceHub: several transports to the same arm behind the single-device interface.

Exactly one transport is active: commands go to it and only its callbacks reach the
app. Inactive transports keep running (the USB port must stay open — reopening it
resets the arm) but are ignored.
"""

from __future__ import annotations

from typing import Callable


def _noop(*_args) -> None:
    pass


class DeviceHub:
    LABELS = {"usb": "USB", "wifi": "Wi-Fi", "sim": "Sim"}

    def __init__(self, devices: dict[str, object], active: str):
        if active not in devices:
            raise ValueError(f"unknown transport {active!r}")
        self.devices: dict[str, object] = {}
        self.active = active
        self.on_state: Callable = _noop
        self.on_line: Callable = _noop
        self.on_status: Callable = _noop
        self.on_background_status: Callable = _noop   # (name, status) from an inactive transport
        self._started = False
        for name, dev in devices.items():
            self._attach(name, dev)

    def _attach(self, name: str, dev) -> None:
        self.devices[name] = dev
        dev.on_state = lambda s, n=name: self._relay(n, "on_state", s)
        dev.on_line = lambda d, t, n=name: self._relay(n, "on_line", d, t)
        dev.on_status = lambda t, n=name: self._relay(n, "on_status", t)

    def _relay(self, name: str, callback: str, *args) -> None:
        # self.active is re-read here, at call time, so a callback racing a
        # concurrent switch() is classified against whichever transport is
        # current when it actually arrives, not when it was scheduled.
        if name == self.active:
            getattr(self, callback)(*args)
        elif callback == "on_line" and args[0] == "sys":
            # inactive tx/rx would flood the log with polls; system messages (e.g. a
            # USB reset while we're on Wi-Fi) must stay visible. on_state stays dropped.
            self.on_line("sys", f"[{name}] {args[1]}")
        elif callback == "on_status":
            self.on_background_status(name, args[0])

    # --- the active device's view ---------------------------------------------
    @property
    def device(self):
        return self.devices[self.active]

    @property
    def connected(self) -> bool:
        return self.device.connected

    @property
    def booting(self) -> bool:
        return self.device.booting

    @property
    def state(self):
        return self.device.state

    @property
    def port(self) -> str:
        return self.device.port

    @property
    def label(self) -> str:
        return self.LABELS.get(self.active, self.active)

    # --- device interface -----------------------------------------------------
    def start(self) -> None:
        self._started = True
        for dev in self.devices.values():
            dev.start()

    def stop(self) -> None:
        self._started = False
        for dev in self.devices.values():
            dev.stop()

    def send(self, cmd: dict) -> None:
        self.device.send(cmd)

    def send_now(self, cmd: dict) -> None:
        self.device.send_now(cmd)

    def set_target(self, pose, spd: int = 0, acc: int = 10) -> None:
        self.device.set_target(pose, spd=spd, acc=acc)

    def clear_queue(self) -> None:
        self.device.clear_queue()

    # --- switching --------------------------------------------------------------
    def other(self) -> str | None:
        names = list(self.devices)
        if len(names) < 2:
            return None
        return names[(names.index(self.active) + 1) % len(names)]

    def switch(self, name: str) -> bool:
        if name not in self.devices or name == self.active:
            return False
        self.device.clear_queue()
        self.active = name
        self.device.clear_queue()
        self.on_status(f"switched:{name}")
        return True

    def add(self, name: str, dev) -> None:
        if name in self.devices:
            raise ValueError(f"transport {name!r} already exists")
        self._attach(name, dev)
        if self._started:
            dev.start()
