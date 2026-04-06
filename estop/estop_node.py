#!/usr/bin/env python3
import importlib
import time
from dataclasses import dataclass

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

from estop.serial_interface import EstopSerialConfig, EstopSerialInterface

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
        self.declare_parameter("probe_timeout_sec", 3.0)
        self.declare_parameter("probe_max_lines", 200)
        self.declare_parameter("post_open_delay_sec", 0.25)
        self.declare_parameter("require_estop_signature", True)
        self.declare_parameter("publish_rate_hz", 50.0)
        self.declare_parameter("state_debounce_sec", 0.08)
        self.declare_parameter("line_stale_timeout_sec", 10.0)
        self.declare_parameter("stale_reopen_timeout_sec", 20.0)
        self.declare_parameter("reconnect_interval_sec", 1.0)
        self.declare_parameter("allow_fallback_from_explicit_port", False)
        self.declare_parameter("fail_safe_triggered_on_disconnect", True)
        self.declare_parameter("raw_dds_enabled", True)
        self.declare_parameter("raw_dds_topic", "h12/estop_status_raw")

        self.publish_rate_hz = float(self.get_parameter("publish_rate_hz").value)
        self.fail_safe_on_disconnect = bool(
            self.get_parameter("fail_safe_triggered_on_disconnect").value
        )
        self.raw_dds_enabled = bool(self.get_parameter("raw_dds_enabled").value)
        self.raw_dds_topic = str(self.get_parameter("raw_dds_topic").value)

        self.triggered_pub = self.create_publisher(Bool, "estop/triggered", 10)
        self.plugged_pub = self.create_publisher(Bool, "estop/plugged_in", 10)

        serial_cfg = EstopSerialConfig(
            serial_port=str(self.get_parameter("serial_port").value),
            serial_port_candidates=list(self.get_parameter("serial_port_candidates").value),
            serial_number=str(self.get_parameter("serial_number").value),
            usb_vid=int(self.get_parameter("usb_vid").value),
            usb_pid=int(self.get_parameter("usb_pid").value),
            baud_rate=int(self.get_parameter("baud_rate").value),
            serial_timeout_sec=float(self.get_parameter("serial_timeout_sec").value),
            probe_timeout_sec=float(self.get_parameter("probe_timeout_sec").value),
            probe_max_lines=int(self.get_parameter("probe_max_lines").value),
            post_open_delay_sec=float(self.get_parameter("post_open_delay_sec").value),
            require_estop_signature=bool(self.get_parameter("require_estop_signature").value),
            state_debounce_sec=float(self.get_parameter("state_debounce_sec").value),
            line_stale_timeout_sec=float(self.get_parameter("line_stale_timeout_sec").value),
            stale_reopen_timeout_sec=float(
                self.get_parameter("stale_reopen_timeout_sec").value
            ),
            reconnect_interval_sec=float(self.get_parameter("reconnect_interval_sec").value),
            allow_fallback_from_explicit_port=bool(
                self.get_parameter("allow_fallback_from_explicit_port").value
            ),
        )
        self._serial_if = EstopSerialInterface(
            config=serial_cfg,
            log_info=self.get_logger().info,
            log_warn=self.get_logger().warn,
            log_error=self.get_logger().error,
        )

        self._sequence_id = 0

        self._dds_writer = None
        self._init_raw_dds()

        self.create_timer(0.02, self._io_tick)
        self.create_timer(1.0 / max(1.0, self.publish_rate_hz), self._publish_tick)

        serial_port_label = serial_cfg.serial_port or "AUTO"
        self.get_logger().info(
            f"Started estop node (serial_port='{serial_port_label}', baud={serial_cfg.baud_rate})"
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

    def _io_tick(self) -> None:
        self._serial_if.tick()

    def close(self) -> None:
        self._serial_if.close()

    def _publish_tick(self) -> None:
        effective_triggered = (
            self._serial_if.triggered
            if self._serial_if.plugged_in
            else self.fail_safe_on_disconnect
        )

        triggered_msg = Bool()
        triggered_msg.data = bool(effective_triggered)
        self.triggered_pub.publish(triggered_msg)

        plugged_msg = Bool()
        plugged_msg.data = bool(self._serial_if.plugged_in)
        self.plugged_pub.publish(plugged_msg)

        self._publish_raw_dds(effective_triggered, self._serial_if.plugged_in)

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
