#!/usr/bin/env python3
import argparse
import glob
import importlib
import sys
import time

try:
    serial = importlib.import_module("serial")
except Exception as exc:
    print(f"[ERROR] pyserial import failed: {exc}", file=sys.stderr)
    sys.exit(2)

try:
    list_ports = importlib.import_module("serial.tools.list_ports")
except Exception:
    list_ports = None


def parse_estop_line(line: str):
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


def candidate_ports(explicit_port: str, patterns: list):
    if explicit_port:
        return [explicit_port]

    ports = []
    if list_ports is not None:
        try:
            for info in list_ports.comports():
                if info.device:
                    ports.append(info.device)
        except Exception:
            pass

    for pattern in patterns:
        ports.extend(glob.glob(pattern))

    return sorted(set(ports), key=lambda p: (0 if "/dev/serial/by-id/" in p else 1, p))


def connect_and_probe(port: str, baud_rate: int, timeout_sec: float, probe_timeout_sec: float):
    try:
        ser = serial.Serial(port, baud_rate, timeout=timeout_sec)
        ser.reset_input_buffer()
    except Exception:
        return None, None

    deadline = time.monotonic() + probe_timeout_sec
    while time.monotonic() < deadline:
        raw = ser.readline()
        if not raw:
            continue

        line = raw.decode("utf-8", errors="replace").strip()
        if line.startswith("ESTOP,BOOT"):
            return ser, None

        parsed = parse_estop_line(line)
        if parsed is not None:
            return ser, parsed

    try:
        ser.close()
    except Exception:
        pass
    return None, None


def main():
    parser = argparse.ArgumentParser(description="Standalone estop serial parser test")
    parser.add_argument("--serial-port", default="", help="Explicit serial port path")
    parser.add_argument("--baud-rate", type=int, default=115200)
    parser.add_argument("--serial-timeout-sec", type=float, default=0.02)
    parser.add_argument("--probe-timeout-sec", type=float, default=3.0)
    parser.add_argument("--line-stale-timeout-sec", type=float, default=10.0)
    parser.add_argument("--reconnect-interval-sec", type=float, default=1.0)
    parser.add_argument(
        "--port-pattern",
        action="append",
        default=["/dev/serial/by-id/*", "/dev/ttyACM*", "/dev/ttyUSB*"],
        help="Port discovery pattern (repeatable)",
    )
    args = parser.parse_args()

    ser = None
    active_port = ""
    triggered = True
    plugged_in = False
    last_line_time = 0.0
    next_connect_try = 0.0
    last_print = 0.0
    last_state = None

    try:
        while True:
            now = time.monotonic()

            if ser is None and now >= next_connect_try:
                for port in candidate_ports(args.serial_port, args.port_pattern):
                    conn, initial_state = connect_and_probe(
                        port,
                        args.baud_rate,
                        args.serial_timeout_sec,
                        args.probe_timeout_sec,
                    )
                    if conn is None:
                        continue

                    ser = conn
                    active_port = port
                    plugged_in = True
                    last_line_time = time.monotonic()
                    if initial_state is not None:
                        triggered = initial_state
                    print(f"[INFO] connected: {port}")
                    break

                if ser is None:
                    plugged_in = False
                    next_connect_try = now + args.reconnect_interval_sec

            if ser is not None:
                try:
                    got_data = False
                    while True:
                        raw = ser.readline()
                        if not raw:
                            break

                        got_data = True
                        last_line_time = time.monotonic()
                        line = raw.decode("utf-8", errors="replace").strip()
                        parsed = parse_estop_line(line)
                        if parsed is not None:
                            triggered = parsed

                    if now - last_line_time > args.line_stale_timeout_sec:
                        plugged_in = False
                    elif got_data:
                        plugged_in = True
                except Exception as exc:
                    print(f"[WARN] disconnected: {exc}")
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None
                    active_port = ""
                    plugged_in = False
                    next_connect_try = now + args.reconnect_interval_sec

            state = (plugged_in, triggered, active_port)
            if state != last_state or (now - last_print) > 2.0:
                print(
                    f"plugged_in={plugged_in} triggered={triggered} "
                    f"port={active_port or '<none>'}"
                )
                last_state = state
                last_print = now

            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
