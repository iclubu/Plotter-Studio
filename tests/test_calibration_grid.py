"""Tests for the calibration grid SVG generator."""
import xml.etree.ElementTree as ET
import math
import os
import tempfile

import pytest

from tools.calibration_grid import (
    PATTERNS,
    DEFAULT_DENSITIES,
    DEFAULT_CELL_MM,
    DEFAULT_GAP_MM,
    generate_calibration_svg,
    main,
    _hatch_lines,
    _clip_line_to_rect,
    _dot_marks,
    _circle_arcs,
    _stroke_text,
)

NS = "http://www.w3.org/2000/svg"


# ── Line clipping ──

class TestClipLine:
    def test_horizontal_through_center(self):
        pt = _clip_line_to_rect(5, 5, 1, 0, 0, 0, 10)
        assert pt is not None
        (x1, y1), (x2, y2) = pt
        assert pytest.approx(x1) == 0
        assert pytest.approx(x2) == 10

    def test_vertical_through_center(self):
        pt = _clip_line_to_rect(5, 5, 0, 1, 0, 0, 10)
        assert pt is not None
        (x1, y1), (x2, y2) = pt
        assert pytest.approx(y1) == 0
        assert pytest.approx(y2) == 10

    def test_line_outside_returns_none(self):
        # horizontal line at y=15, rect 0-10
        assert _clip_line_to_rect(5, 15, 1, 0, 0, 0, 10) is None

    def test_diagonal(self):
        pt = _clip_line_to_rect(5, 5, 1, 1, 0, 0, 10)
        assert pt is not None
        (x1, y1), (x2, y2) = pt
        assert x1 >= 0 and x2 <= 10
        assert y1 >= 0 and y2 <= 10


# ── Hatch lines ──

class TestHatchLines:
    def test_returns_lines(self):
        lines = _hatch_lines(0, 0, 20, 50, angle=45)
        assert len(lines) > 0

    def test_higher_density_more_lines(self):
        sparse = _hatch_lines(0, 0, 20, 10)
        dense = _hatch_lines(0, 0, 20, 75)
        assert len(dense) > len(sparse)

    def test_lines_within_bounds(self):
        x, y, size = 10, 10, 20
        for (x1, y1), (x2, y2) in _hatch_lines(x, y, size, 50):
            assert x1 >= x - 0.01 and x1 <= x + size + 0.01
            assert x2 >= x - 0.01 and x2 <= x + size + 0.01
            assert y1 >= y - 0.01 and y1 <= y + size + 0.01
            assert y2 >= y - 0.01 and y2 <= y + size + 0.01

    def test_density_20_produces_many_lines(self):
        """20% density on a 20mm cell should produce well more than 1 line."""
        lines = _hatch_lines(0, 0, 20, 20, stroke_width=0.3)
        # spacing = 0.3 * 100/20 = 1.5mm, diagonal ~28mm => ~19 lines
        assert len(lines) >= 10

    def test_spacing_derived_from_stroke_width(self):
        """Doubling stroke_width should halve the line count (same coverage)."""
        thin = _hatch_lines(0, 0, 20, 50, stroke_width=0.3)
        thick = _hatch_lines(0, 0, 20, 50, stroke_width=0.6)
        assert pytest.approx(len(thin), abs=2) == len(thick) * 2


# ── Dot marks ──

class TestDotMarks:
    def test_returns_marks(self):
        marks = _dot_marks(0, 0, 20, 50)
        assert len(marks) > 0

    def test_marks_are_short_segments(self):
        """Each mark should be a tiny line, not a circle."""
        marks = _dot_marks(0, 0, 20, 50, stroke_width=0.5)
        for (x1, y1), (x2, y2) in marks:
            length = math.hypot(x2 - x1, y2 - y1)
            assert pytest.approx(length, abs=0.01) == 0.5  # stroke_width

    def test_marks_inside_cell(self):
        x, y, size = 5, 5, 20
        for (x1, y1), (x2, y2) in _dot_marks(x, y, size, 50, stroke_width=0.3):
            mid_x = (x1 + x2) / 2
            mid_y = (y1 + y2) / 2
            assert mid_x > x and mid_x < x + size
            assert mid_y > y and mid_y < y + size

    def test_higher_density_more_marks(self):
        sparse = _dot_marks(0, 0, 20, 10)
        dense = _dot_marks(0, 0, 20, 75)
        assert len(dense) > len(sparse)


