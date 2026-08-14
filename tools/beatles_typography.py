#!/usr/bin/env python3
"""
Beatles Lyrics Wandering Ribbon Generator.

Key phrases from each Beatles song flow as one continuous line of text
that wanders organically from the center of the page outward.
Uses the Relief SingleLine font for plotter-compatible single-stroke output.

Usage:
    python tools/beatles_typography.py [options]

Examples:
    # Default
    python tools/beatles_typography.py

    # Smaller text, more lines per song, tighter curves
    python tools/beatles_typography.py --font-size 6 --lines 12 --curviness 1.5

    # Big text, loose wandering, specific seed for reproducibility
    python tools/beatles_typography.py --font-size 12 --curviness 0.5 --straighten 2.0 --seed 42

    # Quick iteration with PNG preview
    python tools/beatles_typography.py --font-size 7 --lines 10 --png
"""

import bisect
import json
import math
import random
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
FONT_PATH = SCRIPT_DIR / "fonts" / "ReliefSingleLineSVG-Regular.svg"
LYRICS_CACHE = SCRIPT_DIR / ".beatles_lyrics_cache.json"


# --------------- SVG path utilities ---------------

_TOKEN_RE = re.compile(r"[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")

def _offset_path(d, dx):
    if not dx:
        return d
    def _repl(m):
        return f"M{float(m.group(1)) + dx:g} {m.group(2)}"
    return re.sub(r"M(-?[\d.]+)\s+(-?[\d.]+)", _repl, d)

def _transform_path_rotated(d, ox, oy, scale, angle):
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    def tf(fx, fy):
        return (fx*scale*cos_a + fy*scale*sin_a,
                fx*scale*sin_a - fy*scale*cos_a)
    tokens = _TOKEN_RE.findall(d)
    out = []
    i, n = 0, len(tokens)
    num = lambda idx: float(tokens[idx])
    while i < n:
        t = tokens[i]
        if t == "M":
            r = tf(num(i+1), num(i+2))
            out.append(f"M{ox+r[0]:.2f} {oy+r[1]:.2f}"); i += 3
        elif t == "h":
            r = tf(num(i+1), 0)
            out.append(f"l{r[0]:.2f} {r[1]:.2f}"); i += 2
        elif t == "v":
            r = tf(0, num(i+1))
            out.append(f"l{r[0]:.2f} {r[1]:.2f}"); i += 2
        elif t == "l":
            r = tf(num(i+1), num(i+2))
            out.append(f"l{r[0]:.2f} {r[1]:.2f}"); i += 3
        elif t == "c":
            r1, r2, r3 = tf(num(i+1),num(i+2)), tf(num(i+3),num(i+4)), tf(num(i+5),num(i+6))
            out.append(f"c{r1[0]:.2f} {r1[1]:.2f} {r2[0]:.2f} {r2[1]:.2f} {r3[0]:.2f} {r3[1]:.2f}"); i += 7
        elif t == "s":
            r1, r2 = tf(num(i+1), num(i+2)), tf(num(i+3), num(i+4))
            out.append(f"s{r1[0]:.2f} {r1[1]:.2f} {r2[0]:.2f} {r2[1]:.2f}"); i += 5
        elif t in ("z", "Z"):
            out.append("z"); i += 1
        else:
            i += 1
    return "".join(out)


# --------------- Font ---------------

class SingleLineFont:
    def __init__(self, path):
        self.glyphs = {}
        self.units_per_em = 1000
        self.ascent = 800
        self.descent = -200
        self.default_adv = 280
        self._parse(path)

    def _parse(self, path):
        tree = ET.parse(path)
        for elem in tree.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "font":
                self.default_adv = int(elem.get("horiz-adv-x", self.default_adv))
            elif tag == "font-face":
                self.units_per_em = int(elem.get("units-per-em", self.units_per_em))
                self.ascent = int(elem.get("ascent", self.ascent))
                self.descent = int(elem.get("descent", self.descent))
            elif tag == "glyph":
                uc = elem.get("unicode")
                if uc is not None:
                    adv = int(elem.get("horiz-adv-x", self.default_adv))
                    self.glyphs[uc] = (adv, elem.get("d"))

    def has(self, ch):
        return ch in self.glyphs

    def char_advance(self, ch, font_size):
        adv = self.glyphs[ch][0] if ch in self.glyphs else self.default_adv
        return adv * font_size / self.units_per_em

    def render_char(self, ch, ox, oy, font_size, angle=0):
        if ch not in self.glyphs:
            return ""
        _, d = self.glyphs[ch]
        if not d:
            return ""
        return _transform_path_rotated(d, ox, oy, font_size / self.units_per_em, angle)


# --------------- Lyrics ---------------

