#!/usr/bin/env python3
"""Generate SVG calibration grids for testing shading/fill patterns.

Produces a grid where rows are pattern types (hatch, crosshatch, dots, circles)
and columns are density levels. Each cell is a square of configurable size.
"""
import argparse
import math
import sys
import xml.etree.ElementTree as ET


PATTERNS = ["hatch", "crosshatch", "dots", "circles"]
DEFAULT_DENSITIES = [5, 10, 20, 35, 50]
DEFAULT_CELL_MM = 20  # 2 cm
DEFAULT_GAP_MM = 3
LABEL_HEIGHT_MM = 6
MARGIN_MM = 5
ROW_LABEL_WIDTH_MM = 18

# Single-stroke font: each glyph is a list of strokes, each stroke a list of
# (x, y) points on a 0-1 unit grid (width x height).  None separates pen-up
# moves within a glyph.
_GLYPH_DATA = {
    "0": [[(0,0),(1,0),(1,1),(0,1),(0,0)]],
    "1": [[(0.5,0),(0.5,1)]],
    "2": [[(0,0),(1,0),(1,0.5),(0,0.5),(0,1),(1,1)]],
    "3": [[(0,0),(1,0),(1,0.5),(0,0.5)], [(1,0.5),(1,1),(0,1)]],
    "4": [[(0,0),(0,0.5),(1,0.5)], [(1,0),(1,1)]],
    "5": [[(1,0),(0,0),(0,0.5),(1,0.5),(1,1),(0,1)]],
    "6": [[(1,0),(0,0),(0,1),(1,1),(1,0.5),(0,0.5)]],
    "7": [[(0,0),(1,0),(1,1)]],
    "8": [[(0,0),(1,0),(1,1),(0,1),(0,0)], [(0,0.5),(1,0.5)]],
    "9": [[(1,0.5),(0,0.5),(0,0),(1,0),(1,1),(0,1)]],
    "%": [[(0,1),(1,0)], [(0.1,0),(0.3,0),(0.3,0.3),(0.1,0.3),(0.1,0)],
          [(0.7,0.7),(0.9,0.7),(0.9,1),(0.7,1),(0.7,0.7)]],
    " ": [],
    "a": [[(1,0.4),(0,0.4),(0,1),(1,1),(1,0.4),(1,1)]],
    "b": [[(0,0),(0,1),(1,1),(1,0.4),(0,0.4)]],
    "c": [[(1,0.4),(0,0.4),(0,1),(1,1)]],
    "d": [[(1,0),(1,1),(0,1),(0,0.4),(1,0.4)]],
    "e": [[(0,0.7),(1,0.7),(1,0.4),(0,0.4),(0,1),(1,1)]],
    "h": [[(0,0),(0,1)], [(0,0.4),(1,0.4),(1,1)]],
    "i": [[(0.5,0.4),(0.5,1)]],
    "k": [[(0,0),(0,1)], [(1,0.4),(0,0.7),(1,1)]],
    "l": [[(0.5,0),(0.5,1)]],
    "o": [[(0,0.4),(1,0.4),(1,1),(0,1),(0,0.4)]],
    "r": [[(0,1),(0,0.4),(1,0.4)]],
    "s": [[(1,0.4),(0,0.4),(0,0.7),(1,0.7),(1,1),(0,1)]],
    "t": [[(0.5,0),(0.5,1)], [(0,0.4),(1,0.4)]],
}


def _stroke_text(svg, tag, text, x, y, height, stroke_width, anchor="start"):
    """Render text as stroked polylines at (x, y) baseline position.

    anchor: "start" (left), "middle" (center), "end" (right).
    """
    char_h = height
    char_w = height * 0.6
    spacing = height * 0.15
    total_w = len(text) * char_w + max(0, len(text) - 1) * spacing

    if anchor == "middle":
        start_x = x - total_w / 2
    elif anchor == "end":
        start_x = x - total_w
    else:
        start_x = x

    for i, ch in enumerate(text):
        cx = start_x + i * (char_w + spacing)
        cy = y - char_h  # y is baseline, glyphs draw downward from top
        strokes = _GLYPH_DATA.get(ch, [])
        for stroke in strokes:
            if len(stroke) < 2:
                continue
            points = " ".join(f"{cx + px * char_w:.2f},{cy + py * char_h:.2f}"
                              for px, py in stroke)
            ET.SubElement(svg, tag("polyline"), {
                "points": points,
                "fill": "none",
                "stroke": "black",
                "stroke-width": str(stroke_width),
            })