# ── Circle arcs ──

class TestCircleArcs:
    def test_returns_arcs(self):
        arcs = _circle_arcs(0, 0, 20, 50)
        assert len(arcs) > 0

    def test_arcs_clipped_to_cell(self):
        """All points in every arc must be inside the cell bounds."""
        x, y, size = 5, 5, 20
        arcs = _circle_arcs(x, y, size, 50, stroke_width=0.5)
        for arc in arcs:
            for px, py in arc:
                assert px >= x - 0.1 and px <= x + size + 0.1
                assert py >= y - 0.1 and py <= y + size + 0.1

    def test_extends_beyond_inscribed_circle(self):
        """Should have arcs whose radius exceeds size/2 (extends to edges)."""
        x, y, size = 0, 0, 20
        arcs = _circle_arcs(x, y, size, 50, stroke_width=0.5)
        # With dense spacing, some circles will be larger than inscribed
        # Check that we get more arcs than would fit inside r=size/2
        max_inscribed = size / 2
        spacing = 0.5 * 100.0 / 50  # = 1.0
        expected_if_clipped = int(max_inscribed / spacing)
        # Total arcs should exceed count limited to inscribed circle
        assert len(arcs) > expected_if_clipped

    def test_higher_density_more_arcs(self):
        sparse = _circle_arcs(0, 0, 20, 10)
        dense = _circle_arcs(0, 0, 20, 50)
        # More density = more rings = more arc segments
        total_pts_sparse = sum(len(a) for a in sparse)
        total_pts_dense = sum(len(a) for a in dense)
        assert total_pts_dense > total_pts_sparse


# ── Stroke text ──

class TestStrokeText:
    def test_renders_polylines(self):
        NS = "http://www.w3.org/2000/svg"
        ET.register_namespace("", NS)
        svg = ET.Element(f"{{{NS}}}svg")
        tag = lambda n: f"{{{NS}}}{n}"
        _stroke_text(svg, tag, "50%", 10, 10, 3, "0.2", anchor="middle")
        polylines = svg.findall(f".//{{{NS}}}polyline")
        assert len(polylines) > 0

    def test_no_text_elements(self):
        """Stroke text should produce polylines, not <text> elements."""
        NS = "http://www.w3.org/2000/svg"
        ET.register_namespace("", NS)
        svg = ET.Element(f"{{{NS}}}svg")
        tag = lambda n: f"{{{NS}}}{n}"
        _stroke_text(svg, tag, "hello", 10, 10, 3, "0.2")
        texts = svg.findall(f".//{{{NS}}}text")
        assert len(texts) == 0

    def test_empty_string_no_output(self):
        NS = "http://www.w3.org/2000/svg"
        ET.register_namespace("", NS)
        svg = ET.Element(f"{{{NS}}}svg")
        tag = lambda n: f"{{{NS}}}{n}"
        _stroke_text(svg, tag, "", 10, 10, 3, "0.2")
        assert len(list(svg)) == 0


# ── Full SVG generation ──

