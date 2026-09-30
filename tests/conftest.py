import json

import serial


class FakeSerial:
    def __init__(self):
        self.rx = bytearray()
        self.tx: list[bytes] = []
        self.closed = False
        self.fail = False

    def _check(self):
        if self.fail:
            raise serial.SerialException("device disconnected")

    @property
    def in_waiting(self):
        self._check()
        return len(self.rx)

    def read(self, n=1):
        self._check()
        data = bytes(self.rx[:n])
        del self.rx[:n]
        return data

    def write(self, data):
        self._check()
        self.tx.append(bytes(data))
        return len(data)

    def close(self):
        self.closed = True

    def feed(self, text: str):
        self.rx += text.encode()

    def sent(self) -> list[dict]:
        return [json.loads(b) for b in self.tx]


class FakeClock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt
