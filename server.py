import asyncio
import json
import math
import re
import time
import serial
import serial.tools.list_ports
from contextlib import asynccontextmanager
from io import BytesIO
from xml.etree import ElementTree as ET

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BAUD_RATE = 115200
SERIAL_PORT = "/dev/ttyUSB0"

ser = None
clients: list[WebSocket] = []
command_queue: asyncio.Queue = None
stop_event: asyncio.Event = None
pause_event: asyncio.Event = None  # clear = paused, set = running
ok_event: asyncio.Event = None
direct_lock: asyncio.Lock = None  # serialize direct commands vs queued commands
job_total = 0
job_sent = 0

# Serial throughput tracking
serial_stats = {
    "tx_bytes": 0,          # bytes written since last reset
    "rx_bytes": 0,          # bytes read since last reset
    "window_start": 0.0,    # start of current measurement window
    "cmds_in_window": 0,    # commands completed (ok received) in window
    "rtt_sum": 0.0,         # sum of round-trip times in window
    "last_send_time": 0.0,  # timestamp of last serial write (for RTT)
}


def find_printer_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = (p.description or "").lower()
        if any(kw in desc for kw in ("marlin", "printer", "ch340", "cp210", "arduino", "usb serial", "ft232")):
            return p.device
    if ports:
        return ports[0].device
    return None


async def broadcast(msg_dict):
    text = json.dumps(msg_dict)
    for ws in clients[:]:
        try:
            await ws.send_text(text)
        except Exception:
            if ws in clients:
                clients.remove(ws)


async def broadcast_progress():
    if job_total > 0:
        pct = (job_sent / job_total) * 100
        await broadcast({"type": "progress", "sent": job_sent, "total": job_total, "pct": round(pct, 2)})


async def serial_reader():
    """Single reader for the serial port. Routes 'ok' to unblock command_sender."""
    loop = asyncio.get_event_loop()
    while True:
        if ser and ser.is_open:
            try:
                line = await loop.run_in_executor(None, ser.readline)
                if line:
                    serial_stats["rx_bytes"] += len(line)
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if not text:
                        continue
                    if text.startswith("ok"):
                        ok_event.set()
                        # Track RTT and command completion
                        if serial_stats["last_send_time"] > 0:
                            rtt = time.monotonic() - serial_stats["last_send_time"]
                            serial_stats["rtt_sum"] += rtt
                            serial_stats["cmds_in_window"] += 1
                    if text == "wait":
                        continue
                    await broadcast({"type": "serial", "data": text})
            except Exception as e:
                await broadcast({"type": "error", "data": f"Serial read error: {e}"})
                await asyncio.sleep(1)
        else:
            await asyncio.sleep(1)


