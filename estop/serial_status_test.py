#!/usr/bin/env python3
import argparse
import time

from estop.serial_interface import EstopSerialConfig, EstopSerialInterface


def main() -> None:
    parser = argparse.ArgumentParser(description="Simple estop serial parser test")
    parser.add_argument("--serial-port", default="", help="Explicit serial port path")
    parser.add_argument("--baud-rate", type=int, default=115200)
    parser.add_argument("--probe-timeout-sec", type=float, default=3.0)
    parser.add_argument("--line-stale-timeout-sec", type=float, default=10.0)
    parser.add_argument(
        "--require-estop-signature",
        action="store_true",
        default=False,
        help="Require ESTOP signature during probe",
    )
    args = parser.parse_args()

    cfg = EstopSerialConfig(
        serial_port=args.serial_port,
        baud_rate=args.baud_rate,
        probe_timeout_sec=args.probe_timeout_sec,
        line_stale_timeout_sec=args.line_stale_timeout_sec,
        require_estop_signature=args.require_estop_signature,
    )

    bridge = EstopSerialInterface(
        config=cfg,
        log_info=lambda m: print(f"[INFO] {m}"),
        log_warn=lambda m: print(f"[WARN] {m}"),
        log_error=lambda m: print(f"[ERROR] {m}"),
    )

    last_print = 0.0
    last_tuple = None

    try:
        while True:
            bridge.tick()

            snapshot = (bridge.plugged_in, bridge.triggered, bridge.connected_port)
            now = time.monotonic()

            # Print on state changes and at heartbeat interval.
            if snapshot != last_tuple or (now - last_print) > 2.0:
                print(
                    "plugged_in={} triggered={} port={}".format(
                        bridge.plugged_in,
                        bridge.triggered,
                        bridge.connected_port or "<none>",
                    )
                )
                last_tuple = snapshot
                last_print = now

            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