def _hatch_lines(x, y, size, density, angle=45, stroke_width=0.3):
    """Generate diagonal hatch lines inside a square.

    density: 0-100, percentage of area covered by ink.
    Spacing is derived from stroke_width so that at density D%,
    the ink strips cover D% of the area (spacing = stroke_width * 100 / density).
    """
    spacing = stroke_width * 100.0 / max(density, 1)
    lines = []
    # Sweep a perpendicular offset across the diagonal
    diag = size * math.sqrt(2)
    cos_a = math.cos(math.radians(angle))
    sin_a = math.sin(math.radians(angle))
    cx, cy = x + size / 2, y + size / 2
    offset = -diag / 2
    while offset <= diag / 2:
        # Line perpendicular to angle, clipped to square
        # Parametric line: (cx + offset*cos(a+90) + t*cos(a), cy + offset*sin(a+90) + t*sin(a))
        nx = -sin_a  # normal direction (perpendicular to hatch)
        ny = cos_a
        ox = cx + offset * nx
        oy = cy + offset * ny
        # Find intersections with square edges
        pts = _clip_line_to_rect(ox, oy, cos_a, sin_a, x, y, size)
        if pts:
            lines.append(pts)
        offset += spacing
    return lines


def _clip_line_to_rect(ox, oy, dx, dy, rx, ry, size):
    """Clip an infinite line (ox,oy)+t*(dx,dy) to rectangle [rx,rx+size]x[ry,ry+size].

    Returns ((x1,y1),(x2,y2)) or None.
    """
    t_min = -1e9
    t_max = 1e9
    for axis_d, axis_o, lo, hi in [
        (dx, ox, rx, rx + size),
        (dy, oy, ry, ry + size),
    ]:
        if abs(axis_d) < 1e-12:
            if axis_o < lo or axis_o > hi:
                return None
        else:
            t1 = (lo - axis_o) / axis_d
            t2 = (hi - axis_o) / axis_d
            if t1 > t2:
                t1, t2 = t2, t1
            t_min = max(t_min, t1)
            t_max = min(t_max, t2)
    if t_min > t_max:
        return None
    return ((ox + t_min * dx, oy + t_min * dy), (ox + t_max * dx, oy + t_max * dy))


def _dot_marks(x, y, size, density, stroke_width=0.3):
    """Generate ((x1,y1),(x2,y2)) tiny line segments for dot fill.

    Each dot is a short pen hop (length = stroke_width) so the plotter
    can stamp dots without full pen-up/down cycles per dot.  Density
    controls spacing the same way as hatch lines.
    """
    radius = stroke_width
    spacing = radius * math.sqrt(math.pi * 100.0 / max(density, 1))
    half = stroke_width * 0.5
    marks = []
    py = y + spacing / 2
    while py < y + size:
        px = x + spacing / 2
        while px < x + size:
            marks.append(((px - half, py), (px + half, py)))
            px += spacing
        py += spacing
    return marks


def _circle_arcs(x, y, size, density, stroke_width=0.3):
    """Generate polyline point lists for concentric circles clipped to cell.

    Circles are spaced from the center outward using the same density model
    as hatch (spacing = stroke_width * 100 / density).  Circles that extend
    beyond the cell are clipped to its boundary.
    """
    cx, cy = x + size / 2, y + size / 2
    spacing = stroke_width * 100.0 / max(density, 1)
    max_r = size * math.sqrt(2) / 2  # corner distance
    arcs = []
    r = spacing
    while r <= max_r:
        arc = _clip_circle_to_rect(cx, cy, r, x, y, size)
        arcs.extend(arc)
        r += spacing
    return arcs