async def command_sender():
    """Send queued commands one at a time, waiting for 'ok' from Marlin."""
    global job_sent
    loop = asyncio.get_event_loop()
    while True:
        cmd = await command_queue.get()

        if stop_event.is_set():
            while not command_queue.empty():
                try:
                    command_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            continue

        # Wait if paused
        await pause_event.wait()

        if stop_event.is_set():
            continue

        if ser and ser.is_open:
            async with direct_lock:
                line = cmd.strip() + "\n"
                line_bytes = line.encode("utf-8")
                ok_event.clear()
                try:
                    serial_stats["last_send_time"] = time.monotonic()
                    await loop.run_in_executor(None, ser.write, line_bytes)
                    serial_stats["tx_bytes"] += len(line_bytes)
                    job_sent += 1
                    await broadcast({"type": "sent", "data": cmd.strip()})
                    await broadcast_progress()
                except Exception as e:
                    await broadcast({"type": "error", "data": f"Serial write error: {e}"})
                    continue

                try:
                    await asyncio.wait_for(ok_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    await broadcast({"type": "error", "data": f"Timeout waiting for ok: {cmd.strip()}"})


async def serial_stats_broadcaster():
    """Periodically broadcast serial throughput stats to clients."""
    serial_stats["window_start"] = time.monotonic()
    while True:
        await asyncio.sleep(2)
        if not ser or not ser.is_open or not clients:
            continue

        now = time.monotonic()
        elapsed = now - serial_stats["window_start"]
        if elapsed < 0.1:
            continue

        tx_bytes = serial_stats["tx_bytes"]
        rx_bytes = serial_stats["rx_bytes"]
        total_bytes = tx_bytes + rx_bytes
        cmds = serial_stats["cmds_in_window"]

        # Throughput in bytes/sec
        bps = total_bytes / elapsed
        # Theoretical max: BAUD_RATE / 10 (8N1 = 10 bits per byte)
        max_bps = BAUD_RATE / 10
        saturation = (bps / max_bps * 100) if max_bps > 0 else 0

        # Average RTT
        avg_rtt_ms = (serial_stats["rtt_sum"] / cmds * 1000) if cmds > 0 else 0
        # Commands per second
        cmds_per_sec = cmds / elapsed if elapsed > 0 else 0

        await broadcast({
            "type": "serial_stats",
            "tx_bps": round(tx_bytes / elapsed),
            "rx_bps": round(rx_bytes / elapsed),
            "total_bps": round(bps),
            "max_bps": round(max_bps),
            "saturation_pct": round(saturation, 1),
            "avg_rtt_ms": round(avg_rtt_ms, 1),
            "cmds_per_sec": round(cmds_per_sec, 1),
        })

        # Reset window
        serial_stats["tx_bytes"] = 0
        serial_stats["rx_bytes"] = 0
        serial_stats["cmds_in_window"] = 0
        serial_stats["rtt_sum"] = 0.0
        serial_stats["window_start"] = now


async def do_emergency_stop():
    global ser, job_total, job_sent
    stop_event.set()
    pause_event.set()  # unblock sender so it can drain

    while not command_queue.empty():
        try:
            command_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    job_total = 0
    job_sent = 0
    reset_serial_stats()

    if ser and ser.is_open:
        try:
            ser.reset_output_buffer()
            ser.reset_input_buffer()
        except Exception:
            pass
        try:
            ser.write(b"M112\n")
            await broadcast({"type": "sent", "data": "M112"})
        except Exception:
            pass
        try:
            ser.write(b"M410\n")
            await broadcast({"type": "sent", "data": "M410"})
        except Exception:
            pass

    await broadcast({"type": "stopped", "data": "Emergency stop triggered"})
    await broadcast_progress()


async def send_gcode(cmd):
    """Send a G-code command directly, bypassing the queue.
    Uses direct_lock to prevent interference with the queued command sender."""
    loop = asyncio.get_event_loop()
    async with direct_lock:
        if ser and ser.is_open:
            ok_event.clear()
            try:
                await loop.run_in_executor(None, ser.write, (cmd.strip() + "\n").encode("utf-8"))
                await broadcast({"type": "sent", "data": cmd.strip()})
            except Exception:
                return
            try:
                await asyncio.wait_for(ok_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass


async def do_pause(pen_up_z, z_speed):
    pause_event.clear()
    # Lift pen
    await send_gcode("G90")
    await send_gcode(f"G1 Z{pen_up_z} F{z_speed}")
    await broadcast({"type": "paused"})


async def do_resume(pen_down_z, z_speed):
    # Lower pen
    await send_gcode("G90")
    await send_gcode(f"G1 Z{pen_down_z} F{z_speed}")
    await broadcast({"type": "resumed"})
    pause_event.set()


def reset_serial_stats():
    serial_stats["tx_bytes"] = 0
    serial_stats["rx_bytes"] = 0
    serial_stats["cmds_in_window"] = 0
    serial_stats["rtt_sum"] = 0.0
    serial_stats["last_send_time"] = 0.0
    serial_stats["window_start"] = time.monotonic()


async def do_reset():
    global ser, job_total, job_sent
    stop_event.clear()
    pause_event.set()
    ok_event.clear()
    job_total = 0
    job_sent = 0
    reset_serial_stats()

    port = SERIAL_PORT or find_printer_port()
    if ser:
        try:
            ser.close()
        except Exception:
            pass

    await asyncio.sleep(1)

    if port:
        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=1)
            await broadcast({"type": "status", "connected": True, "port": port})
            await broadcast({"type": "serial", "data": f"Reconnected to {port}"})
        except Exception as e:
            ser = None
            await broadcast({"type": "error", "data": f"Could not reopen {port}: {e}"})
            await broadcast({"type": "status", "connected": False, "port": None})

    await broadcast_progress()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ser, command_queue, stop_event, pause_event, ok_event, direct_lock
    command_queue = asyncio.Queue()
    stop_event = asyncio.Event()
    pause_event = asyncio.Event()
    pause_event.set()  # start unpaused
    ok_event = asyncio.Event()
    direct_lock = asyncio.Lock()

    port = SERIAL_PORT or find_printer_port()
    if port:
        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=1)
            print(f"Connected to {port} at {BAUD_RATE} baud", flush=True)
        except Exception as e:
            print(f"Could not open {port}: {e}", flush=True)
            ser = None
    else:
        print("No serial port found. Running in demo mode.", flush=True)

    reader_task = asyncio.create_task(serial_reader())
    sender_task = asyncio.create_task(command_sender())
    stats_task = asyncio.create_task(serial_stats_broadcaster())
    try:
        yield
    finally:
        reader_task.cancel()
        sender_task.cancel()
        stats_task.cancel()
        for task in (reader_task, sender_task, stats_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        for ws in clients[:]:
            try:
                await ws.close()
            except Exception:
                pass
        clients.clear()
        if ser and ser.is_open:
            try:
                ser.close()
                print("Serial port closed.", flush=True)
            except Exception:
                pass


app = FastAPI(lifespan=lifespan)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)

    port_name = ser.port if ser and ser.is_open else None
    await ws.send_text(json.dumps({
        "type": "status",
        "connected": ser is not None and ser.is_open,
        "port": port_name,
    }))

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "command":
                await command_queue.put(msg["data"].strip())
            elif msg.get("type") == "direct_command":
                # Bypass queue — for use while paused
                await send_gcode(msg["data"].strip())
            elif msg.get("type") == "job_start":
                global job_total, job_sent
                job_total = msg.get("total", 0)
                job_sent = 0
                stop_event.clear()
                pause_event.set()
                await broadcast_progress()
            elif msg.get("type") == "pause":
                await do_pause(msg.get("pen_up_z", 5), msg.get("z_speed", 300))
            elif msg.get("type") == "resume":
                await do_resume(msg.get("pen_down_z", 0), msg.get("z_speed", 300))
            elif msg.get("type") == "emergency_stop":
                await do_emergency_stop()
            elif msg.get("type") == "reset":
                await do_reset()

    except WebSocketDisconnect:
        if ws in clients:
            clients.remove(ws)


