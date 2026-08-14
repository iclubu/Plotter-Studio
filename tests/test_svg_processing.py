"""Tests for SVG preprocessing pipeline."""
import pytest
from server import (
    parse_svg_path_to_subpaths,
    apply_transform,
    douglas_peucker,
)


# ── parse_svg_path_to_subpaths ──

class TestParsePathBasic:
    def test_simple_moveto_lineto(self):
        paths = parse_svg_path_to_subpaths("M 0 0 L 10 10 L 20 0")
        assert len(paths) == 1
        assert paths[0] == [(0, 0), (10, 10), (20, 0)]

    def test_relative_moveto_lineto(self):
        paths = parse_svg_path_to_subpaths("m 5 5 l 10 0 l 0 10")
        assert len(paths) == 1
        assert paths[0] == [(5, 5), (15, 5), (15, 15)]

    def test_horizontal_vertical(self):
        paths = parse_svg_path_to_subpaths("M 0 0 H 10 V 10 H 0 Z")
        assert len(paths) == 1
        # Should close back to origin
        assert paths[0][0] == (0, 0)
        assert paths[0][-1] == (0, 0)

    def test_relative_horizontal_vertical(self):
        paths = parse_svg_path_to_subpaths("M 5 5 h 10 v 10 h -10 z")
        assert len(paths) == 1
        assert paths[0] == [(5, 5), (15, 5), (15, 15), (5, 15), (5, 5)]

    def test_empty_path(self):
        paths = parse_svg_path_to_subpaths("")
        assert paths == []

    def test_single_point(self):
        paths = parse_svg_path_to_subpaths("M 0 0")
        assert paths == []  # need at least 2 points


class TestParsePathMultipleSubpaths:
    def test_two_subpaths(self):
        paths = parse_svg_path_to_subpaths("M 0 0 L 10 10 M 20 20 L 30 30")
        assert len(paths) == 2
        assert paths[0] == [(0, 0), (10, 10)]
        assert paths[1] == [(20, 20), (30, 30)]

    def test_relative_subpath_after_absolute(self):
        paths = parse_svg_path_to_subpaths("M 10 10 L 20 20 m 5 5 l 10 0")
        assert len(paths) == 2
        assert paths[0] == [(10, 10), (20, 20)]
        # relative m is from last position (20,20)
        assert paths[1] == [(25, 25), (35, 25)]

    def test_close_then_new_subpath(self):
        paths = parse_svg_path_to_subpaths("M 0 0 L 10 0 L 10 10 Z M 20 20 L 30 30")
        assert len(paths) == 2


class TestParsePathCurves:
    def test_cubic_bezier_absolute(self):
        paths = parse_svg_path_to_subpaths("M 0 0 C 10 20 30 20 40 0")
        assert len(paths) == 1
        # Only start and endpoint (control points consumed)
        assert paths[0][0] == (0, 0)
        assert paths[0][-1] == (40, 0)

    def test_cubic_bezier_relative(self):
        paths = parse_svg_path_to_subpaths("M 5 5 c 10 20 30 20 40 0")
        assert len(paths) == 1
        assert paths[0][0] == (5, 5)
        assert paths[0][-1] == (45, 5)

    def test_quadratic_bezier(self):
        paths = parse_svg_path_to_subpaths("M 0 0 Q 10 20 20 0")
        assert len(paths) == 1
        assert paths[0][0] == (0, 0)
        assert paths[0][-1] == (20, 0)

    def test_arc_command(self):
        paths = parse_svg_path_to_subpaths("M 0 0 A 10 10 0 0 1 20 0")
        assert len(paths) == 1
        assert paths[0][-1] == (20, 0)


class TestParsePathImplicitRepeats:
    def test_implicit_lineto_after_moveto(self):
        # After M, subsequent coordinate pairs are implicit L
        paths = parse_svg_path_to_subpaths("M 0 0 10 10 20 0")
        assert len(paths) == 1
        assert paths[0] == [(0, 0), (10, 10), (20, 0)]

    def test_implicit_relative_lineto(self):
        paths = parse_svg_path_to_subpaths("m 0 0 10 10 10 -10")
        assert len(paths) == 1
        assert paths[0] == [(0, 0), (10, 10), (20, 0)]


# ── apply_transform ──

class TestApplyTransform:
    def test_translate(self):
        paths = [[(0, 0), (10, 10)]]
        result = apply_transform(paths, "translate(5, 3)")
        assert result == [[(5, 3), (15, 13)]]

    def test_scale(self):
        paths = [[(10, 20)]]
        result = apply_transform(paths, "scale(2)")
        assert result == [[(20, 40)]]

    def test_scale_xy(self):
        paths = [[(10, 20)]]
        result = apply_transform(paths, "scale(2, 3)")
        assert result == [[(20, 60)]]

    def test_matrix(self):
        # Identity matrix
        paths = [[(5, 10)]]
        result = apply_transform(paths, "matrix(1, 0, 0, 1, 0, 0)")
        assert result[0][0] == pytest.approx((5, 10))

    def test_no_transform(self):
        paths = [[(1, 2)]]
        result = apply_transform(paths, "")
        assert result == paths

    def test_combined_transforms(self):
        paths = [[(10, 10)]]
        result = apply_transform(paths, "translate(5, 5) scale(2)")
        # scale(2) applied first: (20, 20), then translate: (25, 25)
        assert result[0][0] == pytest.approx((25, 25))


# ── douglas_peucker ──

class TestDouglasPeucker:
    def test_straight_line(self):
        # Points on a straight line should simplify to just endpoints
        pts = [(0, 0), (5, 5), (10, 10)]
        result = douglas_peucker(pts, 0.1)
        assert result == [(0, 0), (10, 10)]

    def test_preserves_corners(self):
        # L-shape should keep the corner
        pts = [(0, 0), (10, 0), (10, 10)]
        result = douglas_peucker(pts, 0.1)
        assert len(result) == 3

    def test_two_points_unchanged(self):
        pts = [(0, 0), (10, 10)]
        result = douglas_peucker(pts, 1.0)
        assert result == pts

    def test_single_point_unchanged(self):
        pts = [(5, 5)]
        result = douglas_peucker(pts, 1.0)
        assert result == pts

    def test_zero_tolerance(self):
        pts = [(0, 0), (5, 5), (10, 10)]
        result = douglas_peucker(pts, 0)
        assert result == pts  # no simplification

    def test_high_tolerance_reduces_to_endpoints(self):
        pts = [(0, 0), (1, 0.1), (2, -0.1), (3, 0.05), (4, 0)]
        result = douglas_peucker(pts, 1.0)
        assert result == [(0, 0), (4, 0)]

    def test_preserves_significant_deviation(self):
        pts = [(0, 0), (5, 10), (10, 0)]
        result = douglas_peucker(pts, 1.0)
        assert len(result) == 3  # peak is significant