def _clip_circle_to_rect(cx, cy, r, rx, ry, size):
    """Clip a circle to a rectangle, returning list of polyline point lists."""
    steps = max(24, int(2 * math.pi * r / 0.3))
    points = []
    current_arc = []
    for i in range(steps + 1):
        angle = 2 * math.pi * i / steps
        px = cx + r * math.cos(angle)
        py = cy + r * math.sin(angle)
        inside = rx <= px <= rx + size and ry <= py <= ry + size
        if inside:
            if not current_arc:
                # If entering from outside, add the boundary crossing point
                if i > 0:
                    prev_angle = 2 * math.pi * (i - 1) / steps
                    ppx = cx + r * math.cos(prev_angle)
                    ppy = cy + r * math.sin(prev_angle)
                    edge_pt = _intersect_segment_rect(ppx, ppy, px, py, rx, ry, size)
                    if edge_pt:
                        current_arc.append(edge_pt)
            current_arc.append((px, py))
        else:
            if current_arc:
                # Exiting: add boundary crossing point
                prev_angle = 2 * math.pi * (i - 1) / steps
                ppx = cx + r * math.cos(prev_angle)
                ppy = cy + r * math.sin(prev_angle)
                edge_pt = _intersect_segment_rect(ppx, ppy, px, py, rx, ry, size)
                if edge_pt:
                    current_arc.append(edge_pt)
                points.append(current_arc)
                current_arc = []
    if current_arc:
        # If circle is fully inside, or last arc wraps around
        if points and len(current_arc) > 0:
            # Check if we can merge with the first arc (wrap-around)
            points[0] = current_arc + points[0]
        else:
            points.append(current_arc)
    return points


def _intersect_segment_rect(x1, y1, x2, y2, rx, ry, size):
    """Find the point where segment (x1,y1)-(x2,y2) crosses the rect boundary."""
    dx, dy = x2 - x1, y2 - y1
    best_t = None
    for edge_val, axis_is_x in [
        (rx, True), (rx + size, True), (ry, False), (ry + size, False),
    ]:
        d = dx if axis_is_x else dy
        o = x1 if axis_is_x else y1
        if abs(d) < 1e-12:
            continue
        t = (edge_val - o) / d
        if 0 <= t <= 1:
            ix = x1 + t * dx
            iy = y1 + t * dy
            if rx - 1e-9 <= ix <= rx + size + 1e-9 and ry - 1e-9 <= iy <= ry + size + 1e-9:
                if best_t is None or t < best_t:
                    best_t = t
    if best_t is not None:
        return (x1 + best_t * dx, y1 + best_t * dy)
    return None


def generate_calibration_svg(
    cell_size=DEFAULT_CELL_MM,
    densities=None,
    patterns=None,
    stroke_width=0.5,
    gap=DEFAULT_GAP_MM,
):
    """Build and return an SVG ElementTree for the calibration grid."""
    if densities is None:
        densities = list(DEFAULT_DENSITIES)
    if patterns is None:
        patterns = list(PATTERNS)

    cols = len(densities)
    rows = len(patterns)

    grid_w = cols * cell_size + (cols - 1) * gap
    grid_h = rows * cell_size + (rows - 1) * gap
    total_w = MARGIN_MM + ROW_LABEL_WIDTH_MM + grid_w + MARGIN_MM
    total_h = MARGIN_MM + LABEL_HEIGHT_MM + grid_h + MARGIN_MM

    NS = "http://www.w3.org/2000/svg"
    ET.register_namespace("", NS)

    def tag(name):
        return f"{{{NS}}}{name}"

    svg = ET.Element(tag("svg"), {
        "width": f"{total_w}mm",
        "height": f"{total_h}mm",
        "viewBox": f"0 0 {total_w} {total_h}",
    })

    label_h = 3  # label character height in mm
    label_sw = str(stroke_width * 0.6)

    # Column headers (density labels)
    for ci, d in enumerate(densities):
        tx = MARGIN_MM + ROW_LABEL_WIDTH_MM + ci * (cell_size + gap) + cell_size / 2
        ty = MARGIN_MM + LABEL_HEIGHT_MM - 1
        _stroke_text(svg, tag, f"{d}%", tx, ty, label_h, label_sw, anchor="middle")

    # Row headers + cells
    for ri, pat in enumerate(patterns):
        ry = MARGIN_MM + LABEL_HEIGHT_MM + ri * (cell_size + gap)
        # Row label
        tx = MARGIN_MM + ROW_LABEL_WIDTH_MM - 2
        ty = ry + cell_size / 2 + label_h / 2
        _stroke_text(svg, tag, pat, tx, ty, label_h, label_sw, anchor="end")

        for ci, density in enumerate(densities):
            cx = MARGIN_MM + ROW_LABEL_WIDTH_MM + ci * (cell_size + gap)
            # Cell border
            ET.SubElement(svg, tag("rect"), {
                "x": str(cx), "y": str(ry),
                "width": str(cell_size), "height": str(cell_size),
                "fill": "none", "stroke": "black",
                "stroke-width": str(stroke_width),
            })
            # Fill pattern
            _render_pattern(svg, tag, pat, cx, ry, cell_size, density, stroke_width)

    ET.indent(svg)
    return ET.ElementTree(svg)