def fetch_lyrics():
    if LYRICS_CACHE.exists():
        return json.loads(LYRICS_CACHE.read_text())
    print("  Fetching from GitHub...", file=sys.stderr)
    url = "https://api.github.com/repos/tylerlewiscook/beatles-lyrics/git/trees/master?recursive=1"
    req = urllib.request.Request(url, headers={"User-Agent": "PlotterTool/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        tree = json.loads(r.read())
    txt_paths = sorted(
        e["path"] for e in tree["tree"]
        if e["path"].startswith("lyrics/") and e["path"].endswith(".txt")
    )
    texts = []
    for i, p in enumerate(txt_paths):
        raw = ("https://raw.githubusercontent.com/tylerlewiscook/beatles-lyrics"
               f"/master/{urllib.request.quote(p)}")
        try:
            req = urllib.request.Request(raw, headers={"User-Agent": "PlotterTool/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                texts.append(r.read().decode("utf-8", errors="replace"))
        except Exception as e:
            print(f"    skip {p}: {e}", file=sys.stderr)
        if (i + 1) % 30 == 0:
            print(f"    {i+1}/{len(txt_paths)}...", file=sys.stderr)
    LYRICS_CACHE.write_text(json.dumps(texts))
    return texts


def extract_key_phrases(lyrics_texts, max_lines_per_song=8):
    """Extract opening lines, repeated phrases, and key verses from each song."""
    all_phrases = []
    for text in lyrics_texts:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            continue
        opening = lines[:3]
        counts = Counter(lines)
        repeated = [line for line, c in counts.most_common(5)
                    if c > 1 and line not in opening]
        used = set(opening + repeated)
        extras = [l for l in lines if l not in used]
        chosen = opening + repeated[:3] + extras[:max(0, max_lines_per_song - 6)]
        all_phrases.extend(chosen[:max_lines_per_song])
    return " ".join(all_phrases)


# --------------- Wandering path generation ---------------

def _norm_angle(a):
    while a > math.pi:  a -= 2 * math.pi
    while a < -math.pi: a += 2 * math.pi
    return a


def generate_wandering_path(cfg):
    """Generate a wandering path from the center outward.

    cfg is a dict with all tuning parameters.
    """
    font_size = cfg["font_size"]
    canvas    = cfg["canvas"]
    margin    = cfg["margin"]
    curviness   = cfg["curviness"]
    straighten  = cfg["straighten"]
    avoidance   = cfg["avoidance"]
    edge_soft   = cfg["edge_soft"]
    edge_hard   = cfg["edge_hard"]
    max_turn    = cfg["max_turn"]
    smoothing   = cfg["smoothing"]
    seed        = cfg["seed"]

    rng = random.Random(seed)
    # Random phase offsets so each seed produces a unique path
    p1, p2, p3 = rng.uniform(0, 10), rng.uniform(0, 10), rng.uniform(0, 10)
    init_dir = rng.uniform(0, 2 * math.pi)

    cell = font_size * 1.0
    step = 1.0
    min_arc_sep = cell * 12
    max_steps = 400000

    grid = {}  # (cx,cy) -> arc_len of first visit

    x, y = canvas / 2.0, canvas / 2.0
    direction = init_dir
    smooth_k = 0.0
    arc = 0.0
    points = []

    for step_i in range(max_steps):
        points.append((x, y, direction))
        gx, gy = int(x / cell), int(y / cell)
        if (gx, gy) not in grid:
            grid[(gx, gy)] = arc

        k = 0.0

        # --- 1. Organic noise (meandering) ---
        k += curviness * (
            0.040 * math.sin(arc * 0.003 + p1)
          + 0.025 * math.sin(arc * 0.008 + p2)
          + 0.015 * math.sin(arc * 0.019 + p3))

        # --- 2. Straightening gravity ---
        k -= smooth_k * straighten

        # --- 3. Edge avoidance ---
        emergency = 0.0
        fx, fy = 0.0, 0.0
        for edge, sign, is_x in [
            (margin, 1, True), (canvas - margin, -1, True),
            (margin, 1, False), (canvas - margin, -1, False),
        ]:
            d = abs((x if is_x else y) - edge)
            if d < edge_soft:
                force = sign * 2.5 * ((1 - d / edge_soft) ** 2)
                if is_x: fx += force
                else:    fy += force
            if d < edge_hard:
                edir = (0 if sign > 0 else math.pi) if is_x \
                       else (math.pi/2 if sign > 0 else -math.pi/2)
                emergency += _norm_angle(edir - direction) * 0.5 * (1 - d/edge_hard)
        if fx or fy:
            k += _norm_angle(math.atan2(fy, fx) - direction) * 0.06

        # --- 4. Self-avoidance ---
        sa = 0.0
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                if dx == 0 and dy == 0:
                    continue
                key = (gx + dx, gy + dy)
                if key not in grid:
                    continue
                if arc - grid[key] < min_arc_sep:
                    continue
                cell_cx = (gx + dx + 0.5) * cell
                cell_cy = (gy + dy + 0.5) * cell
                away = math.atan2(y - cell_cy, x - cell_cx)
                dist = max(cell * 0.5, math.sqrt((dx*cell)**2 + (dy*cell)**2))
                sa += _norm_angle(away - direction) * avoidance * (cell / dist) ** 2
        k += sa

        # --- 5. Density steering (every 4 steps) ---
        if step_i % 4 == 0:
            best_delta, best_empty = 0.0, -1
            look = int(font_size * 6 / cell)
            for probe in range(-4, 5):
                ta = direction + probe * 0.25
                ca, sa2 = math.cos(ta), math.sin(ta)
                empty = sum(
                    1 for d in range(1, look + 1)
                    if (int((x + ca*d*cell)/cell),
                        int((y + sa2*d*cell)/cell)) not in grid
                )
                if empty > best_empty:
                    best_empty = empty
                    best_delta = probe * 0.25
            if best_empty == 0:
                print(f"  Page full at step {step_i}", file=sys.stderr)
                break
            k += best_delta * 0.06

        # --- Smooth and limit curvature ---
        smooth_k = smooth_k * smoothing + k * (1 - smoothing)
        clamped = max(-max_turn, min(max_turn, smooth_k + emergency))

        # Last-resort nudge if about to enter occupied cell
        test_dir = direction + clamped
        nx = x + math.cos(test_dir) * step * 3
        ny = y + math.sin(test_dir) * step * 3
        ngx, ngy = int(nx / cell), int(ny / cell)
        if (ngx, ngy) in grid and arc - grid[(ngx, ngy)] > min_arc_sep:
            clamped += 0.04 * (1 if smooth_k >= 0 else -1)

        direction += clamped
        x += math.cos(direction) * step
        y += math.sin(direction) * step
        arc += step

        # Bounce off edges
        if x < margin or x > canvas - margin or y < margin or y > canvas - margin:
            x = max(margin + 1, min(canvas - margin - 1, x))
            y = max(margin + 1, min(canvas - margin - 1, y))
            direction = math.atan2(canvas/2 - y, canvas/2 - x) + rng.uniform(-0.5, 0.5)
            smooth_k = 0.0

    return points


# --------------- Place text on path ---------------

def compute_arclengths(pts):
    s = [0.0]
    for i in range(1, len(pts)):
        dx = pts[i][0] - pts[i-1][0]
        dy = pts[i][1] - pts[i-1][1]
        s.append(s[-1] + math.sqrt(dx*dx + dy*dy))
    return s

def sample_path(pts, arcs, s):
    if s <= 0: return pts[0]
    if s >= arcs[-1]: return pts[-1]
    idx = bisect.bisect_right(arcs, s) - 1
    idx = max(0, min(idx, len(pts) - 2))
    seg = arcs[idx+1] - arcs[idx]
    if seg < 1e-6: return pts[idx]
    t = (s - arcs[idx]) / seg
    x = pts[idx][0] + t * (pts[idx+1][0] - pts[idx][0])
    y = pts[idx][1] + t * (pts[idx+1][1] - pts[idx][1])
    a0, a1 = pts[idx][2], pts[idx+1][2]
    da = a1 - a0
    if da > math.pi: da -= 2*math.pi
    if da < -math.pi: da += 2*math.pi
    return x, y, a0 + t * da


def generate_svg(font, text, cfg):
    font_size = cfg["font_size"]
    phys_mm   = cfg["phys_mm"]
    canvas    = cfg["canvas"]
    stroke_w  = cfg["stroke_w"]

    print("  Building wandering path...", file=sys.stderr)
    pts = generate_wandering_path(cfg)
    print(f"  Path: {len(pts)} samples", file=sys.stderr)

    arcs = compute_arclengths(pts)
    total_len = arcs[-1]

    paths = []
    s = 0.0
    chars_placed = 0

    for ch in text:
        if s >= total_len:
            break
        adv = font.char_advance(ch, font_size) if font.has(ch) \
              else font.default_adv * font_size / font.units_per_em
        x, y, angle = sample_path(pts, arcs, s)
        d = font.render_char(ch, x, y, font_size, angle)
        if d:
            paths.append(f'  <path d="{d}"/>')
        chars_placed += 1
        s += adv

    words_placed = text[:chars_placed].count(" ")
    pct = 100 * len(grid_cells_used(pts, font_size)) / ((canvas // int(font_size)) ** 2)
    print(f"  Placed {chars_placed} chars (~{words_placed} words), "
          f"path {total_len:.0f}u, ~{pct:.0f}% filled", file=sys.stderr)

    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg"',
        f'     viewBox="0 0 {canvas} {canvas}"',
        f'     width="{phys_mm}mm" height="{phys_mm}mm">',
        "  <style>",
        f"    path {{ stroke: black; fill: none; stroke-width: {stroke_w};",
        "           stroke-linecap: round; stroke-linejoin: round; }",
        "  </style>",
        f'  <rect width="{canvas}" height="{canvas}" fill="white"/>',
        *paths,
        "</svg>",
    ])


def grid_cells_used(pts, font_size):
    """Count unique grid cells visited."""
    cells = set()
    for x, y, _ in pts:
        cells.add((int(x / font_size), int(y / font_size)))
    return cells


# --------------- Main ---------------

def main():
    import argparse

    p = argparse.ArgumentParser(
        description="Beatles lyrics wandering ribbon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Tuning guide:
  --font-size   Smaller = more text fits, denser. Larger = more readable.
  --lines       Lines extracted per song. More = more text to place.
  --curviness   Amplitude of meandering. 0.5=gentle, 1.0=default, 2.0=wild.
  --straighten  Pull-back to straight. Higher=straighter runs, fewer loops.
  --avoidance   Self-repulsion strength. Higher=less overlap, more deflection.
  --max-turn    Max curvature per step. Higher=tighter turns allowed.
  --smoothing   0-1, higher=smoother/slower direction changes.
  --edge-soft   Distance at which soft edge steering begins.
  --edge-hard   Distance at which emergency edge steering kicks in.
  --seed        Random seed for reproducible output.
  --margin      Border margin in SVG units.
  --stroke-w    Stroke width for preview (pen width on plotter).
  --png         Also render a PNG preview.
""")
    p.add_argument("-o", "--output", default="artwork/beatles_typography.svg")
    p.add_argument("--font-size", type=float, default=8, help="Font size (default: 8)")
    p.add_argument("--lines", type=int, default=8, help="Max lines per song (default: 8)")
    p.add_argument("--curviness", type=float, default=1.0, help="Meandering amplitude (default: 1.0)")
    p.add_argument("--straighten", type=float, default=1.2, help="Straightening force (default: 1.2)")
    p.add_argument("--avoidance", type=float, default=0.25, help="Self-avoidance strength (default: 0.25)")
    p.add_argument("--max-turn", type=float, default=0.05, help="Max curvature/step (default: 0.05)")
    p.add_argument("--smoothing", type=float, default=0.90, help="Curvature smoothing 0-1 (default: 0.90)")
    p.add_argument("--edge-soft", type=float, default=150, help="Soft edge zone (default: 150)")
    p.add_argument("--edge-hard", type=float, default=25, help="Hard edge zone (default: 25)")
    p.add_argument("--seed", type=int, default=None, help="Random seed (default: random)")
    p.add_argument("--margin", type=float, default=8, help="Border margin (default: 8)")
    p.add_argument("--stroke-w", type=float, default=1.0, help="Stroke width (default: 1.0)")
    p.add_argument("--size", type=int, default=200, help="Physical size mm (default: 200)")
    p.add_argument("--png", action="store_true", help="Also render PNG preview")
    args = p.parse_args()

    if args.seed is None:
        args.seed = random.randint(0, 999999)

    cfg = {
        "font_size":  args.font_size,
        "canvas":     1000,
        "margin":     args.margin,
        "curviness":  args.curviness,
        "straighten": args.straighten,
        "avoidance":  args.avoidance,
        "max_turn":   args.max_turn,
        "smoothing":  args.smoothing,
        "edge_soft":  args.edge_soft,
        "edge_hard":  args.edge_hard,
        "seed":       args.seed,
        "phys_mm":    args.size,
        "stroke_w":   args.stroke_w,
    }

    print("Beatles Lyrics Wandering Ribbon", file=sys.stderr)
    print(f"  seed={args.seed}  font={args.font_size}  lines={args.lines}  "
          f"curv={args.curviness}  str={args.straighten}  avoid={args.avoidance}  "
          f"turn={args.max_turn}  smooth={args.smoothing}", file=sys.stderr)

    font = SingleLineFont(FONT_PATH)

    lyrics = fetch_lyrics()
    text = extract_key_phrases(lyrics, args.lines)
    print(f"  {len(text)} chars from {len(lyrics)} songs ({args.lines} lines/song)",
          file=sys.stderr)

    svg = generate_svg(font, text, cfg)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg)
    print(f"  Output: {out} ({len(svg) // 1024} KB)  seed={args.seed}", file=sys.stderr)

    if args.png:
        try:
            import cairosvg
            png_path = out.with_suffix(".png")
            cairosvg.svg2png(url=str(out), write_to=str(png_path),
                             output_width=3000, output_height=3000)
            print(f"  Preview: {png_path}", file=sys.stderr)
        except ImportError:
            print("  (install cairosvg for --png preview)", file=sys.stderr)


if __name__ == "__main__":
    main()
