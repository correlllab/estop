#!/usr/bin/env python3
import importlib
import glob
import time
from dataclasses import dataclass
from typing import Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

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

try:
    DomainParticipant = importlib.import_module("cyclonedds.domain").DomainParticipant
    IdlStruct = importlib.import_module("cyclonedds.idl").IdlStruct
    DataWriter = importlib.import_module("cyclonedds.pub").DataWriter
    Publisher = importlib.import_module("cyclonedds.pub").Publisher
    Topic = importlib.import_module("cyclonedds.topic").Topic

    HAVE_CYCLONEDDS = True
except Exception:
    HAVE_CYCLONEDDS = False


if HAVE_CYCLONEDDS:

    @dataclass
    class EstopStatusRaw(IdlStruct, typename="h12.estop.EstopStatusRaw"):
        triggered: bool
        plugged_in: bool
        sequence_id: int
        stamp_ms: int


class EstopNode(Node):
    def __init__(self) -> None:
        super().__init__("estop_node")

        self.declare_parameter("serial_port", "")
        self.declare_parameter(
            "serial_port_candidates",
            ["/dev/serial/by-id/*", "/dev/ttyACM*", "/dev/ttyUSB*"],
        )
        self.declare_parameter("serial_number", "")
        self.declare_parameter("usb_vid", -1)
        self.declare_parameter("usb_pid", -1)
        self.declare_parameter("baud_rate", 115200)
        self.declare_parameter("serial_timeout_sec", 0.02)
        self.declare_parameter("probe_timeout_sec", 1.0)
        self.declare_parameter("probe_max_lines", 100)
        self.declare_parameter("require_estop_signature", True)
        self.declare_parameter("publish_rate_hz", 30.0)
        self.declare_parameter("line_stale_timeout_sec", 5.0)
        self.declare_parameter("reconnect_interval_sec", 1.0)
        self.declare_parameter("fail_safe_triggered_on_disconnect", True)
        self.declare_parameter("raw_dds_enabled", True)
        self.declare_parameter("raw_dds_topic", "h12/estop_status_raw")

        self.serial_port = str(self.get_parameter("serial_port").value)
        self.serial_port_candidates = list(
            self.get_parameter("serial_port_candidates").value
        )
        self.serial_number = str(self.get_parameter("serial_number").value)
        self.usb_vid = int(self.get_parameter("usb_vid").value)
        self.usb_pid = int(self.get_parameter("usb_pid").value)
        self.baud_rate = int(self.get_parameter("baud_rate").value)
        self.serial_timeout_sec = float(self.get_parameter("serial_timeout_sec").value)
        self.probe_timeout_sec = float(self.get_parameter("probe_timeout_sec").value)
        self.probe_max_lines = int(self.get_parameter("probe_max_lines").value)
        self.require_estop_signature = bool(
            self.get_parameter("require_estop_signature").value
        )
        self.publish_rate_hz = float(self.get_parameter("publish_rate_hz").value)
        self.line_stale_timeout_sec = float(self.get_parameter("line_stale_timeout_sec").value)
        self.reconnect_interval_sec = float(self.get_parameter("reconnect_interval_sec").value)
        self.fail_safe_on_disconnect = bool(
            self.get_parameter("fail_safe_triggered_on_disconnect").value
        )
        self.raw_dds_enabled = bool(self.get_parameter("raw_dds_enabled").value)
        self.raw_dds_topic = str(self.get_parameter("raw_dds_topic").value)

        self.triggered_pub = self.create_publisher(Bool, "estop/triggered", 10)
        self.plugged_pub = self.create_publisher(Bool, "estop/plugged_in", 10)

        self._serial: Optional[object] = None
        self._connected_port = ""
        self._next_connect_try = 0.0
        self._last_line_time = 0.0
        self._stale_active = False

        # Fail-safe default until we can confirm device state.
        self._triggered = True
        self._plugged_in = False
        self._sequence_id = 0

        self._dds_writer = None
        self._init_raw_dds()

        self.create_timer(0.02, self._io_tick)
        self.create_timer(1.0 / max(1.0, self.publish_rate_hz), self._publish_tick)

        self.get_logger().info(
            f"Started estop node (serial_port='{self.serial_port or 'AUTO'}', baud={self.baud_rate})"
        )

    def _init_raw_dds(self) -> None:
        if not self.raw_dds_enabled:
            self.get_logger().info("Raw DDS publishing disabled by parameter")
            return

        if not HAVE_CYCLONEDDS:
            self.get_logger().warn(
                "CycloneDDS Python bindings not installed; raw DDS publishing disabled"
            )
            return

        try:
            participant = DomainParticipant()
            publisher = Publisher(participant)
            topic = Topic(participant, self.raw_dds_topic, EstopStatusRaw)
            self._dds_writer = DataWriter(publisher, topic)
            self.get_logger().info(
                f"Raw DDS publishing enabled on topic '{self.raw_dds_topic}'"
            )
        except Exception as exc:
            self.get_logger().error(f"Failed to initialize raw DDS publisher: {exc}")
            self._dds_writer = None

    def _connect_serial(self) -> None:
        if serial is None:
            self._plugged_in = False
            self.get_logger().error("pyserial is not installed; cannot read estop serial")
            return

        now = time.monotonic()
        if now < self._next_connect_try or self._serial is not None:
            return

        candidates = self._candidate_ports()
        if not candidates:
            self._plugged_in = False
            self._next_connect_try = now + self.reconnect_interval_sec
            return

        for port in candidates:
            ser = self._open_and_probe(port)
            if ser is None:
                continue

            self._serial = ser
            self._connected_port = port
            self._last_line_time = now
            self._plugged_in = True
            self.get_logger().info(f"Connected to estop device on {port}")
            return

        self._serial = None
        self._connected_port = ""
        self._plugged_in = False
        self._next_connect_try = now + self.reconnect_interval_sec

    def _candidate_ports(self) -> list:
        if self.serial_port.strip():
            return [self.serial_port.strip()]

        candidates = []

        if list_ports is not None:
            try:
                for info in list_ports.comports():
                    if self.usb_vid >= 0 and info.vid != self.usb_vid:
                        continue
                    if self.usb_pid >= 0 and info.pid != self.usb_pid:
                        continue
                    if self.serial_number and (info.serial_number or "") != self.serial_number:
                        continue
                    if info.device:
                        candidates.append(info.device)
            except Exception:
                pass

        if not candidates:
            for pattern in self.serial_port_candidates:
                candidates.extend(glob.glob(str(pattern)))

        deduped = sorted(set(candidates), key=lambda p: (0 if "/dev/serial/by-id/" in p else 1, p))
        return deduped

    def _open_and_probe(self, port: str):
        try:
            ser = serial.Serial(port, self.baud_rate, timeout=self.serial_timeout_sec)
            ser.reset_input_buffer()
        except Exception:
            return None

        if not self.require_estop_signature:
            return ser

        deadline = time.monotonic() + max(0.05, self.probe_timeout_sec)
        lines = 0
        try:
            while time.monotonic() < deadline and lines < max(1, self.probe_max_lines):
                raw = ser.readline()
                if not raw:
                    continue
                lines += 1
                line = raw.decode("utf-8", errors="replace").strip()
                if line.startswith("ESTOP,BOOT"):
                    return ser
                parsed = self._parse_estop_line(line)
                if parsed is not None:
                    self._triggered = parsed
                    return ser
        except Exception:
            pass

        try:
            ser.close()
        except Exception:
            pass
        return None

    def _disconnect_serial(self, reason: str) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        self._connected_port = ""
        self._plugged_in = False
        self._next_connect_try = time.monotonic() + self.reconnect_interval_sec
        self._stale_active = False
        self.get_logger().warn(f"Serial disconnected: {reason}")

    def _parse_estop_line(self, line: str) -> Optional[bool]:
        # Preferred format from Arduino sketch: "ESTOP,0" or "ESTOP,1"
        if line.startswith("ESTOP,"):
            value = line.split(",", 1)[1].strip()
            if value == "1":
                return True
            if value == "0":
                return False
            return None

        upper = line.upper()

        # Backward compatibility with current test sketch output.
        if "E-STOP TRIGGERED" in upper:
            return True

        if "E-STOP CLEAR" in upper or "E-STOP RELEASED" in upper:
            return False

        return None

    def _io_tick(self) -> None:
        if self._serial is None:
            self._connect_serial()
            return

        try:
            received_any = False
            while True:
                raw = self._serial.readline()
                if not raw:
                    break

                received_any = True
                self._last_line_time = time.monotonic()
                line = raw.decode("utf-8", errors="replace").strip()
                parsed = self._parse_estop_line(line)
                if parsed is not None:
                    self._triggered = parsed
                    if not self._plugged_in:
                        self.get_logger().info(
                            f"Estop serial data stream active on {self._connected_port}"
                        )

            if received_any:
                self._plugged_in = True
                if self._stale_active:
                    self._stale_active = False
                    self.get_logger().info(
                        f"Estop serial stream recovered on {self._connected_port}"
                    )

            if time.monotonic() - self._last_line_time > self.line_stale_timeout_sec:
                if not self._stale_active:
                    self._stale_active = True
                    self.get_logger().warn(
                        "Serial data stale; keeping port open and waiting for stream recovery"
                    )
                self._plugged_in = False
        except SerialException as exc:
            self._disconnect_serial(str(exc))
        except OSError as exc:
            self._disconnect_serial(str(exc))

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def _publish_tick(self) -> None:
        effective_triggered = (
            self._triggered if self._plugged_in else self.fail_safe_on_disconnect
        )

        triggered_msg = Bool()
        triggered_msg.data = bool(effective_triggered)
        self.triggered_pub.publish(triggered_msg)

        plugged_msg = Bool()
        plugged_msg.data = bool(self._plugged_in)
        self.plugged_pub.publish(plugged_msg)

        self._publish_raw_dds(effective_triggered, self._plugged_in)

    def _publish_raw_dds(self, triggered: bool, plugged_in: bool) -> None:
        if self._dds_writer is None:
            return

        try:
            self._sequence_id += 1
            sample = EstopStatusRaw(
                triggered=bool(triggered),
                plugged_in=bool(plugged_in),
                sequence_id=self._sequence_id,
                stamp_ms=int(time.time() * 1000),
            )
            self._dds_writer.write(sample)
        except Exception as exc:
            self.get_logger().error(f"Raw DDS publish error: {exc}")
            self._dds_writer = None


def main(args=None) -> None:
    rclpy.init(args=args)
    node = EstopNode()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.close()
        node.destroy_node()
        try:
            if rclpy.ok():
                rclpy.shutdown()
        except Exception:
            # Launch may have already shut down rcl context after SIGINT.
            pass


if __name__ == "__main__":
    main()