@app.get("/api/ports")
async def list_ports():
    ports = serial.tools.list_ports.comports()
    return [{"device": p.device, "description": p.description} for p in ports]


# ── SVG preprocessing ──


def _cubic_bezier_points(x0, y0, cp1x, cp1y, cp2x, cp2y, x1, y1):
    """Interpolate a cubic Bezier curve into a list of (x, y) points (excluding start)."""
    ctrl_len = (math.hypot(cp1x - x0, cp1y - y0) +
                math.hypot(cp2x - cp1x, cp2y - cp1y) +
                math.hypot(x1 - cp2x, y1 - cp2y))
    steps = max(2, int(ctrl_len / 1.0))
    pts = []
    for s in range(1, steps + 1):
        t = s / steps
        mt = 1 - t
        px = mt**3*x0 + 3*mt**2*t*cp1x + 3*mt*t**2*cp2x + t**3*x1
        py = mt**3*y0 + 3*mt**2*t*cp1y + 3*mt*t**2*cp2y + t**3*y1
        pts.append((px, py))
    return pts


def _quadratic_bezier_points(x0, y0, cpx, cpy, x1, y1):
    """Interpolate a quadratic Bezier curve into a list of (x, y) points (excluding start)."""
    ctrl_len = math.hypot(cpx - x0, cpy - y0) + math.hypot(x1 - cpx, y1 - cpy)
    steps = max(2, int(ctrl_len / 1.0))
    pts = []
    for s in range(1, steps + 1):
        t = s / steps
        mt = 1 - t
        px = mt**2*x0 + 2*mt*t*cpx + t**2*x1
        py = mt**2*y0 + 2*mt*t*cpy + t**2*y1
        pts.append((px, py))
    return pts


