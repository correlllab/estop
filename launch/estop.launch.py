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
                        "probe_timeout_sec": 3.0,
                        "probe_max_lines": 200,
                        "post_open_delay_sec": 0.25,
                        "require_estop_signature": True,
                        "publish_rate_hz": 100.0,
                        "state_debounce_sec": 0.08,
                        "line_stale_timeout_sec": 0.1,
                        "stale_connect_grace_sec": 1.5,
                        "stale_reopen_timeout_sec": 3.0,
                        "reconnect_interval_sec": 1.0,
                        "allow_fallback_from_explicit_port": False,
                        "fail_safe_triggered_on_disconnect": True,
                        "raw_dds_enabled": True,
                        "raw_dds_topic": "h12/estop_status_raw",
                    }
                ],
            )
        ]
    )
