#!/usr/bin/env python3
import glob
import importlib
import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

try:
    serial = importlib.import_module("serial")
    SerialException = getattr(serial, "SerialException", Exception)
except Exception:
    serial = None
    SerialException = Exception

try:
    list_ports = importlib.import_module("serial.tools.list_ports")
except Exception:
    list_ports = None


def parse_estop_line(line: str) -> Optional[bool]:
    if line.startswith("ESTOP,"):
        value = line.split(",", 1)[1].strip()
        if value == "1":
            return True
        if value == "0":
            return False
        return None

    upper = line.upper()
    if "E-STOP TRIGGERED" in upper:
        return True
    if "E-STOP CLEAR" in upper or "E-STOP RELEASED" in upper:
        return False
    return None


@dataclass
class EstopSerialConfig:
    serial_port: str = ""
    serial_port_candidates: list = None
    serial_number: str = ""
    usb_vid: int = -1
    usb_pid: int = -1
    baud_rate: int = 115200
    serial_timeout_sec: float = 0.02
    probe_timeout_sec: float = 3.0
    probe_max_lines: int = 200
    post_open_delay_sec: float = 0.25
    require_estop_signature: bool = True
    line_stale_timeout_sec: float = 10.0
    stale_connect_grace_sec: float = 1.5
    stale_reopen_timeout_sec: float = 20.0
    reconnect_interval_sec: float = 1.0
    allow_fallback_from_explicit_port: bool = False
    state_debounce_sec: float = 0.08

    def __post_init__(self) -> None:
        if self.serial_port_candidates is None:
            self.serial_port_candidates = [
                "/dev/serial/by-id/*",
                "/dev/ttyACM*",
                "/dev/ttyUSB*",
            ]