def _render_pattern(svg, tag, pattern, x, y, size, density, stroke_width):
    """Render a single pattern into a cell."""
    sw = str(stroke_width)
    if pattern == "hatch":
        for (x1, y1), (x2, y2) in _hatch_lines(x, y, size, density, angle=45, stroke_width=stroke_width):
            ET.SubElement(svg, tag("line"), {
                "x1": f"{x1:.2f}", "y1": f"{y1:.2f}",
                "x2": f"{x2:.2f}", "y2": f"{y2:.2f}",
                "stroke": "black", "stroke-width": sw,
            })
    elif pattern == "crosshatch":
        for angle in (45, -45):
            for (x1, y1), (x2, y2) in _hatch_lines(x, y, size, density, angle=angle, stroke_width=stroke_width):
                ET.SubElement(svg, tag("line"), {
                    "x1": f"{x1:.2f}", "y1": f"{y1:.2f}",
                    "x2": f"{x2:.2f}", "y2": f"{y2:.2f}",
                    "stroke": "black", "stroke-width": sw,
                })
    elif pattern == "dots":
        for (x1, y1), (x2, y2) in _dot_marks(x, y, size, density, stroke_width=stroke_width):
            ET.SubElement(svg, tag("line"), {
                "x1": f"{x1:.2f}", "y1": f"{y1:.2f}",
                "x2": f"{x2:.2f}", "y2": f"{y2:.2f}",
                "stroke": "black", "stroke-width": sw,
            })
    elif pattern == "circles":
        for arc in _circle_arcs(x, y, size, density, stroke_width=stroke_width):
            if len(arc) < 2:
                continue
            points = " ".join(f"{px:.2f},{py:.2f}" for px, py in arc)
            ET.SubElement(svg, tag("polyline"), {
                "points": points,
                "fill": "none", "stroke": "black",
                "stroke-width": sw,
            })


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate SVG calibration grid for plotter shading tests."
    )
    parser.add_argument(
        "-o", "--output", default="calibration_grid.svg",
        help="Output SVG file path (default: calibration_grid.svg)",
    )
    parser.add_argument(
        "--cell-size", type=float, default=DEFAULT_CELL_MM,
        help=f"Cell size in mm (default: {DEFAULT_CELL_MM})",
    )
    parser.add_argument(
        "--densities", type=int, nargs="+", default=DEFAULT_DENSITIES,
        help=f"Density levels as integers 0-100 (default: {DEFAULT_DENSITIES})",
    )
    parser.add_argument(
        "--patterns", nargs="+", choices=PATTERNS, default=PATTERNS,
        help=f"Pattern types to include (default: all)",
    )
    parser.add_argument(
        "--stroke-width", type=float, default=0.5,
        help="Stroke width in mm (default: 0.5)",
    )
    parser.add_argument(
        "--gap", type=float, default=DEFAULT_GAP_MM,
        help=f"Gap between cells in mm (default: {DEFAULT_GAP_MM})",
    )
    args = parser.parse_args(argv)

    tree = generate_calibration_svg(
        cell_size=args.cell_size,
        densities=args.densities,
        patterns=args.patterns,
        stroke_width=args.stroke_width,
        gap=args.gap,
    )
    tree.write(args.output, xml_declaration=True, encoding="unicode")
    print(f"Written: {args.output}")


if __name__ == "__main__":
    main()
