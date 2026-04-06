from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription(
        [
            Node(
                package="estop",
                executable="estop_node",
                name="estop_node",
                output="screen",
                parameters=[
                    {
                        "serial_port": "",
                        "serial_port_candidates": [
                            "/dev/serial/by-id/*",
                            "/dev/ttyACM*",
                            "/dev/ttyUSB*",
                        ],
                        "serial_number": "",
                        "usb_vid": -1,
                        "usb_pid": -1,
                        "baud_rate": 115200,
                        "probe_timeout_sec": 1.0,
                        "probe_max_lines": 100,
                        "require_estop_signature": True,
                        "publish_rate_hz": 30.0,
                        "line_stale_timeout_sec": 5.0,
                        "fail_safe_triggered_on_disconnect": True,
                        "raw_dds_enabled": True,
                        "raw_dds_topic": "h12/estop_status_raw",
                    }
                ],
            )
        ]
    )
