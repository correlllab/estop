# estop

ROS 2 package to bridge an Arduino-based emergency stop button to:

- ROS 2 topics:
  - `/estop/triggered` (`std_msgs/Bool`)
  - `/estop/plugged_in` (`std_msgs/Bool`)
- Optional raw CycloneDDS topic:
  - `h12/estop_status_raw`
    - type `h12.estop.EstopStatusRaw`
    - fields: `triggered` (`bool`), `plugged_in` (`bool`), `sequence_id` (`int`), `stamp_ms` (`int`, epoch time)

## Behavior

- Continuously attempts to connect to `/dev/ttyACM0` (configurable).
- Parses serial lines from Arduino in format `ESTOP,0` / `ESTOP,1`.
- Publishes status at fixed rate (`50 Hz` default) even when disconnected.
- Applies software debounce (`state_debounce_sec=0.08` default) to suppress spurious state flip-flops.
- `plugged_in=false` when serial cannot be opened or data goes stale.
- NOTE: Fail-safe mode forces `triggered=true` while disconnected.

## Architecture

- `estop/serial_interface.py`: serial discovery, probe, parsing, stale handling, and reconnect.
- `estop/estop_node.py`: ROS 2 + raw DDS publisher only.
- `estop/serial_status_test.py`: standalone serial parser test executable.

## Arduino Firmware

Flash [arduino/estop_serial.ino](arduino/estop_serial.ino) to your UNO.

Default configuration:
- Digital pin: `13`
- Serial baud: `115200`
- Message format: `ESTOP,0|1`

## Dependencies

ROS dependencies are declared in `package.xml`.

Python runtime dependencies:
- required: `pyserial`
- optional for raw DDS publishing: `cyclonedds`

If you are using a venv without pip in this workspace, use `uv pip`:

```bash
uv pip install --python .venv310/bin/python pyserial cyclonedds
```

## Build

From ros2 workspace root (one level above `src`):

```bash
colcon build --paths src/estop
source install/setup.bash
```

## Run

```bash
ros2 launch estop estop.launch.py
```

Or run node directly:

```bash
ros2 run estop estop_node
```

Override publish rate if needed:

```bash
ros2 run estop estop_node --ros-args -p publish_rate_hz:=200.0
```

If your wiring is noisy or the button bounces, increase debounce window:

```bash
ros2 run estop estop_node --ros-args -p state_debounce_sec:=0.15
```

If you see `package 'estop' not found`, source the workspace in the same shell you launch from:

```bash
cd /home/humanoid/ws_ctrl
source install/setup.bash
ros2 pkg list | grep '^estop$'
```

## Useful checks

```bash
ros2 topic echo /estop/triggered
ros2 topic echo /estop/plugged_in
```

If `raw_dds_enabled` is true and `cyclonedds` is installed, the node also writes raw DDS samples on `h12/estop_status_raw`.

## Raw CycloneDDS subscriber test

Run the ROS-side publisher first, then in another shell:

```bash
cd /home/humanoid/ws_ctrl
source install/setup.bash
ros2 run estop estop_raw_dds_subscriber
```

Optional topic override:

```bash
ros2 run estop estop_raw_dds_subscriber -- --topic h12/estop_status_raw
```

## Serial parser test (no ROS topics required)

This directly exercises estop serial discovery + parsing:

```bash
cd /home/humanoid/ws_ctrl/src/estop
conda deactivate
python3 scripts/estop_serial_test.py \
  --serial-port /dev/serial/by-id/usb-Arduino__www.arduino.cc__0043_55338343539351817262-if00
```

This script is fully outside ROS 2 (no `ros2 run`, no workspace sourcing).

## Safe unique estop port selection

The node now supports robust matching with probe validation so it does not attach to the wrong serial device.

Recommended order:

1. Use `/dev/serial/by-id/*` if available.
2. Set exact USB `serial_number` if your adapter exposes one.
3. Optionally constrain by `usb_vid` and `usb_pid`.
4. Keep `require_estop_signature=true` so the node only accepts devices emitting `ESTOP,BOOT` or `ESTOP,0|1`.

Defaults tuned for unplug/replug robustness:

- `probe_timeout_sec=3.0`
- `line_stale_timeout_sec=10.0`
- `stale_reopen_timeout_sec=20.0`
- `reconnect_interval_sec=1.0`

Example launch overrides:

```bash
ros2 run estop estop_node --ros-args \
  -p serial_port:=/dev/serial/by-id/usb-Arduino__www.arduino.cc__0043_XXXXXXXX-if00 \
  -p require_estop_signature:=true
```

or auto-discovery with USB filters:

```bash
ros2 run estop estop_node --ros-args \
  -p serial_port:="" \
  -p usb_vid:=9025 \
  -p usb_pid:=67 \
  -p require_estop_signature:=true
```
