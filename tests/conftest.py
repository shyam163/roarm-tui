import json
import urllib.parse

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


FEEDBACK_JSON = ('{"T":1051,"x":315.2,"y":0,"z":221.9,"b":0,"s":0.02,"e":1.59,"t":3.14,'
                 '"torB":0,"torS":76,"torE":72,"torH":0}')


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body.encode()

    def read(self):
        return self._body


class FakeHTTP:
    """Stands in for http.client.HTTPConnection. Replies to T:105 with feedback."""

    def __init__(self):
        self.requests: list[str] = []
        self.fail = False
        self.fail_next = 0
        self.status = 200
        self.closed = 0
        self.replies = {105: FEEDBACK_JSON}
        self.on_request = None  # optional callback(), run before each request is handled

    def request(self, method, url):
        if self.on_request is not None:
            self.on_request()
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ConnectionRefusedError("connection refused")
        if self.fail:
            raise ConnectionRefusedError("connection refused")
        assert method == "GET"
        self.requests.append(url)

    def getresponse(self):
        cmd = self.sent()[-1]
        return FakeResponse(self.status, self.replies.get(cmd.get("T"), ""))

    def close(self):
        self.closed += 1

    def sent(self) -> list[dict]:
        return [json.loads(urllib.parse.unquote(u.split("json=", 1)[1])) for u in self.requests]
