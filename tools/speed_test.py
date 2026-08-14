#!/usr/bin/env python3
"""GCode speed test: measures actual feed rates by timing repeated moves.

Homes the machine, then for each target speed runs a batch of random XY moves
and a batch of random Z moves, timing each batch.  Reports commanded vs
measured feed rates in mm/sec and mm/min.
"""
import argparse
import math
import random
import serial
import serial.tools.list_ports
import sys
import time

# -- defaults ----------------------------------------------------------------
BED_X = 300
BED_Y = 300
Z_MAX = 40
Z_MIN = 5           # stay above 0 to avoid dragging on the bed
MARGIN = 15          # XY keep-out margin from bed edges
XY_MOVE_LEN = 30    # mm per XY move
Z_MOVE_LEN = 5      # mm per Z move
MOVES_PER_RUN = 20
SPEEDS = [1000, 1500, 2000, 3000, 4000]  # mm/min
BAUD = 115200
TIMEOUT = 10         # serial readline timeout (seconds)
HOME_TIMEOUT = 60    # homing can be slow


def find_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = (p.description or "").lower()
        if any(kw in desc for kw in ("marlin", "printer", "ch340", "cp210",
                                      "arduino", "usb serial", "ft232")):
            return p.device
    if ports:
        return ports[0].device
    return None


def send(ser, cmd, timeout=TIMEOUT):
    """Send a GCode line and block until 'ok' is received."""
    ser.write((cmd.strip() + "\n").encode())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if line.startswith("ok"):
            return line
        if "error" in line.lower():
            print(f"  printer error: {line}", file=sys.stderr)
    raise TimeoutError(f"No 'ok' for: {cmd.strip()}")


def home(ser):
    """Home all axes."""
    print("Homing all axes ...")
    send(ser, "G28", timeout=HOME_TIMEOUT)
    # Lift Z to safe height after homing
    send(ser, f"G1 Z{Z_MIN + Z_MOVE_LEN} F1000")
    send(ser, "M400", timeout=HOME_TIMEOUT)
    print("  homed.")


def generate_xy_moves(n, move_len, bed_x, bed_y, margin):
    """Return list of (x, y) targets and total distance traveled."""
    x, y = bed_x / 2, bed_y / 2
    targets = []
    total_dist = 0.0
    for _ in range(n):
        angle = random.uniform(0, 2 * math.pi)
        nx = x + move_len * math.cos(angle)
        ny = y + move_len * math.sin(angle)
        nx = max(margin, min(bed_x - margin, nx))
        ny = max(margin, min(bed_y - margin, ny))
        dist = math.hypot(nx - x, ny - y)
        if dist < 1.0:
            # too short after clamping, pick opposite direction
            nx = x - move_len * math.cos(angle)
            ny = y - move_len * math.sin(angle)
            nx = max(margin, min(bed_x - margin, nx))
            ny = max(margin, min(bed_y - margin, ny))
            dist = math.hypot(nx - x, ny - y)
        total_dist += dist
        targets.append((nx, ny))
        x, y = nx, ny
    return targets, total_dist


def generate_z_moves(n, move_len, z_min, z_max):
    """Return list of z targets and total distance traveled."""
    z = (z_min + z_max) / 2
    targets = []
    total_dist = 0.0
    for _ in range(n):
        direction = random.choice([-1, 1])
        nz = z + direction * move_len
        nz = max(z_min, min(z_max, nz))
        if abs(nz - z) < 0.5:
            nz = z - direction * move_len
            nz = max(z_min, min(z_max, nz))
        total_dist += abs(nz - z)
        targets.append(nz)
        z = nz
    return targets, total_dist


def motion_timeout(total_dist, speed_mmpmin):
    """Compute a generous timeout for M400 based on distance and speed."""
    motion_secs = (total_dist / speed_mmpmin) * 60 if speed_mmpmin > 0 else 30
    return max(30, motion_secs * 3)  # 3x safety margin, at least 30s