class EstopSerialInterface:
    def __init__(
        self,
        config: EstopSerialConfig,
        log_info: Callable[[str], None] = lambda _m: None,
        log_warn: Callable[[str], None] = lambda _m: None,
        log_error: Callable[[str], None] = lambda _m: None,
    ) -> None:
        self.config = config
        self._log_info = log_info
        self._log_warn = log_warn
        self._log_error = log_error

        self._serial = None
        self.connected_port = ""
        self.next_connect_try = 0.0
        self.last_line_time = 0.0
        self.connected_since = 0.0
        self.stale_active = False

        self.triggered = None
        self._raw_triggered = True
        self._pending_state: Optional[bool] = None
        self._pending_since = 0.0
        self.plugged_in = None

        while self.triggered is None and self.plugged_in is None:
            self.tick()




    def tick(self) -> None:
        now = time.monotonic()

        if self._serial is None:
            self._connect(now)
            return

        self._read_lines(now)
        self._update_stale(now)

    def close(self) -> None:
        self._disconnect("shutdown", warn=False)

    def _candidate_ports(self) -> list:
        explicit = self.config.serial_port.strip()
        if explicit and not self.config.allow_fallback_from_explicit_port:
            return [explicit]

        candidates = []
        if explicit:
            candidates.append(explicit)

        if list_ports is not None:
            try:
                for info in list_ports.comports():
                    if self.config.usb_vid >= 0 and info.vid != self.config.usb_vid:
                        continue
                    if self.config.usb_pid >= 0 and info.pid != self.config.usb_pid:
                        continue
                    if (
                        self.config.serial_number
                        and (info.serial_number or "") != self.config.serial_number
                    ):
                        continue
                    if info.device:
                        candidates.append(info.device)
            except Exception:
                pass

        for pattern in self.config.serial_port_candidates:
            candidates.extend(glob.glob(str(pattern)))

        # Prefer stable /dev/serial/by-id entries over ephemeral tty names.
        return sorted(
            set(candidates), key=lambda p: (0 if "/dev/serial/by-id/" in p else 1, p)
        )

    def _connect(self, now: float) -> None:
        if serial is None:
            self.plugged_in = False
            self._log_error("pyserial is not installed; cannot read estop serial")
            self.next_connect_try = now + self.config.reconnect_interval_sec
            return

        if now < self.next_connect_try:
            return

        candidates = self._candidate_ports()
        if not candidates:
            self.plugged_in = False
            self.next_connect_try = now + self.config.reconnect_interval_sec
            return

        for port in candidates:
            ser = self._open_and_probe(port)
            if ser is None:
                continue

            self._serial = ser
            try:
                # Use non-blocking reads during steady-state operation so the ROS
                # executor can honor higher publish rates.
                self._serial.timeout = 0.0
            except Exception:
                pass
            self.connected_port = port
            now_connected = time.monotonic()
            self.last_line_time = now_connected
            self.connected_since = now_connected
            self.stale_active = False
            self.plugged_in = True
            self._log_info(f"Connected to estop device on {port}")
            return

        self.plugged_in = False
        self.next_connect_try = now + self.config.reconnect_interval_sec

    def _open_and_probe(self, port: str):
        try:
            ser = serial.Serial(
                port,
                self.config.baud_rate,
                timeout=self.config.serial_timeout_sec,
                exclusive=True,
            )
            ser.reset_input_buffer()
        except TypeError:
            try:
                ser = serial.Serial(
                    port,
                    self.config.baud_rate,
                    timeout=self.config.serial_timeout_sec,
                )
                ser.reset_input_buffer()
            except Exception:
                return None
        except Exception:
            return None

        if self.config.post_open_delay_sec > 0.0:
            time.sleep(self.config.post_open_delay_sec)

        if not self.config.require_estop_signature:
            return ser

        deadline = time.monotonic() + max(0.05, self.config.probe_timeout_sec)
        lines = 0
        try:
            while time.monotonic() < deadline and lines < max(
                1, self.config.probe_max_lines
            ):
                raw = ser.readline()
                if not raw:
                    time.sleep(0.001)
                    continue

                lines += 1
                decoded = raw.decode("utf-8", errors="replace").strip()
                if decoded.startswith("ESTOP,BOOT"):
                    return ser

                parsed = parse_estop_line(decoded)
                if parsed is not None:
                    self._raw_triggered = parsed
                    self._apply_debounced_state(parsed, time.monotonic())
                    return ser
        except Exception:
            pass

        try:
            ser.close()
        except Exception:
            pass
        return None

    def _read_lines(self, now: float) -> None:
        try:
            got_data = False
            while True:
                waiting = 0
                try:
                    waiting = int(getattr(self._serial, "in_waiting", 0))
                except Exception:
                    waiting = 0

                # In non-blocking mode, skip readline when no bytes are pending.
                if waiting <= 0:
                    break

                raw = self._serial.readline()
                if not raw:
                    break

                got_data = True
                line_now = time.monotonic()
                self.last_line_time = line_now
                line = raw.decode("utf-8", errors="replace").strip()
                parsed = parse_estop_line(line)
                if parsed is not None:
                    self._raw_triggered = parsed
                    self._apply_debounced_state(parsed, line_now)

            if got_data and self.stale_active:
                self.stale_active = False
                self._log_info(
                    f"Estop serial stream recovered on {self.connected_port}"
                )

            if got_data:
                self.plugged_in = True
        except (SerialException, OSError) as exc:
            self._disconnect(str(exc))

    def _update_stale(self, now: float) -> None:
        if now - self.connected_since < max(0.0, self.config.stale_connect_grace_sec):
            return

        age = now - self.last_line_time
        if age <= self.config.line_stale_timeout_sec:
            return

        if not self.stale_active:
            self.stale_active = True
            self.plugged_in = False
            self._log_warn("Serial data stale; waiting for stream recovery")

        reopen_timeout = self.config.stale_reopen_timeout_sec
        if reopen_timeout > 0 and age > reopen_timeout:
            self._disconnect("serial data stale too long; reopening port")

    def _apply_debounced_state(self, parsed: bool, now: float) -> None:
        # Require state to remain stable for a short window before committing.
        # This suppresses contact bounce and serial jitter-induced flip-flops.
        if parsed == self.triggered:
            self._pending_state = None
            return

        if self._pending_state != parsed:
            self._pending_state = parsed
            self._pending_since = now
            return

        if now - self._pending_since >= max(0.0, self.config.state_debounce_sec):
            self.triggered = parsed
            self._pending_state = None

    def _disconnect(self, reason: str, warn: bool = True) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass

        self._serial = None
        self.connected_port = ""
        self.plugged_in = False
        self.connected_since = 0.0
        self.stale_active = False
        self.next_connect_try = time.monotonic() + self.config.reconnect_interval_sec

        if warn:
            self._log_warn(f"Serial disconnected: {reason}")
