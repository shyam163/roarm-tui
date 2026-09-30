# RoArm-M2-S TUI — Wi-Fi transport & USB/Wi-Fi switching (addendum)

Date: 2026-09-30
Status: Approved in chat
Extends: `2026-09-30-roarm-tui-design.md`

## Goal

Control the arm over Wi-Fi as well as USB, switch between the two from inside
the app, and configure the arm's Wi-Fi from the app.

## Verified facts (this arm, this network)

- Arm configured over USB with `{"T":407,"mode":3,...}` (persisted
  `wifiConfig.json`) + `{"T":404,...}` (applied now): AP+STA. It joins STA
  SSID `shinjitham` at boot and keeps its own AP `RoArm-M2` / `12345678` at
  `192.168.4.1` as a fallback. Confirmed it rejoins after reboot.
- STA IP (DHCP, currently) `192.168.1.59`. `{"T":405}` returns
  `{"wifi_mode_on_boot":3,"sta_ssid":..,"sta_password":..,"ap_ssid":..,"ap_password":..,"ip":"192.168.1.59","rssi":-40}`.
  Note: the T:405 reply contains the Wi-Fi password in clear text.
- HTTP control: `GET http://<ip>/js?json=<url-encoded JSON>`; the response body
  is the reply JSON (for T:105, the `T:1051` feedback object, no echo line).
  Round-trip p50 ≈ 40 ms, p90 ≈ 60–80 ms, max ≈ 160 ms; keep-alive gives no
  meaningful gain.
- The SSIDs `pallan1` and `shinjitham` are isolated from each other; the PC
  must be on `shinjitham` (or the arm's AP) to use Wi-Fi control.

## Design

### WifiDevice (roarm/device.py)

Same public interface as ArmDevice/SimDevice (`start stop send send_now
set_target clear_queue`, `state connected booting port`, callbacks
`on_state on_line on_status`). `port` is `"wifi <host>"`.

- One worker thread, **one request in flight at a time**. Each loop: queued
  command → pending jog target (same 50 ms `jog_interval` coalescing as
  ArmDevice) → feedback poll `{"T":105}` if `poll_interval` (0.05 s) elapsed.
- Request: `GET /js?json=` + `urllib.parse.quote(compact_json)`, timeout 1.0 s,
  via `http.client.HTTPConnection` (stdlib only — no new dependency).
- Response body lines go to `on_line("rx", …)` and through the same
  `parse_line`/`parse_feedback` path as serial.
- No reset on connect → no boot phase: `booting` is False; "ready" is
  reported after the first successful response.
- Failure: 3 consecutive request failures → `connected=False`, queue cleared,
  status `"disconnected"`; retry every 2 s; on success → `"ready"` (the app
  re-syncs targets exactly as after a serial reconnect).
- Injectable `http_factory(host, timeout)` for tests (fake HTTP connection).

### Switching (roarm/app.py)

- New `TransportManager`-style holder in the app: `self.devices = {"usb": ArmDevice|None, "wifi": WifiDevice|None}` and `self.device` = the active one. The inactive device keeps running (so the USB port is never closed/reopened — that would reset the arm) but its callbacks are ignored except `on_line` (logged with a `[usb]`/`[wifi]` prefix).
- `c` key / status-bar badge click → switch: stop playback, `clear_queue()`
  on both, set `needs_sync=True`, `state=None`, activate the other device,
  resync from its next feedback. Refused (warning) if the other transport is
  not configured.
- Status bar shows `USB /dev/…` or `WiFi 192.168.1.59` plus the existing
  connection dot.
- Motion is refused while the active device is not ready (reuses the
  readiness rule from the final-review fix C2).

### CLI (roarm/__main__.py)

- `--wifi [HOST]`: enable the Wi-Fi transport (HOST from arg, else config,
  else `192.168.4.1`); start on Wi-Fi.
- `--port PORT`: USB transport (default auto-detect as today).
- `--no-usb`: don't open the serial port at all (arm on battery / cable
  unplugged) — avoids the reset-on-open entirely.
- Both transports are enabled when a USB port exists and a Wi-Fi host is
  known; start transport = `--wifi` given ? wifi : usb.
- `--sim` unchanged (single SimDevice; switching disabled).

### Config (roarm/config.py)

- `~/.config/roarm/config.json` (created with mode 0600):
  `{"wifi_host": "192.168.1.59"}`. Never stores Wi-Fi passwords.
- Updated automatically whenever a T:405 reply reports a non-empty `ip`.

### Wi-Fi panel (Diagnostics tab)

- Shows mode, STA SSID, IP, RSSI from T:405 (password fields masked `***`
  everywhere, including the serial log line).
- Form: SSID + password (password Input with `password=True`) + "Join
  network" → sends `{"T":407,"mode":3,"ap_ssid":"RoArm-M2","ap_password":"12345678","sta_ssid":…,"sta_password":…}`
  then `{"T":404,…same…}` over the **USB** device only (refused if USB isn't
  available), then polls T:405 until an IP appears (≤ 20 s), saves it to
  config and enables the Wi-Fi transport.

## Security

- The Wi-Fi password is never written to the repo, config, logs or sequence
  files. Any `sta_password`/`ap_password` value in RX/TX log lines is masked.
- The arm's HTTP API is unauthenticated (firmware limitation); document in
  README that anyone on the same network can drive it.

## Testing

- WifiDevice with a fake HTTP connection: priority/coalescing, poll cadence,
  one-in-flight, failure counting → disconnected → reconnect → ready,
  URL encoding.
- Password masking helper unit tests.
- App pilot tests with two SimDevices standing in for usb/wifi: switch key
  clears queues, resyncs, refuses motion until fresh feedback, badge text.
- CLI parsing: `--wifi`, `--wifi HOST`, `--no-usb`, config fallback.
- Manual: switch USB↔Wi-Fi on the real arm, jog on each, unplug USB while on
  Wi-Fi (Wi-Fi keeps working), Wi-Fi setup panel round-trip.