def _arc_to_points(x1, y1, rx, ry, x_rotation, large_arc, sweep, x2, y2):
    """Convert an SVG arc to interpolated (x, y) points (excluding start).

    Implements the SVG arc endpoint-to-center parametrization from
    https://www.w3.org/TR/SVG/implnote.html#ArcImplementationNotes
    """
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [(x2, y2)]

    rx, ry = abs(rx), abs(ry)
    phi = math.radians(x_rotation)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    # Step 1: Compute (x1', y1')
    dx = (x1 - x2) / 2
    dy = (y1 - y2) / 2
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    # Ensure radii are large enough
    lam = x1p**2 / rx**2 + y1p**2 / ry**2
    if lam > 1:
        s = math.sqrt(lam)
        rx *= s
        ry *= s

    # Step 2: Compute (cx', cy')
    rx2, ry2 = rx**2, ry**2
    x1p2, y1p2 = x1p**2, y1p**2
    num = rx2 * ry2 - rx2 * y1p2 - ry2 * x1p2
    den = rx2 * y1p2 + ry2 * x1p2
    if den == 0:
        return [(x2, y2)]
    sq = math.sqrt(max(0, num / den))
    if large_arc == sweep:
        sq = -sq
    cxp = sq * rx * y1p / ry
    cyp = -sq * ry * x1p / rx

    # Step 3: Compute centre
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2

    # Step 4: Compute theta1 and dtheta
    def _angle(ux, uy, vx, vy):
        n = math.hypot(ux, uy) * math.hypot(vx, vy)
        if n == 0:
            return 0
        c = max(-1, min(1, (ux * vx + uy * vy) / n))
        a = math.acos(c)
        if ux * vy - uy * vx < 0:
            a = -a
        return a

    theta1 = _angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = _angle((x1p - cxp) / rx, (y1p - cyp) / ry,
                     (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dtheta > 0:
        dtheta -= 2 * math.pi
    elif sweep and dtheta < 0:
        dtheta += 2 * math.pi

    arc_len = abs(dtheta) * max(rx, ry)
    steps = max(2, int(arc_len / 1.0))
    pts = []
    for s in range(1, steps + 1):
        t = s / steps
        angle = theta1 + t * dtheta
        ca, sa = math.cos(angle), math.sin(angle)
        px = cos_phi * rx * ca - sin_phi * ry * sa + cx
        py = sin_phi * rx * ca + cos_phi * ry * sa + cy
        pts.append((px, py))
    # Ensure the last point is exactly the target to avoid float drift
    if pts:
        pts[-1] = (x2, y2)
    return pts


def parse_svg_path_to_subpaths(d_attr):
    """Parse an SVG path d attribute into a list of subpaths (list of (x,y) tuples).

    Handles M, L, H, V, C, S, Q, T, A, Z commands (absolute and relative).
    Bezier curves and arcs are interpolated into polyline segments.
    """
    subpaths = []
    current = []
    x, y = 0.0, 0.0
    start_x, start_y = 0.0, 0.0

    # For S/s reflection (last cubic cp2) and T/t reflection (last quad cp)
    last_cubic_cp = None
    last_quad_cp = None
    prev_cmd = None

    # Tokenize: split into commands and their numeric arguments
    tokens = re.findall(r'[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d_attr)

    i = 0
    cmd = 'M'

    def next_num():
        nonlocal i
        while i < len(tokens) and tokens[i] in 'MmLlHhVvCcSsQqTtAaZz':
            i += 1
        if i < len(tokens):
            val = float(tokens[i])
            i += 1
            return val
        return 0.0

    while i < len(tokens):
        t = tokens[i]
        if t in 'MmLlHhVvCcSsQqTtAaZz':
            cmd = t
            i += 1
        # else: implicit repeat of last command

        if cmd == 'M':
            if current and len(current) >= 2:
                subpaths.append(current)
            x, y = next_num(), next_num()
            start_x, start_y = x, y
            current = [(x, y)]
            cmd = 'L'  # subsequent coords are line-to
            last_cubic_cp = last_quad_cp = None
        elif cmd == 'm':
            if current and len(current) >= 2:
                subpaths.append(current)
            x += next_num()
            y += next_num()
            start_x, start_y = x, y
            current = [(x, y)]
            cmd = 'l'
            last_cubic_cp = last_quad_cp = None
        elif cmd == 'L':
            x, y = next_num(), next_num()
            current.append((x, y))
            last_cubic_cp = last_quad_cp = None
        elif cmd == 'l':
            x += next_num()
            y += next_num()
            current.append((x, y))
            last_cubic_cp = last_quad_cp = None
        elif cmd == 'H':
            x = next_num()
            current.append((x, y))
            last_cubic_cp = last_quad_cp = None
        elif cmd == 'h':
            x += next_num()
            current.append((x, y))
            last_cubic_cp = last_quad_cp = None
        elif cmd == 'V':
            y = next_num()
            current.append((x, y))
            last_cubic_cp = last_quad_cp = None
        elif cmd == 'v':
            y += next_num()
            current.append((x, y))
            last_cubic_cp = last_quad_cp = None
        elif cmd == 'C':
            cp1x, cp1y = next_num(), next_num()
            cp2x, cp2y = next_num(), next_num()
            ex, ey = next_num(), next_num()
            current.extend(_cubic_bezier_points(x, y, cp1x, cp1y, cp2x, cp2y, ex, ey))
            last_cubic_cp = (cp2x, cp2y)
            last_quad_cp = None
            x, y = ex, ey
        elif cmd == 'c':
            cp1x, cp1y = x + next_num(), y + next_num()
            cp2x, cp2y = x + next_num(), y + next_num()
            dx, dy = next_num(), next_num()
            ex, ey = x + dx, y + dy
            current.extend(_cubic_bezier_points(x, y, cp1x, cp1y, cp2x, cp2y, ex, ey))
            last_cubic_cp = (cp2x, cp2y)
            last_quad_cp = None
            x, y = ex, ey
        elif cmd == 'S':
            if prev_cmd in ('C', 'c', 'S', 's') and last_cubic_cp:
                cp1x, cp1y = 2 * x - last_cubic_cp[0], 2 * y - last_cubic_cp[1]
            else:
                cp1x, cp1y = x, y
            cp2x, cp2y = next_num(), next_num()
            ex, ey = next_num(), next_num()
            current.extend(_cubic_bezier_points(x, y, cp1x, cp1y, cp2x, cp2y, ex, ey))
            last_cubic_cp = (cp2x, cp2y)
            last_quad_cp = None
            x, y = ex, ey
        elif cmd == 's':
            if prev_cmd in ('C', 'c', 'S', 's') and last_cubic_cp:
                cp1x, cp1y = 2 * x - last_cubic_cp[0], 2 * y - last_cubic_cp[1]
            else:
                cp1x, cp1y = x, y
            cp2x, cp2y = x + next_num(), y + next_num()
            dx, dy = next_num(), next_num()
            ex, ey = x + dx, y + dy
            current.extend(_cubic_bezier_points(x, y, cp1x, cp1y, cp2x, cp2y, ex, ey))
            last_cubic_cp = (cp2x, cp2y)
            last_quad_cp = None
            x, y = ex, ey
        elif cmd == 'Q':
            cpx, cpy = next_num(), next_num()
            ex, ey = next_num(), next_num()
            current.extend(_quadratic_bezier_points(x, y, cpx, cpy, ex, ey))
            last_quad_cp = (cpx, cpy)
            last_cubic_cp = None
            x, y = ex, ey
        elif cmd == 'q':
            cpx, cpy = x + next_num(), y + next_num()
            dx, dy = next_num(), next_num()
            ex, ey = x + dx, y + dy
            current.extend(_quadratic_bezier_points(x, y, cpx, cpy, ex, ey))
            last_quad_cp = (cpx, cpy)
            last_cubic_cp = None
            x, y = ex, ey
        elif cmd == 'T':
            if prev_cmd in ('Q', 'q', 'T', 't') and last_quad_cp:
                cpx, cpy = 2 * x - last_quad_cp[0], 2 * y - last_quad_cp[1]
            else:
                cpx, cpy = x, y
            ex, ey = next_num(), next_num()
            current.extend(_quadratic_bezier_points(x, y, cpx, cpy, ex, ey))
            last_quad_cp = (cpx, cpy)
            last_cubic_cp = None
            x, y = ex, ey
        elif cmd == 't':
            if prev_cmd in ('Q', 'q', 'T', 't') and last_quad_cp:
                cpx, cpy = 2 * x - last_quad_cp[0], 2 * y - last_quad_cp[1]
            else:
                cpx, cpy = x, y
            dx, dy = next_num(), next_num()
            ex, ey = x + dx, y + dy
            current.extend(_quadratic_bezier_points(x, y, cpx, cpy, ex, ey))
            last_quad_cp = (cpx, cpy)
            last_cubic_cp = None
            x, y = ex, ey
        elif cmd == 'A':
            arx, ary = next_num(), next_num()
            x_rot = next_num()
            large = int(next_num())
            sw = int(next_num())
            ex, ey = next_num(), next_num()
            current.extend(_arc_to_points(x, y, arx, ary, x_rot, large, sw, ex, ey))
            last_cubic_cp = last_quad_cp = None
            x, y = ex, ey
        elif cmd == 'a':
            arx, ary = next_num(), next_num()
            x_rot = next_num()
            large = int(next_num())
            sw = int(next_num())
            dx, dy = next_num(), next_num()
            ex, ey = x + dx, y + dy
            current.extend(_arc_to_points(x, y, arx, ary, x_rot, large, sw, ex, ey))
            last_cubic_cp = last_quad_cp = None
            x, y = ex, ey
        elif cmd in ('Z', 'z'):
            if current:
                current.append((start_x, start_y))
                if len(current) >= 2:
                    subpaths.append(current)
                current = []
            x, y = start_x, start_y
            last_cubic_cp = last_quad_cp = None
        else:
            i += 1  # skip unknown

        prev_cmd = cmd

    if current and len(current) >= 2:
        subpaths.append(current)

    return subpaths


def apply_transform(subpaths, transform_str):
    """Apply a simple SVG transform string (translate, scale) to subpaths."""
    if not transform_str:
        return subpaths

    result = subpaths
    # Process transforms right-to-left (inner-most first)
    transforms = re.findall(r'(translate|scale|matrix)\(([^)]+)\)', transform_str)
    for ttype, args in reversed(transforms):
        nums = [float(x) for x in re.findall(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', args)]
        if ttype == 'translate':
            tx = nums[0] if len(nums) > 0 else 0
            ty = nums[1] if len(nums) > 1 else 0
            result = [[(x + tx, y + ty) for x, y in sp] for sp in result]
        elif ttype == 'scale':
            sx = nums[0] if len(nums) > 0 else 1
            sy = nums[1] if len(nums) > 1 else sx
            result = [[(x * sx, y * sy) for x, y in sp] for sp in result]
        elif ttype == 'matrix' and len(nums) == 6:
            a, b, c, d, e, f = nums
            result = [[(a*x + c*y + e, b*x + d*y + f) for x, y in sp] for sp in result]

    return result


def douglas_peucker(points, tolerance):
    """Simplify a polyline using Douglas-Peucker algorithm."""
    if len(points) <= 2 or tolerance <= 0:
        return points

    max_dist = 0
    max_idx = 0
    first, last = points[0], points[-1]
    dx, dy = last[0] - first[0], last[1] - first[1]
    len_sq = dx * dx + dy * dy

    for i in range(1, len(points) - 1):
        px, py = points[i]
        if len_sq == 0:
            dist = math.sqrt((px - first[0])**2 + (py - first[1])**2)
        else:
            t = max(0, min(1, ((px - first[0]) * dx + (py - first[1]) * dy) / len_sq))
            proj_x, proj_y = first[0] + t * dx, first[1] + t * dy
            dist = math.sqrt((px - proj_x)**2 + (py - proj_y)**2)
        if dist > max_dist:
            max_dist = dist
            max_idx = i

    if max_dist > tolerance:
        left = douglas_peucker(points[:max_idx + 1], tolerance)
        right = douglas_peucker(points[max_idx:], tolerance)
        return left[:-1] + right
    return [first, last]


# Common SVG named colors → (R, G, B)
_NAMED_COLORS = {
    "black": (0, 0, 0), "white": (255, 255, 255),
    "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
    "grey": (128, 128, 128), "gray": (128, 128, 128),
    "darkgrey": (169, 169, 169), "darkgray": (169, 169, 169),
    "lightgrey": (211, 211, 211), "lightgray": (211, 211, 211),
    "dimgrey": (105, 105, 105), "dimgray": (105, 105, 105),
    "silver": (192, 192, 192), "orange": (255, 165, 0),
    "purple": (128, 0, 128), "brown": (165, 42, 42),
    "navy": (0, 0, 128), "teal": (0, 128, 128),
    "maroon": (128, 0, 0), "olive": (128, 128, 0),
}


def color_to_brightness(color_str):
    """Convert a CSS color string to perceived brightness (0.0=black, 1.0=white).
    Returns 0.0 for unrecognized colors (treat as black/full density)."""
    color_str = color_str.strip().lower()

    r, g, b = 0, 0, 0

    if color_str.startswith("#"):
        h = color_str[1:]
        if len(h) == 3:
            r, g, b = int(h[0]*2, 16), int(h[1]*2, 16), int(h[2]*2, 16)
        elif len(h) == 6:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        else:
            return 0.0
    elif color_str.startswith("rgb"):
        nums = re.findall(r'[\d.]+', color_str)
        if len(nums) >= 3:
            r, g, b = int(float(nums[0])), int(float(nums[1])), int(float(nums[2]))
    elif color_str in _NAMED_COLORS:
        r, g, b = _NAMED_COLORS[color_str]
    else:
        return 0.0  # unknown color → treat as black

    # Perceived brightness (ITU-R BT.601 luminance)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


# ── Fill pattern generation (server-side equivalent of plotter.js fills) ──


def _is_closed_path(path, epsilon=0.1):
    """Check if a path's first and last points are within epsilon distance."""
    if len(path) < 3:
        return False
    dx = path[0][0] - path[-1][0]
    dy = path[0][1] - path[-1][1]
    return dx * dx + dy * dy <= epsilon * epsilon


def _polygon_area(path):
    """Compute polygon area using the shoelace formula."""
    area = 0
    n = len(path)
    for i in range(n - 1):
        area += path[i][0] * path[i + 1][1] - path[i + 1][0] * path[i][1]
    return abs(area) / 2


def _scanline_intersections(path, y):
    """Find x-coords where horizontal line at y intersects polygon edges."""
    xs = []
    n = len(path)
    for i in range(n - 1):
        ax, ay = path[i]
        bx, by = path[i + 1]
        if ay == by:
            continue
        if y < min(ay, by) or y >= max(ay, by):
            continue
        t = (y - ay) / (by - ay)
        xs.append(ax + t * (bx - ax))
    xs.sort()
    return xs


def _point_in_polygon(x, y, path):
    """Ray-casting point-in-polygon test."""
    inside = False
    n = len(path)
    j = n - 1
    for i in range(n):
        xi, yi = path[i]
        xj, yj = path[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _hatch_polygon(path, spacing, angle_deg=45):
    """Generate hatch fill lines for a closed polygon.

    Returns list of chains, each chain a list of (x, y) tuples forming a
    zigzag hatching pattern.
    """
    if len(path) < 3 or spacing <= 0:
        return []

    angle = math.radians(angle_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    # Rotate polygon so fill lines become horizontal
    rotated = [(px * cos_a + py * sin_a, -px * sin_a + py * cos_a)
               for px, py in path]

    min_y = min(p[1] for p in rotated)
    max_y = max(p[1] for p in rotated)

    lines = []
    y = min_y + spacing * 0.5
    zigzag = False

    while y < max_y:
        xs = _scanline_intersections(rotated, y)
        for j in range(0, len(xs) - 1, 2):
            x1, x2 = xs[j], xs[j + 1]
            if zigzag:
                lines.append([
                    (x2 * cos_a - y * sin_a, x2 * sin_a + y * cos_a),
                    (x1 * cos_a - y * sin_a, x1 * sin_a + y * cos_a),
                ])
            else:
                lines.append([
                    (x1 * cos_a - y * sin_a, x1 * sin_a + y * cos_a),
                    (x2 * cos_a - y * sin_a, x2 * sin_a + y * cos_a),
                ])
        zigzag = not zigzag
        y += spacing

    # Connect adjacent segments into zigzag chains
    if len(lines) <= 1:
        return lines
    chains = [list(lines[0])]
    for j in range(1, len(lines)):
        prev = chains[-1]
        prev_end = prev[-1]
        cur_start = lines[j][0]
        dx = cur_start[0] - prev_end[0]
        dy = cur_start[1] - prev_end[1]
        if dx * dx + dy * dy <= (spacing * 2) ** 2:
            chains[-1].extend(lines[j])
        else:
            chains.append(list(lines[j]))
    return chains


def _dots_polygon(path, spacing, angle_deg=45):
    """Generate dot marks inside a closed polygon."""
    if len(path) < 3 or spacing <= 0:
        return []

    angle = math.radians(angle_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    rotated = [(px * cos_a + py * sin_a, -px * sin_a + py * cos_a)
               for px, py in path]

    min_x = min(p[0] for p in rotated)
    min_y = min(p[1] for p in rotated)
    max_x = max(p[0] for p in rotated)
    max_y = max(p[1] for p in rotated)

    dots = []
    y = min_y + spacing * 0.5
    while y < max_y:
        gx = min_x + spacing * 0.5
        while gx < max_x:
            if _point_in_polygon(gx, y, rotated):
                ox = gx * cos_a - y * sin_a
                oy = gx * sin_a + y * cos_a
                dots.append([(ox, oy), (ox, oy)])
            gx += spacing
        y += spacing
    return dots


def _fill_spacing_for_brightness(min_spacing, max_spacing, brightness, max_brightness=0.95):
    """Compute fill line spacing from colour brightness."""
    if brightness >= max_brightness:
        return 0
    return min_spacing + (max_spacing - min_spacing) * brightness


def generate_fill_paths(paths, filled, mode='hatch', angle_deg=45,
                        min_area=5.0, min_spacing=0.4, max_spacing=5.0,
                        max_brightness=0.95):
    """Generate fill-pattern paths for all eligible closed paths.

    *paths*: list of subpaths (each a list of (x, y) tuples).
    *filled*: parallel list — ``None`` = no fill, float = brightness.
    Returns list of fill paths (each a list of (x, y) tuples).
    """
    if mode == 'none' or not mode:
        return []

    fills = []
    for i, path in enumerate(paths):
        if not _is_closed_path(path):
            continue
        if filled and (i >= len(filled) or filled[i] is None):
            continue
        if min_area > 0 and _polygon_area(path) < min_area:
            continue

        brightness = (filled[i]
                      if (filled and i < len(filled) and filled[i] is not None)
                      else 0.0)
        spacing = _fill_spacing_for_brightness(min_spacing, max_spacing,
                                               brightness, max_brightness)
        if spacing <= 0:
            continue

        if mode == 'dots':
            fills.extend(_dots_polygon(path, spacing, angle_deg))
        else:
            fills.extend(_hatch_polygon(path, spacing, angle_deg))
            if mode == 'crosshatch':
                fills.extend(_hatch_polygon(path, spacing, angle_deg + 90))
    return fills


@app.post("/api/preprocess-svg")
async def preprocess_svg(
    file: UploadFile = File(...),
    simplify: float = Form(0.0),
    fill_mode: str = Form("none"),
    fill_angle: float = Form(45.0),
    fill_min_spacing: float = Form(0.4),
    fill_max_spacing: float = Form(5.0),
    fill_max_brightness: float = Form(0.95),
    fill_min_area: float = Form(5.0),
):
    """Parse SVG server-side, split mega-paths into subpaths, return polylines."""
    content = await file.read()
    try:
        tree = ET.parse(BytesIO(content))
    except Exception as e:
        return JSONResponse({"error": f"SVG parse error: {e}"}, status_code=400)

    root = tree.getroot()

    # Collect all paths with their accumulated transforms and fill brightness
    all_subpaths = []
    all_filled = []  # None = no fill, 0.0 = black (densest), 1.0 = white (no fill)

    def resolve_fill(el, parent_fill):
        """Resolve the effective fill value for an element.
        Returns a brightness float (0.0=black .. 1.0=white) or None for no fill.
        SVG default fill is 'black' (brightness 0.0)."""
        color_str = None
        # Check style attribute first (overrides fill attribute)
        style = el.get('style', '')
        if style:
            m = re.search(r'fill\s*:\s*([^;]+)', style)
            if m:
                color_str = m.group(1).strip().lower()
        # Check fill attribute
        if color_str is None:
            fill_attr = el.get('fill', '')
            if fill_attr:
                color_str = fill_attr.strip().lower()
        if color_str is not None:
            if color_str in ('none', 'transparent'):
                return None
            return color_to_brightness(color_str)
        # Inherit from parent
        return parent_fill

    def walk(el, parent_transform="", parent_fill=0.0):
        tag = el.tag.split('}')[-1]
        el_transform = el.get('transform', '')
        combined = (parent_transform + " " + el_transform).strip()
        el_fill = resolve_fill(el, parent_fill)

        if tag == 'path':
            d = el.get('d', '')
            if d:
                subpaths = parse_svg_path_to_subpaths(d)
                if combined:
                    subpaths = apply_transform(subpaths, combined)
                all_subpaths.extend(subpaths)
                all_filled.extend([el_fill] * len(subpaths))
        elif tag in ('line', 'polyline', 'polygon', 'rect', 'circle', 'ellipse'):
            # Convert simple shapes to points
            pts = []
            if tag == 'line':
                pts = [(float(el.get('x1', 0)), float(el.get('y1', 0))),
                       (float(el.get('x2', 0)), float(el.get('y2', 0)))]
            elif tag == 'rect':
                x, y = float(el.get('x', 0)), float(el.get('y', 0))
                w, h = float(el.get('width', 0)), float(el.get('height', 0))
                pts = [(x, y), (x+w, y), (x+w, y+h), (x, y+h), (x, y)]
            elif tag in ('polyline', 'polygon'):
                raw = el.get('points', '').strip()
                nums = re.findall(r'[-+]?(?:\d+\.?\d*|\.\d+)', raw)
                for j in range(0, len(nums) - 1, 2):
                    pts.append((float(nums[j]), float(nums[j+1])))
                if tag == 'polygon' and pts:
                    pts.append(pts[0])
            elif tag == 'circle':
                cx = float(el.get('cx', 0))
                cy = float(el.get('cy', 0))
                r = float(el.get('r', 0))
                if r > 0:
                    steps = max(12, int(2 * math.pi * r / 0.5))
                    for i in range(steps + 1):
                        angle = 2 * math.pi * i / steps
                        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
            elif tag == 'ellipse':
                cx = float(el.get('cx', 0))
                cy = float(el.get('cy', 0))
                rx = float(el.get('rx', 0))
                ry = float(el.get('ry', 0))
                if rx > 0 and ry > 0:
                    steps = max(12, int(2 * math.pi * max(rx, ry) / 0.5))
                    for i in range(steps + 1):
                        angle = 2 * math.pi * i / steps
                        pts.append((cx + rx * math.cos(angle), cy + ry * math.sin(angle)))

            if len(pts) >= 2:
                sp = [pts]
                if combined:
                    sp = apply_transform(sp, combined)
                all_subpaths.extend(sp)
                all_filled.extend([el_fill] * len(sp))

        for child in el:
            walk(child, combined, el_fill)

    walk(root)

    # Auto-compute tolerance if not provided: 0.1% of the bbox diagonal
    if simplify <= 0:
        min_x0 = min(p[0] for sp in all_subpaths for p in sp) if all_subpaths else 0
        min_y0 = min(p[1] for sp in all_subpaths for p in sp) if all_subpaths else 0
        max_x0 = max(p[0] for sp in all_subpaths for p in sp) if all_subpaths else 0
        max_y0 = max(p[1] for sp in all_subpaths for p in sp) if all_subpaths else 0
        diag = math.sqrt((max_x0 - min_x0)**2 + (max_y0 - min_y0)**2)
        simplify = diag * 0.001  # 0.1% of diagonal

    simplified = []
    simplified_filled = []
    for idx, sp in enumerate(all_subpaths):
        s = douglas_peucker(sp, simplify)
        if len(s) >= 2:
            # Round to 2 decimal places to reduce JSON size
            simplified.append([[round(x, 2), round(y, 2)] for x, y in s])
            simplified_filled.append(all_filled[idx] if idx < len(all_filled) else 0.0)

    # Compute bbox
    min_x = min_y = 0.0
    max_x = max_y = 0.0
    total_pts = 0
    if simplified:
        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')
        for sp in simplified:
            for x, y in sp:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
            total_pts += len(sp)

    # Generate fill patterns if requested
    fill_paths_out = []
    if fill_mode != "none" and simplified:
        tuple_paths = [[(x, y) for x, y in sp] for sp in simplified]
        raw_fills = generate_fill_paths(
            tuple_paths, simplified_filled, mode=fill_mode,
            angle_deg=fill_angle, min_area=fill_min_area,
            min_spacing=fill_min_spacing, max_spacing=fill_max_spacing,
            max_brightness=fill_max_brightness,
        )
        fill_paths_out = [
            [[round(x, 2), round(y, 2)] for x, y in fp]
            for fp in raw_fills
        ]

    return {
        "paths": simplified,
        "filled": simplified_filled,
        "fill_paths": fill_paths_out,
        "bbox": {"minX": min_x, "minY": min_y, "maxX": max_x, "maxY": max_y},
        "stats": {
            "original_paths": len(all_subpaths),
            "simplified_paths": len(simplified),
            "total_points": total_pts,
            "fill_paths": len(fill_paths_out),
        }
    }


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["."],
        reload_includes=["*.py", "*.html", "*.js", "*.css"],
    )