class TestGenerateSVG:
    def test_produces_valid_svg(self):
        tree = generate_calibration_svg()
        root = tree.getroot()
        assert root.tag == f"{{{NS}}}svg" or root.tag == "svg"

    def test_default_grid_has_correct_cell_count(self):
        tree = generate_calibration_svg()
        root = tree.getroot()
        rects = root.findall(f".//{{{NS}}}rect")
        expected = len(PATTERNS) * len(DEFAULT_DENSITIES)
        assert len(rects) == expected

    def test_custom_densities(self):
        densities = [20, 40, 60]
        tree = generate_calibration_svg(densities=densities)
        root = tree.getroot()
        rects = root.findall(f".//{{{NS}}}rect")
        assert len(rects) == len(PATTERNS) * len(densities)

    def test_custom_patterns(self):
        patterns = ["hatch", "dots"]
        tree = generate_calibration_svg(patterns=patterns)
        root = tree.getroot()
        rects = root.findall(f".//{{{NS}}}rect")
        assert len(rects) == len(patterns) * len(DEFAULT_DENSITIES)

    def test_cell_size_affects_viewbox(self):
        small = generate_calibration_svg(cell_size=10)
        large = generate_calibration_svg(cell_size=40)
        small_vb = small.getroot().get("viewBox")
        large_vb = large.getroot().get("viewBox")
        # Larger cell should yield a bigger viewBox
        small_w = float(small_vb.split()[2])
        large_w = float(large_vb.split()[2])
        assert large_w > small_w

    def test_hatch_cells_have_lines(self):
        tree = generate_calibration_svg(patterns=["hatch"], densities=[50])
        root = tree.getroot()
        lines = root.findall(f".//{{{NS}}}line")
        assert len(lines) > 0

    def test_crosshatch_has_more_lines_than_hatch(self):
        hatch = generate_calibration_svg(patterns=["hatch"], densities=[50])
        cross = generate_calibration_svg(patterns=["crosshatch"], densities=[50])
        h_lines = len(hatch.getroot().findall(f".//{{{NS}}}line"))
        c_lines = len(cross.getroot().findall(f".//{{{NS}}}line"))
        # Crosshatch has two sets of lines
        assert c_lines > h_lines

    def test_dots_cells_have_line_marks(self):
        """Dots pattern should produce <line> elements, not <circle>."""
        tree = generate_calibration_svg(patterns=["dots"], densities=[50])
        root = tree.getroot()
        lines = root.findall(f".//{{{NS}}}line")
        assert len(lines) > 0
        circles = root.findall(f".//{{{NS}}}circle")
        assert len(circles) == 0

    def test_circles_cells_have_polylines(self):
        """Circles pattern should produce clipped <polyline> arcs."""
        tree = generate_calibration_svg(patterns=["circles"], densities=[50])
        root = tree.getroot()
        polylines = root.findall(f".//{{{NS}}}polyline")
        assert len(polylines) > 0
        circles = root.findall(f".//{{{NS}}}circle")
        assert len(circles) == 0

    def test_labels_are_stroked_not_text(self):
        """Labels should be polylines, not <text> elements."""
        tree = generate_calibration_svg()
        root = tree.getroot()
        texts = root.findall(f".//{{{NS}}}text")
        assert len(texts) == 0
        polylines = root.findall(f".//{{{NS}}}polyline")
        assert len(polylines) > 0

    def test_gap_increases_viewbox(self):
        no_gap = generate_calibration_svg(gap=0)
        with_gap = generate_calibration_svg(gap=5)
        no_gap_w = float(no_gap.getroot().get("viewBox").split()[2])
        with_gap_w = float(with_gap.getroot().get("viewBox").split()[2])
        assert with_gap_w > no_gap_w

    def test_gap_separates_cells(self):
        """Adjacent cell rects should not share an edge when gap > 0."""
        tree = generate_calibration_svg(
            patterns=["hatch"], densities=[10, 20], gap=4,
        )
        rects = tree.getroot().findall(f".//{{{NS}}}rect")
        assert len(rects) == 2
        r0_right = float(rects[0].get("x")) + float(rects[0].get("width"))
        r1_left = float(rects[1].get("x"))
        assert r1_left - r0_right == pytest.approx(4, abs=0.01)


# ── CLI ──

class TestCLI:
    def test_default_output(self, tmp_path):
        out = str(tmp_path / "test.svg")
        main(["-o", out])
        assert os.path.exists(out)
        tree = ET.parse(out)
        root = tree.getroot()
        assert "svg" in root.tag

    def test_custom_args(self, tmp_path):
        out = str(tmp_path / "custom.svg")
        main([
            "-o", out,
            "--cell-size", "15",
            "--densities", "20", "60",
            "--patterns", "dots", "circles",
            "--stroke-width", "0.5",
        ])
        assert os.path.exists(out)
        tree = ET.parse(out)
        rects = tree.getroot().findall(f".//{{{NS}}}rect")
        assert len(rects) == 2 * 2  # 2 patterns x 2 densities