def run_xy_batch(ser, speed, targets, total_dist):
    """Send XY moves at *speed* mm/min, return elapsed seconds."""
    # Move to start and sync
    x0, y0 = targets[0]
    send(ser, f"G1 X{x0:.2f} Y{y0:.2f} F{speed}")
    send(ser, "M400", timeout=motion_timeout(total_dist, speed))
    time.sleep(0.05)

    t0 = time.monotonic()
    for x, y in targets:
        send(ser, f"G1 X{x:.2f} Y{y:.2f} F{speed}")
    send(ser, "M400", timeout=motion_timeout(total_dist, speed))
    return time.monotonic() - t0


def run_z_batch(ser, speed, targets, total_dist):
    """Send Z moves at *speed* mm/min, return elapsed seconds."""
    send(ser, f"G1 Z{targets[0]:.2f} F{speed}")
    send(ser, "M400", timeout=motion_timeout(total_dist, speed))
    time.sleep(0.05)

    t0 = time.monotonic()
    for z in targets:
        send(ser, f"G1 Z{z:.2f} F{speed}")
    send(ser, "M400", timeout=motion_timeout(total_dist, speed))
    return time.monotonic() - t0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Measure actual plotter feed rates across multiple speeds.",
    )
    parser.add_argument("-p", "--port", default=None,
                        help="Serial port (auto-detect if omitted)")
    parser.add_argument("-b", "--baud", type=int, default=BAUD)
    parser.add_argument("--bed-x", type=float, default=BED_X,
                        help=f"Bed X size in mm (default {BED_X})")
    parser.add_argument("--bed-y", type=float, default=BED_Y,
                        help=f"Bed Y size in mm (default {BED_Y})")
    parser.add_argument("--z-max", type=float, default=Z_MAX,
                        help=f"Max Z height in mm (default {Z_MAX})")
    parser.add_argument("--z-min", type=float, default=Z_MIN,
                        help=f"Min Z height in mm (default {Z_MIN})")
    parser.add_argument("--xy-len", type=float, default=XY_MOVE_LEN,
                        help=f"XY move length in mm (default {XY_MOVE_LEN})")
    parser.add_argument("--z-len", type=float, default=Z_MOVE_LEN,
                        help=f"Z move length in mm (default {Z_MOVE_LEN})")
    parser.add_argument("-n", "--moves", type=int, default=MOVES_PER_RUN,
                        help=f"Moves per speed/axis run (default {MOVES_PER_RUN})")
    parser.add_argument("--speeds", type=int, nargs="+", default=SPEEDS,
                        help=f"Feed rates to test in mm/min (default {SPEEDS})")
    parser.add_argument("--skip-z", action="store_true",
                        help="Skip Z-axis tests")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility (default 42)")
    args = parser.parse_args(argv)

    port = args.port or find_port()
    if not port:
        print("No serial port found.", file=sys.stderr)
        sys.exit(1)

    random.seed(args.seed)

    print(f"Connecting to {port} @ {args.baud} baud ...")
    ser = serial.Serial(port, args.baud, timeout=TIMEOUT)
    time.sleep(2)  # wait for Marlin boot
    ser.reset_input_buffer()

    # Drain any startup messages
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        raw = ser.readline()
        if raw:
            print(f"  boot: {raw.decode('utf-8', errors='replace').strip()}")

    # Use absolute positioning
    send(ser, "G90")

    home(ser)

    # Keep Z at a safe height for XY tests
    safe_z = args.z_min + args.z_len
    send(ser, f"G1 Z{safe_z:.2f} F1000")
    send(ser, "M400", timeout=30)

    results = []

    for speed in sorted(args.speeds):
        print(f"\n--- Testing {speed} mm/min ---")

        # -- XY run --
        random.seed(args.seed)  # same pattern each speed for fairness
        xy_targets, xy_dist = generate_xy_moves(
            args.moves, args.xy_len, args.bed_x, args.bed_y, MARGIN)
        xy_elapsed = run_xy_batch(ser, speed, xy_targets, xy_dist)
        xy_mmps = xy_dist / xy_elapsed if xy_elapsed > 0 else 0
        xy_mmpmin = xy_mmps * 60
        print(f"  XY: {xy_dist:.1f} mm in {xy_elapsed:.2f}s "
              f"=> {xy_mmps:.1f} mm/s  ({xy_mmpmin:.0f} mm/min)  "
              f"[commanded {speed} mm/min = {speed/60:.1f} mm/s]")

        z_dist = z_elapsed = z_mmps = z_mmpmin = 0
        if not args.skip_z:
            # Return to center and safe Z before Z test
            send(ser, f"G1 X{args.bed_x/2:.0f} Y{args.bed_y/2:.0f} F{speed}")
            send(ser, f"G1 Z{safe_z:.2f} F1000")
            send(ser, "M400", timeout=60)

            # -- Z run --
            random.seed(args.seed + 1)
            z_targets, z_dist = generate_z_moves(
                args.moves, args.z_len, args.z_min, args.z_max)
            z_elapsed = run_z_batch(ser, speed, z_targets, z_dist)
            z_mmps = z_dist / z_elapsed if z_elapsed > 0 else 0
            z_mmpmin = z_mmps * 60
            print(f"   Z: {z_dist:.1f} mm in {z_elapsed:.2f}s "
                  f"=> {z_mmps:.1f} mm/s  ({z_mmpmin:.0f} mm/min)  "
                  f"[commanded {speed} mm/min = {speed/60:.1f} mm/s]")

        results.append({
            "speed_cmd": speed,
            "xy_dist": xy_dist,
            "xy_time": xy_elapsed,
            "xy_mmps": xy_mmps,
            "xy_mmpmin": xy_mmpmin,
            "z_dist": z_dist,
            "z_time": z_elapsed,
            "z_mmps": z_mmps,
            "z_mmpmin": z_mmpmin,
        })

    # -- final report --------------------------------------------------------
    show_z = not args.skip_z
    sep_len = 78 if show_z else 52
    print("\n" + "=" * sep_len)
    print("SPEED TEST REPORT")
    print("=" * sep_len)
    hdr = f"{'Cmd mm/min':>10}  {'Cmd mm/s':>9}  {'XY mm/s':>8}  {'XY mm/min':>10}  {'XY eff%':>7}"
    if show_z:
        hdr += f"  {'Z mm/s':>7}  {'Z mm/min':>9}  {'Z eff%':>6}"
    print(hdr)
    print("-" * sep_len)
    for r in results:
        xy_eff = (r["xy_mmpmin"] / r["speed_cmd"] * 100) if r["speed_cmd"] else 0
        row = (f"{r['speed_cmd']:>10}  {r['speed_cmd']/60:>9.1f}  "
               f"{r['xy_mmps']:>8.1f}  {r['xy_mmpmin']:>10.0f}  {xy_eff:>6.1f}%")
        if show_z:
            z_eff = (r["z_mmpmin"] / r["speed_cmd"] * 100) if r["speed_cmd"] else 0
            row += f"  {r['z_mmps']:>7.1f}  {r['z_mmpmin']:>9.0f}  {z_eff:>5.1f}%"
        print(row)
    print("=" * sep_len)

    # Averages
    n = len(results)
    avg_xy_eff = sum((r["xy_mmpmin"] / r["speed_cmd"] * 100) for r in results) / n
    print(f"\nAverage XY efficiency: {avg_xy_eff:.1f}%")
    if show_z:
        avg_z_eff = sum((r["z_mmpmin"] / r["speed_cmd"] * 100) for r in results) / n
        print(f"Average  Z efficiency: {avg_z_eff:.1f}%")
    print(f"Moves per run: {args.moves}")
    print(f"XY move length: {args.xy_len} mm" + (f"  |  Z move length: {args.z_len} mm" if show_z else ""))

    # Park
    print("\nParking ...")
    send(ser, "G1 Z40 F1000")
    send(ser, "G1 X0 Y0 F3000")
    send(ser, "M400", timeout=60)
    print("Done.")

    ser.close()


if __name__ == "__main__":
    main()
