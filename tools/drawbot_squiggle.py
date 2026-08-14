#!/usr/bin/env python3
"""
image_to_squiggle_svg.py

Convert a JPG/PNG into an SVG made of many squiggly line paths,
in a drawbot-like style where density of lines creates depth.

Usage:
    python image_to_squiggle_svg.py input.jpg output.svg

Examples:
    # Basic usage
    python image_to_squiggle_svg.py input.jpg output.svg

    # Low contrast image - unsharp mask is key for legibility
    python image_to_squiggle_svg.py input.jpg output.svg \
        --unsharp 3

    # Very low contrast - aggressive enhancement
    python image_to_squiggle_svg.py input.jpg output.svg \
        --gamma 0.7 \
        --unsharp 4 \
        --clahe

By default, automatic contrast enhancement is applied (histogram stretching).
Use --contrast to override with manual contrast (value > 1.0 increases contrast).
Use --gamma to adjust brightness curve (0.5=brighten, 2.0=darken).
Use --clahe for local contrast enhancement (good for low contrast images).
"""

from __future__ import annotations

import argparse
import base64
import math
import random
from dataclasses import dataclass
from xml.sax.saxutils import escape

import numpy as np
from PIL import Image, ImageFilter


STRAIGHT_OFFSETS = (
    -0.24434609527920614,
    -0.12217304763960307,
    0.0,
    0.12217304763960307,
    0.24434609527920614,
)
CURVE_OFFSETS = (
    -1.3962634015954636,
    -0.7853981633974483,
    -0.3490658503988659,
    0.0,
    0.3490658503988659,
    0.7853981633974483,
    1.3962634015954636,
)
AXIAL_OFFSETS = (0.0, math.pi / 2.0, math.pi, 3.0 * math.pi / 2.0)
ASCII_CHARS = "ILFTJRXVUNOCZMWQPKDBHAMY"
ASCII_SIZE_SCALE = 0.75
SIMPLE_GLYPH_STROKES: dict[str, tuple[tuple[tuple[float, float], ...], ...]] = {
    "A": (((0.15, 1.0), (0.5, 0.0), (0.85, 1.0)), ((0.28, 0.62), (0.72, 0.62))),
    "B": (((0.18, 0.0), (0.18, 1.0)), ((0.18, 0.0), (0.68, 0.08), (0.7, 0.45), (0.18, 0.5)), ((0.18, 0.5), (0.72, 0.58), (0.74, 0.95), (0.18, 1.0))),
    "C": (((0.82, 0.12), (0.66, 0.02), (0.34, 0.02), (0.16, 0.2), (0.16, 0.8), (0.34, 0.98), (0.66, 0.98), (0.82, 0.88)),),
    "D": (((0.18, 0.0), (0.18, 1.0)), ((0.18, 0.0), (0.62, 0.08), (0.82, 0.3), (0.82, 0.7), (0.62, 0.92), (0.18, 1.0))),
    "F": (((0.18, 0.0), (0.18, 1.0)), ((0.18, 0.0), (0.84, 0.0)), ((0.18, 0.5), (0.68, 0.5))),
    "H": (((0.18, 0.0), (0.18, 1.0)), ((0.82, 0.0), (0.82, 1.0)), ((0.18, 0.5), (0.82, 0.5))),
    "I": (((0.5, 0.0), (0.5, 1.0)),),
    "J": (((0.8, 0.0), (0.8, 0.78), (0.65, 0.98), (0.38, 0.98), (0.22, 0.84)),),
    "K": (((0.2, 0.0), (0.2, 1.0)), ((0.8, 0.0), (0.2, 0.55), (0.8, 1.0))),
    "L": (((0.2, 0.0), (0.2, 1.0), (0.82, 1.0)),),
    "M": (((0.14, 1.0), (0.14, 0.0), (0.5, 0.56), (0.86, 0.0), (0.86, 1.0)),),
    "N": (((0.16, 1.0), (0.16, 0.0), (0.84, 1.0), (0.84, 0.0)),),
    "O": (((0.5, 0.02), (0.76, 0.1), (0.9, 0.32), (0.9, 0.68), (0.76, 0.9), (0.5, 0.98), (0.24, 0.9), (0.1, 0.68), (0.1, 0.32), (0.24, 0.1), (0.5, 0.02)),),
    "P": (((0.18, 1.0), (0.18, 0.0), (0.68, 0.08), (0.72, 0.38), (0.18, 0.46)),),
    "Q": (((0.5, 0.02), (0.76, 0.1), (0.9, 0.32), (0.9, 0.68), (0.76, 0.9), (0.5, 0.98), (0.24, 0.9), (0.1, 0.68), (0.1, 0.32), (0.24, 0.1), (0.5, 0.02)), ((0.62, 0.72), (0.9, 1.0))),
    "R": (((0.18, 1.0), (0.18, 0.0), (0.68, 0.08), (0.72, 0.4), (0.18, 0.5), (0.78, 1.0)),),
    "T": (((0.1, 0.0), (0.9, 0.0)), ((0.5, 0.0), (0.5, 1.0))),
    "U": (((0.16, 0.0), (0.16, 0.72), (0.3, 0.96), (0.7, 0.96), (0.84, 0.72), (0.84, 0.0)),),
    "V": (((0.14, 0.0), (0.5, 1.0), (0.86, 0.0)),),
    "W": (((0.1, 0.0), (0.28, 1.0), (0.5, 0.46), (0.72, 1.0), (0.9, 0.0)),),
    "X": (((0.14, 0.0), (0.86, 1.0)), ((0.86, 0.0), (0.14, 1.0))),
    "Y": (((0.14, 0.0), (0.5, 0.48), (0.86, 0.0)), ((0.5, 0.48), (0.5, 1.0))),
    "Z": (((0.12, 0.0), (0.88, 0.0), (0.12, 1.0), (0.88, 1.0)),),
}


@dataclass
class Config:
    width: int = 1200
    strokes: int = 5000
    stroke_len: int = 12
    step: int = 3
    blur_radius: float = 1.2
    contrast: float | None = None
    gamma: float | None = None
    seed: int = 1
    dark_bias: float = 1.8
    edge_bias: float = 0.9
    min_darkness: float = 0.01
    stroke_width: float = 1.0
    simplify_decimals: int = 2
    background: bool = False
    clahe_clip_limit: float = 2.0
    clahe_tile_size: int = 8
    unsharp_amount: int = 2
    max_pixel_coverage: int = 2
    max_overlap_ratio: float = 0.55
    coverage_radius: int = 1
    continuous: bool = True
    continuous_chain_max: int = 18
    style: str = "squiggle"
    ascii_method: str = "auto"
    ascii_cell: int = 10
    ascii_count: int = 0
    ascii_min_darkness: float = 0.14
    ascii_font: str = "Hershey Simplex, CNC Vector, SingleLine, monospace"
    ascii_stroke_text: bool = True
    ascii_font_file: str | None = None
    ascii_text_as_paths: bool = True
    splat_edge_bias: float = 1.25
    splat_satellites: int = 3
    splat_drips: int = 2


@dataclass
class GlyphMark:
    ch: str
    x: float
    y: float
    size: float
    angle: float
    opacity: float


@dataclass
class SplatPrimitive:
    d: str
    fill: bool
    opacity: float
    stroke_width: float = 0.0


def parse_args() -> tuple[str, str, Config]:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Input JPG/PNG")
    parser.add_argument("output", nargs="?", help="Output SVG (default: [input].svg in current dir)")
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--strokes", type=int, default=5000)
    parser.add_argument("--stroke-len", type=int, default=12)
    parser.add_argument("--step", type=int, default=3)
    parser.add_argument("--blur-radius", type=float, default=1.2)
    parser.add_argument("--contrast", type=float, default=None, help="Manual contrast (default: auto)")
    parser.add_argument("--gamma", type=float, default=None, help="Gamma correction (0.5=brighten, 2.0=darken)")
    parser.add_argument("--clahe", action="store_true", help="Use CLAHE for adaptive contrast enhancement")
    parser.add_argument("--unsharp", type=int, default=2, help="Unsharp mask strength (0=off, 1-5=low-high)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--dark-bias", type=float, default=1.8)
    parser.add_argument("--edge-bias", type=float, default=0.9)
    parser.add_argument("--min-darkness", type=float, default=0.01)
    parser.add_argument("--stroke-width", type=float, default=1.0)
    parser.add_argument("--decimals", type=int, default=2)
    parser.add_argument("--max-pixel-coverage", type=int, default=2, help="Max times a pixel can be reused by accepted paths")
    parser.add_argument("--max-overlap-ratio", type=float, default=0.55, help="Reject path if this fraction of its pixels already have ink")
    parser.add_argument("--coverage-radius", type=int, default=1, help="Pixel radius to reserve around accepted paths")
    parser.add_argument("--segmented", action="store_true", help="Disable continuous path chaining")
    parser.add_argument("--chain-max", type=int, default=18, help="Max accepted chunks per continuous chain")
    parser.add_argument("--style", choices=("squiggle", "hybrid", "ascii", "splat"), default="squiggle", help="Rendering style")
    parser.add_argument("--ascii-method", choices=("auto", "grid", "jitter", "edge", "multiscale"), default="auto", help="ASCII mark generation method")
    parser.add_argument("--ascii-cell", type=int, default=10, help="Base cell size for ASCII rendering")
    parser.add_argument("--ascii-count", type=int, default=0, help="ASCII mark count for stochastic methods (0=use --strokes)")
    parser.add_argument("--ascii-min-darkness", type=float, default=0.14, help="Minimum darkness for placing ASCII letters")
    parser.add_argument("--ascii-font", type=str, default="Hershey Simplex, CNC Vector, SingleLine, monospace", help="Font family used for ASCII mode")
    parser.add_argument("--ascii-fill-text", action="store_true", help="Use filled text instead of stroke-only glyphs in ASCII mode")
    parser.add_argument("--ascii-font-file", type=str, default=None, help="Optional OTF/TTF font file to embed into SVG for reliable rendering")
    parser.add_argument("--ascii-text-elements", action="store_true", help="Keep ASCII glyphs as <text> elements instead of converting to <path>")
    parser.add_argument("--splat-edge-bias", type=float, default=1.25, help="Boost splats near edges")
    parser.add_argument("--splat-satellites", type=int, default=3, help="Max tiny splats around each main splat")
    parser.add_argument("--splat-drips", type=int, default=2, help="Max drip strokes emitted per splat")
    parser.add_argument("--background", action="store_true", help="Add white background rect")
    args = parser.parse_args()

    cfg = Config(
        width=args.width,
        strokes=args.strokes,
        stroke_len=args.stroke_len,
        step=args.step,
        blur_radius=args.blur_radius,
        contrast=args.contrast,
        gamma=args.gamma,
        seed=args.seed,
        dark_bias=args.dark_bias,
        edge_bias=args.edge_bias,
        min_darkness=args.min_darkness,
        stroke_width=args.stroke_width,
        simplify_decimals=args.decimals,
        background=args.background,
        clahe_clip_limit=2.0 if args.clahe else 0.0,
        unsharp_amount=args.unsharp,
        max_pixel_coverage=args.max_pixel_coverage,
        max_overlap_ratio=args.max_overlap_ratio,
        coverage_radius=args.coverage_radius,
        continuous=not args.segmented,
        continuous_chain_max=max(1, args.chain_max),
        style=args.style,
        ascii_method=args.ascii_method,
        ascii_cell=max(4, args.ascii_cell),
        ascii_count=max(0, args.ascii_count),
        ascii_min_darkness=max(0.0, min(1.0, args.ascii_min_darkness)),
        ascii_font=args.ascii_font,
        ascii_stroke_text=not args.ascii_fill_text,
        ascii_font_file=args.ascii_font_file,
        ascii_text_as_paths=not args.ascii_text_elements,
        splat_edge_bias=max(0.0, args.splat_edge_bias),
        splat_satellites=max(0, args.splat_satellites),
        splat_drips=max(0, args.splat_drips),
    )
    return args.input, args.output, cfg


def load_grayscale(path: str, width: int) -> np.ndarray:
    img = Image.open(path).convert("L")
    w, h = img.size
    scale = width / float(w)
    new_h = max(1, int(h * scale))
    img = img.resize((width, new_h), Image.Resampling.LANCZOS)
    return np.asarray(img, dtype=np.float32) / 255.0


def enhance_tone(gray: np.ndarray, contrast: float) -> np.ndarray:
    x = (gray - 0.5) * contrast + 0.5
    return np.clip(x, 0.0, 1.0)


def auto_contrast(gray: np.ndarray, low_percentile: float = 0.5, high_percentile: float = 99.5) -> np.ndarray:
    lo = np.percentile(gray, low_percentile)
    hi = np.percentile(gray, high_percentile)
    if hi <= lo:
        return np.clip(gray, 0.0, 1.0)
    stretched = (gray - lo) / (hi - lo)
    return np.clip(stretched, 0.0, 1.0)


def apply_gamma(gray: np.ndarray, gamma: float) -> np.ndarray:
    return np.power(gray, gamma)


def histogram_equalize(gray: np.ndarray) -> np.ndarray:
    flat = gray.ravel()
    hist, bins = np.histogram(flat, bins=256, range=(0.0, 1.0))
    cdf = hist.cumsum()
    cdf = cdf / cdf[-1]
    return np.interp(flat, bins[:-1], cdf).reshape(gray.shape)


def unsharp_mask(gray: np.ndarray, amount: int = 1) -> np.ndarray:
    img = Image.fromarray(np.uint8(np.clip(gray * 255.0, 0, 255)))
    blurred = img.filter(ImageFilter.GaussianBlur(radius=2))
    blurred_arr = np.asarray(blurred, dtype=np.float32) / 255.0
    detail = gray - blurred_arr
    result = gray + detail * amount
    return np.clip(result, 0.0, 1.0)


def local_contrast_enhance(gray: np.ndarray, tile_size: int = 32, strength: float = 0.5) -> np.ndarray:
    h, w = gray.shape
    result = gray.copy()
    for y in range(0, h, tile_size):
        for x in range(0, w, tile_size):
            y2 = min(y + tile_size, h)
            x2 = min(x + tile_size, w)
            tile = gray[y:y2, x:x2]
            tile_mean = tile.mean()
            tile_std = tile.std()
            if tile_std > 0.001:
                local_norm = (tile - tile_mean) / tile_std
                result[y:y2, x:x2] = tile + strength * local_norm
    return np.clip(result, 0, 1)


def gaussian_blur_array(gray: np.ndarray, radius: float) -> np.ndarray:
    img = Image.fromarray(np.uint8(np.clip(gray * 255.0, 0, 255)))
    img = img.filter(ImageFilter.GaussianBlur(radius=radius))
    return np.asarray(img, dtype=np.float32) / 255.0


def sobel(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    p = np.pad(gray, 1, mode="edge")

    gx = (
        -1 * p[:-2, :-2] + 1 * p[:-2, 2:]
        -2 * p[1:-1, :-2] + 2 * p[1:-1, 2:]
        -1 * p[2:, :-2] + 1 * p[2:, 2:]
    )

    gy = (
        -1 * p[:-2, :-2] - 2 * p[:-2, 1:-1] - 1 * p[:-2, 2:]
        +1 * p[2:, :-2] + 2 * p[2:, 1:-1] + 1 * p[2:, 2:]
    )

    mag = np.sqrt(gx * gx + gy * gy)
    return gx, gy, mag


def build_sampling_map(
    gray: np.ndarray,
    mag: np.ndarray,
    dark_bias: float,
    edge_bias: float,
    min_darkness: float,
) -> np.ndarray:
    darkness = 1.0 - gray
    darkness = np.clip(darkness - min_darkness, 0.0, 1.0)
    edge = mag / (mag.max() + 1e-8)

    weights = (darkness ** dark_bias) + edge_bias * (edge ** 1.3) * (darkness ** 0.7)
    weights += 0.002
    weights = np.clip(weights, 0.0, None)
    weights /= weights.sum()
    return weights


def build_residual_sampling_map(
    residual_dark: np.ndarray,
    edge: np.ndarray,
    coverage: np.ndarray,
    dark_bias: float,
    edge_bias: float,
    min_darkness: float,
    max_pixel_coverage: int,
) -> np.ndarray:
    darkness = np.clip(residual_dark - min_darkness, 0.0, 1.0)
    edge_term = edge ** 1.2

    ink_penalty = 1.0 - (coverage.astype(np.float32) / max(1, max_pixel_coverage))
    ink_penalty = np.clip(ink_penalty, 0.05, 1.0)

    weights = (darkness ** dark_bias) * (0.85 + (edge_bias * edge_term))
    weights *= ink_penalty
    weights += 1e-9
    weights /= weights.sum()
    return weights


def sample_points(weights: np.ndarray, count: int, rng: np.random.Generator) -> np.ndarray:
    flat = weights.ravel()
    replace = count > flat.size
    idx = rng.choice(flat.size, size=count, replace=replace, p=flat)
    ys, xs = np.divmod(idx, weights.shape[1])
    return np.column_stack((xs, ys))


def path_pixel_coords(points: list[tuple[float, float]], width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    if len(points) < 2:
        return np.array([], dtype=np.int32), np.array([], dtype=np.int32)

    xs_parts: list[np.ndarray] = []
    ys_parts: list[np.ndarray] = []

    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
        steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0))))
        seg_x = np.rint(np.linspace(x0, x1, steps + 1)).astype(np.int32)
        seg_y = np.rint(np.linspace(y0, y1, steps + 1)).astype(np.int32)
        valid = (seg_x >= 0) & (seg_x < width) & (seg_y >= 0) & (seg_y < height)
        if np.any(valid):
            xs_parts.append(seg_x[valid])
            ys_parts.append(seg_y[valid])

    if not xs_parts:
        return np.array([], dtype=np.int32), np.array([], dtype=np.int32)

    xs = np.concatenate(xs_parts)
    ys = np.concatenate(ys_parts)
    flat = ys * width + xs
    unique_flat = np.unique(flat)
    return (unique_flat % width).astype(np.int32), (unique_flat // width).astype(np.int32)


def expand_pixel_coords(
    xs: np.ndarray,
    ys: np.ndarray,
    width: int,
    height: int,
    radius: int,
) -> tuple[np.ndarray, np.ndarray]:
    if xs.size == 0 or ys.size == 0 or radius <= 0:
        return xs, ys

    offsets = np.arange(-radius, radius + 1, dtype=np.int32)
    dx, dy = np.meshgrid(offsets, offsets)
    dx = dx.ravel()
    dy = dy.ravel()

    ex = (xs[:, None] + dx[None, :]).ravel()
    ey = (ys[:, None] + dy[None, :]).ravel()
    valid = (ex >= 0) & (ex < width) & (ey >= 0) & (ey < height)
    ex = ex[valid]
    ey = ey[valid]

    flat = ey * width + ex
    unique_flat = np.unique(flat)
    return (unique_flat % width).astype(np.int32), (unique_flat // width).astype(np.int32)


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def quantized_axis_from_angle(angle: float) -> int:
    dx = abs(math.cos(angle))
    dy = abs(math.sin(angle))
    return 0 if dx >= dy else 1


def candidate_score(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    residual_dark: np.ndarray,
    edge: np.ndarray,
    coverage: np.ndarray,
    max_pixel_coverage: int,
) -> float:
    h, w = residual_dark.shape
    dx = x1 - x0
    dy = y1 - y0
    span = max(abs(dx), abs(dy))
    samples = max(5, min(15, int(span / 2.0) + 2))

    sum_dark = 0.0
    peak_dark = 0.0
    sum_edge = 0.0
    sum_cov = 0.0
    denom_cov = 1.0 / max(1, max_pixel_coverage)

    for i in range(samples):
        t = i / (samples - 1)
        ix = int(round(x0 + dx * t))
        iy = int(round(y0 + dy * t))

        if ix < 0:
            ix = 0
        elif ix >= w:
            ix = w - 1
        if iy < 0:
            iy = 0
        elif iy >= h:
            iy = h - 1

        dark_v = float(residual_dark[iy, ix])
        if dark_v > peak_dark:
            peak_dark = dark_v
        sum_dark += dark_v
        sum_edge += float(edge[iy, ix])
        sum_cov += float(coverage[iy, ix]) * denom_cov

    inv = 1.0 / samples
    dark = sum_dark * inv
    dark_peak = peak_dark
    edge_strength = sum_edge * inv
    cov = sum_cov * inv
    return (dark * 1.45) + (dark_peak * 0.45) + (edge_strength * 0.24) - (cov * 1.08)


def guided_squiggle(
    x0: int,
    y0: int,
    gray: np.ndarray,
    gx: np.ndarray,
    gy: np.ndarray,
    residual_dark: np.ndarray,
    edge: np.ndarray,
    coverage: np.ndarray,
    max_segments: int,
    step: int,
    max_pixel_coverage: int,
    straight_bias: float,
    long_line_bias: float,
    rng: random.Random,
) -> list[tuple[float, float]]:
    h, w = gray.shape
    path: list[tuple[float, float]] = [(float(x0), float(y0))]
    x, y = float(x0), float(y0)

    ix = int(clamp(round(x), 0, w - 1))
    iy = int(clamp(round(y), 0, h - 1))
    angle = math.atan2(gy[iy, ix], gx[iy, ix]) + math.pi / 2.0

    for _ in range(max_segments):
        ix = int(clamp(round(x), 0, w - 1))
        iy = int(clamp(round(y), 0, h - 1))
        local_dark = residual_dark[iy, ix]
        if local_dark <= 0.002:
            break

        local_edge = float(edge[iy, ix])
        grad_angle = math.atan2(gy[iy, ix], gx[iy, ix]) + math.pi / 2.0
        grad_normal = grad_angle - (math.pi / 2.0)

        straight_mode = rng.random() < (straight_bias * (0.35 + 0.65 * local_edge))
        contour_mode = local_edge > 0.08 and rng.random() < (0.30 + 0.50 * local_edge)
        axial_mode = local_edge < 0.22 and rng.random() < (0.20 + 0.35 * (1.0 - local_edge))

        if straight_mode:
            angle = 0.88 * angle + 0.12 * grad_angle
            offsets = STRAIGHT_OFFSETS
            turn_jitter = 0.025
            length_options = (1.0, 2.2)
        elif contour_mode:
            angle = 0.70 * angle + 0.30 * grad_angle
            offsets = CURVE_OFFSETS
            turn_jitter = 0.07
            length_options = (0.95, 1.8)
        else:
            angle = 0.64 * angle + 0.36 * grad_angle
            offsets = CURVE_OFFSETS
            turn_jitter = 0.08
            length_options = (0.9, 1.4)

        step_len = step * (0.85 + local_dark * 1.15)
        if rng.random() < (long_line_bias * (0.30 + 0.70 * local_dark)):
            step_len *= rng.uniform(1.8, 3.8)

        best_score = -1e9
        best_xy = None
        best_angle = angle

        for off in offsets:
            base_a = angle + off + rng.uniform(-turn_jitter, turn_jitter)
            for scale in length_options:
                trial_len = step_len * scale
                nx = clamp(x + math.cos(base_a) * trial_len, 0, w - 1)
                ny = clamp(y + math.sin(base_a) * trial_len, 0, h - 1)
                s = candidate_score(
                    x,
                    y,
                    nx,
                    ny,
                    residual_dark=residual_dark,
                    edge=edge,
                    coverage=coverage,
                    max_pixel_coverage=max_pixel_coverage,
                )
                if s > best_score:
                    best_score = s
                    best_xy = (nx, ny)
                    best_angle = base_a

        if axial_mode:
            for base_a in AXIAL_OFFSETS:
                a = base_a + rng.uniform(-0.06, 0.06)
                trial_len = step_len * rng.uniform(1.1, 2.8)
                nx = clamp(x + math.cos(a) * trial_len, 0, w - 1)
                ny = clamp(y + math.sin(a) * trial_len, 0, h - 1)
                s = candidate_score(
                    x,
                    y,
                    nx,
                    ny,
                    residual_dark=residual_dark,
                    edge=edge,
                    coverage=coverage,
                    max_pixel_coverage=max_pixel_coverage,
                )
                if s > best_score:
                    best_score = s
                    best_xy = (nx, ny)
                    best_angle = a

        if contour_mode:
            for tangent_dir in (-1.0, 1.0):
                a = grad_normal + (tangent_dir * math.pi / 2.0) + rng.uniform(-0.09, 0.09)
                trial_len = step_len * rng.uniform(1.3, 3.4)
                nx = clamp(x + math.cos(a) * trial_len, 0, w - 1)
                ny = clamp(y + math.sin(a) * trial_len, 0, h - 1)
                s = candidate_score(
                    x,
                    y,
                    nx,
                    ny,
                    residual_dark=residual_dark,
                    edge=edge,
                    coverage=coverage,
                    max_pixel_coverage=max_pixel_coverage,
                )
                if s > best_score:
                    best_score = s
                    best_xy = (nx, ny)
                    best_angle = a

        if best_xy is None or best_score < 0.01:
            break

        x, y = best_xy
        angle = best_angle
        path.append((x, y))

    return path


def score_path(
    points: list[tuple[float, float]],
    residual_dark: np.ndarray,
    edge: np.ndarray,
    coverage: np.ndarray,
    max_pixel_coverage: int,
) -> float:
    h, w = residual_dark.shape
    px, py = path_pixel_coords(points, w, h)
    if px.size == 0:
        return -1e9
    line_dark = residual_dark[py, px]
    dark = float(np.mean(line_dark))
    dark_peak = float(np.max(line_dark))
    edge_strength = float(np.mean(edge[py, px]))
    cov = float(np.mean(coverage[py, px] / max(1, max_pixel_coverage)))
    return (dark * 1.42) + (dark_peak * 0.40) + (edge_strength * 0.22) - (cov * 1.05)


def circle_mark(cx: float, cy: float, radius: float, steps: int, width: int, height: int) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in range(steps + 1):
        a = (2.0 * math.pi * i) / steps
        x = clamp(cx + math.cos(a) * radius, 0, width - 1)
        y = clamp(cy + math.sin(a) * radius, 0, height - 1)
        pts.append((x, y))
    return pts


def choose_hybrid_mark(
    x0: int,
    y0: int,
    gray: np.ndarray,
    gx: np.ndarray,
    gy: np.ndarray,
    residual_dark: np.ndarray,
    edge: np.ndarray,
    coverage: np.ndarray,
    max_segments: int,
    step: int,
    max_pixel_coverage: int,
    straight_bias: float,
    long_line_bias: float,
    rng: random.Random,
) -> tuple[list[tuple[float, float]], bool]:
    h, w = gray.shape
    ix = int(clamp(x0, 0, w - 1))
    iy = int(clamp(y0, 0, h - 1))
    local_dark = float(residual_dark[iy, ix])
    local_edge = float(edge[iy, ix])
    grad_angle = math.atan2(gy[iy, ix], gx[iy, ix]) + math.pi / 2.0
    grad_normal = grad_angle - (math.pi / 2.0)

    candidates: list[tuple[float, list[tuple[float, float]], bool]] = []

    squiggle_pts = guided_squiggle(
        x0=x0,
        y0=y0,
        gray=gray,
        gx=gx,
        gy=gy,
        residual_dark=residual_dark,
        edge=edge,
        coverage=coverage,
        max_segments=max_segments,
        step=step,
        max_pixel_coverage=max_pixel_coverage,
        straight_bias=straight_bias,
        long_line_bias=long_line_bias,
        rng=rng,
    )
    if len(squiggle_pts) >= 2:
        s = score_path(squiggle_pts, residual_dark, edge, coverage, max_pixel_coverage)
        candidates.append((s + 0.03, squiggle_pts, True))

    base_len = step * (1.4 + local_dark * 2.2)
    straight_scales = (2.3, 3.6)
    for a in (grad_angle, grad_normal, 0.0, math.pi / 2.0):
        for sc in straight_scales:
            ln = base_len * sc
            nx = clamp(x0 + math.cos(a) * ln, 0, w - 1)
            ny = clamp(y0 + math.sin(a) * ln, 0, h - 1)
            pts = [(float(x0), float(y0)), (nx, ny)]
            s = score_path(pts, residual_dark, edge, coverage, max_pixel_coverage)
            s += 0.12 * local_edge
            candidates.append((s, pts, True))

    hatch_angles = (grad_normal + math.pi / 4.0, grad_normal - math.pi / 4.0)
    for a in hatch_angles:
        ln = base_len * 1.8
        hx0 = clamp(x0 - math.cos(a) * ln * 0.5, 0, w - 1)
        hy0 = clamp(y0 - math.sin(a) * ln * 0.5, 0, h - 1)
        hx1 = clamp(x0 + math.cos(a) * ln * 0.5, 0, w - 1)
        hy1 = clamp(y0 + math.sin(a) * ln * 0.5, 0, h - 1)
        pts = [(hx0, hy0), (hx1, hy1)]
        s = score_path(pts, residual_dark, edge, coverage, max_pixel_coverage)
        s += 0.08 * local_edge
        candidates.append((s, pts, False))

    if local_edge < 0.38:
        radius = step * (0.7 + local_dark * 1.3)
        dot_pts = circle_mark(float(x0), float(y0), radius, 10, w, h)
        s = score_path(dot_pts, residual_dark, edge, coverage, max_pixel_coverage)
        s += 0.10 * (1.0 - local_edge)
        candidates.append((s, dot_pts, False))

        loop_a = grad_angle + rng.uniform(-0.25, 0.25)
        ex = radius * 1.4
        ey = radius * 0.85
        loop_pts: list[tuple[float, float]] = []
        for i in range(13):
            t = (2.0 * math.pi * i) / 12.0
            ox = (math.cos(loop_a) * math.cos(t) * ex) - (math.sin(loop_a) * math.sin(t) * ey)
            oy = (math.sin(loop_a) * math.cos(t) * ex) + (math.cos(loop_a) * math.sin(t) * ey)
            loop_pts.append((clamp(x0 + ox, 0, w - 1), clamp(y0 + oy, 0, h - 1)))
        s = score_path(loop_pts, residual_dark, edge, coverage, max_pixel_coverage)
        s += 0.12 * (1.0 - local_edge)
        candidates.append((s, loop_pts, False))

    if not candidates:
        return [(float(x0), float(y0))], False

    best = max(candidates, key=lambda item: item[0])
    return best[1], best[2]


def rectilinear_squiggle(
    x0: int,
    y0: int,
    gray: np.ndarray,
    gx: np.ndarray,
    gy: np.ndarray,
    max_segments: int,
    step: int,
    rng: random.Random,
) -> list[tuple[float, float]]:
    h, w = gray.shape
    path: list[tuple[float, float]] = [(float(x0), float(y0))]
    x, y = float(x0), float(y0)
    prev_axis = None

    for _ in range(max_segments):
        ix = int(clamp(round(x), 0, w - 1))
        iy = int(clamp(round(y), 0, h - 1))

        angle = math.atan2(gy[iy, ix], gx[iy, ix]) + math.pi / 2.0
        preferred_axis = quantized_axis_from_angle(angle)

        if prev_axis is None:
            axis = preferred_axis
        else:
            axis = 1 - prev_axis if rng.random() < 0.72 else preferred_axis

        if axis == 0:
            sign = 1 if math.cos(angle) >= 0 else -1
            if rng.random() < 0.25:
                sign *= -1
            dx = sign * step
            dy = rng.uniform(-0.35, 0.35) * step
        else:
            sign = 1 if math.sin(angle) >= 0 else -1
            if rng.random() < 0.25:
                sign *= -1
            dx = rng.uniform(-0.35, 0.35) * step
            dy = sign * step

        dark = 1.0 - gray[iy, ix]
        jitter = (1.0 - dark) * 1.3 + 0.15

        nx = clamp(x + dx + rng.uniform(-jitter, jitter), 0, w - 1)
        ny = clamp(y + dy + rng.uniform(-jitter, jitter), 0, h - 1)

        path.append((nx, ny))
        x, y = nx, ny
        prev_axis = axis

    return path


def format_num(value: float, decimals: int) -> str:
    s = f"{value:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s else "0"


def path_to_svg_d(points: list[tuple[float, float]], decimals: int) -> str:
    if not points:
        return ""
    first = points[0]
    parts = [f"M {format_num(first[0], decimals)} {format_num(first[1], decimals)}"]
    for x, y in points[1:]:
        parts.append(f"L {format_num(x, decimals)} {format_num(y, decimals)}")
    return " ".join(parts)


def pick_ascii_char(dark: float) -> str:
    d = clamp(dark, 0.0, 1.0)
    idx = int(round(d * (len(ASCII_CHARS) - 1)))
    return ASCII_CHARS[idx]


def char_weight(ch: str) -> float:
    idx = ASCII_CHARS.find(ch)
    if idx < 0:
        return 0.45
    return 0.22 + 0.78 * (idx / max(1, len(ASCII_CHARS) - 1))


def normalize_map(img: np.ndarray) -> np.ndarray:
    lo = np.quantile(img, 0.01)
    hi = np.quantile(img, 0.99)
    return np.clip((img - lo) / (hi - lo + 1e-6), 0.0, 1.0)


def render_ascii_proxy(width: int, height: int, marks: list[GlyphMark]) -> np.ndarray:
    proxy = np.zeros((height, width), dtype=np.float32)
    for mark in marks:
        ix = int(round(mark.x))
        iy = int(round(mark.y))
        if ix < 0 or ix >= width or iy < 0 or iy >= height:
            continue
        radius = max(1, int(mark.size * 0.23))
        val = char_weight(mark.ch) * mark.opacity
        x0 = max(0, ix - radius)
        x1 = min(width, ix + radius + 1)
        y0 = max(0, iy - radius)
        y1 = min(height, iy + radius + 1)
        for yy in range(y0, y1):
            dy = yy - iy
            for xx in range(x0, x1):
                dx = xx - ix
                dist2 = dx * dx + dy * dy
                if dist2 > (radius * radius):
                    continue
                falloff = 1.0 - (dist2 / max(1.0, float(radius * radius)))
                proxy[yy, xx] = min(1.0, proxy[yy, xx] + (val * (0.25 + 0.75 * falloff)))
    return proxy


def ascii_metrics(source_dark: np.ndarray, marks: list[GlyphMark]) -> tuple[float, float]:
    h, w = source_dark.shape
    proxy = render_ascii_proxy(w, h, marks)
    s = normalize_map(source_dark)
    p = normalize_map(proxy)
    corr = float(np.corrcoef(s.ravel(), p.ravel())[0, 1])
    mse = float(np.mean((s - p) ** 2))
    return corr, mse


def ascii_target_count(cfg: Config) -> int:
    return cfg.ascii_count if cfg.ascii_count > 0 else max(1, cfg.strokes)


def limit_ascii_marks(marks: list[GlyphMark], target: int, seed: int) -> list[GlyphMark]:
    if target <= 0 or len(marks) <= target:
        return marks

    rng = np.random.default_rng(seed)
    weights = np.array([max(1e-6, m.opacity * (0.55 + 0.45 * (m.size / 20.0))) for m in marks], dtype=np.float64)
    weights /= weights.sum()
    idx = rng.choice(len(marks), size=target, replace=False, p=weights)
    idx.sort()
    return [marks[int(i)] for i in idx]


def builtin_glyph_strokes(ch: str, size: float) -> list[np.ndarray]:
    key = ch.upper()
    strokes = SIMPLE_GLYPH_STROKES.get(key)
    if strokes is None:
        strokes = SIMPLE_GLYPH_STROKES["O"]

    sx = size * 0.78
    sy = size * 1.0
    result: list[np.ndarray] = []
    for stroke in strokes:
        pts: list[list[float]] = []
        for u, v in stroke:
            x = (u - 0.5) * sx
            y = (v - 0.5) * sy
            pts.append([x, y])
        if len(pts) >= 2:
            result.append(np.array(pts, dtype=np.float64))
    return result


def generate_ascii_grid(gray: np.ndarray, gx: np.ndarray, gy: np.ndarray, edge: np.ndarray, cfg: Config) -> list[GlyphMark]:
    h, w = gray.shape
    dark_map = np.clip(1.0 - gray, 0.0, 1.0)
    marks: list[GlyphMark] = []
    cell = cfg.ascii_cell
    for y in range(cell // 2, h, cell):
        for x in range(cell // 2, w, cell):
            dark = float(dark_map[y, x])
            if dark < cfg.ascii_min_darkness:
                continue
            angle = math.degrees(math.atan2(gy[y, x], gx[y, x]) + math.pi / 2.0)
            local_edge = float(edge[y, x])
            size = cell * (0.75 + dark * 1.3 + local_edge * 0.35) * ASCII_SIZE_SCALE
            opacity = clamp(0.18 + dark * 0.86, 0.16, 1.0)
            marks.append(GlyphMark(pick_ascii_char(dark), float(x), float(y), size, angle, opacity))
    return marks


def generate_ascii_jitter(gray: np.ndarray, gx: np.ndarray, gy: np.ndarray, edge: np.ndarray, cfg: Config) -> list[GlyphMark]:
    h, w = gray.shape
    dark_map = np.clip(1.0 - gray, 0.0, 1.0)
    rng = np.random.default_rng(cfg.seed)
    weights = (dark_map ** 1.35) * (0.78 + 0.45 * edge)
    weights += 1e-9
    weights /= weights.sum()
    count = cfg.ascii_count if cfg.ascii_count > 0 else cfg.strokes
    starts = sample_points(weights, count, rng)
    marks: list[GlyphMark] = []
    for x, y in starts:
        ix = int(x)
        iy = int(y)
        dark = float(dark_map[iy, ix])
        if dark < cfg.ascii_min_darkness:
            continue
        base = math.degrees(math.atan2(gy[iy, ix], gx[iy, ix]) + math.pi / 2.0)
        angle = base + rng.uniform(-34.0, 34.0)
        size = cfg.ascii_cell * (0.62 + dark * 1.55) * ASCII_SIZE_SCALE
        opacity = clamp(0.16 + dark * 0.9, 0.14, 1.0)
        marks.append(GlyphMark(pick_ascii_char(dark ** 0.92), float(ix), float(iy), size, angle, opacity))
    return marks


def generate_ascii_edge(gray: np.ndarray, gx: np.ndarray, gy: np.ndarray, edge: np.ndarray, cfg: Config) -> list[GlyphMark]:
    h, w = gray.shape
    dark_map = np.clip(1.0 - gray, 0.0, 1.0)
    rng = np.random.default_rng(cfg.seed)
    weights = (edge ** 1.8) * (0.30 + dark_map ** 1.1) + (dark_map ** 1.25)
    weights += 1e-9
    weights /= weights.sum()
    count = int((cfg.ascii_count if cfg.ascii_count > 0 else cfg.strokes) * 1.12)
    starts = sample_points(weights, count, rng)
    marks: list[GlyphMark] = []
    for x, y in starts:
        ix = int(x)
        iy = int(y)
        dark = float(dark_map[iy, ix])
        if dark < cfg.ascii_min_darkness:
            continue
        e = float(edge[iy, ix])
        angle = math.degrees(math.atan2(gy[iy, ix], gx[iy, ix]) + math.pi / 2.0)
        size = cfg.ascii_cell * (0.58 + dark * 1.35 + e * 0.8) * ASCII_SIZE_SCALE
        opacity = clamp(0.16 + dark * 0.8 + e * 0.2, 0.14, 1.0)
        marks.append(GlyphMark(pick_ascii_char((dark * 0.8) + (e * 0.2)), float(ix), float(iy), size, angle, opacity))
    return marks


def generate_ascii_multiscale(gray: np.ndarray, gx: np.ndarray, gy: np.ndarray, edge: np.ndarray, cfg: Config) -> list[GlyphMark]:
    h, w = gray.shape
    dark_map = np.clip(1.0 - gray, 0.0, 1.0)
    rng = random.Random(cfg.seed)
    marks: list[GlyphMark] = []

    coarse = max(8, int(cfg.ascii_cell * 1.8))
    fine = max(5, int(cfg.ascii_cell * 0.9))

    for y in range(coarse // 2, h, coarse):
        for x in range(coarse // 2, w, coarse):
            dark = float(dark_map[y, x])
            if dark < (cfg.ascii_min_darkness + 0.06):
                continue
            e = float(edge[y, x])
            angle = math.degrees(math.atan2(gy[y, x], gx[y, x]) + math.pi / 2.0)
            size = coarse * (0.72 + dark * 1.1) * ASCII_SIZE_SCALE
            opacity = clamp(0.17 + dark * 0.75, 0.14, 1.0)
            marks.append(GlyphMark(pick_ascii_char(dark), float(x), float(y), size, angle, opacity))

    for y in range(fine // 2, h, fine):
        for x in range(fine // 2, w, fine):
            dark = float(dark_map[y, x])
            if dark < (cfg.ascii_min_darkness + 0.14):
                continue
            jx = clamp(x + rng.uniform(-fine * 0.35, fine * 0.35), 0, w - 1)
            jy = clamp(y + rng.uniform(-fine * 0.35, fine * 0.35), 0, h - 1)
            ix = int(jx)
            iy = int(jy)
            angle = math.degrees(math.atan2(gy[iy, ix], gx[iy, ix]) + math.pi / 2.0) + rng.uniform(-20.0, 20.0)
            e = float(edge[iy, ix])
            size = fine * (0.62 + dark * 1.35 + e * 0.25) * ASCII_SIZE_SCALE
            opacity = clamp(0.14 + dark * 0.88, 0.12, 1.0)
            marks.append(GlyphMark(pick_ascii_char(dark ** 0.95), float(jx), float(jy), size, angle, opacity))

    return marks


def generate_ascii_marks(gray: np.ndarray, gx: np.ndarray, gy: np.ndarray, mag: np.ndarray, cfg: Config) -> tuple[list[GlyphMark], str, float, float]:
    edge = mag / (mag.max() + 1e-8)
    source_dark = np.clip(1.0 - gray, 0.0, 1.0)

    methods = [cfg.ascii_method] if cfg.ascii_method != "auto" else ["grid", "jitter", "edge", "multiscale"]
    target = ascii_target_count(cfg)
    best_marks: list[GlyphMark] = []
    best_method = methods[0]
    best_corr = -1e9
    best_mse = 1e9

    for method in methods:
        if method == "grid":
            marks = generate_ascii_grid(gray, gx, gy, edge, cfg)
        elif method == "jitter":
            marks = generate_ascii_jitter(gray, gx, gy, edge, cfg)
        elif method == "edge":
            marks = generate_ascii_edge(gray, gx, gy, edge, cfg)
        else:
            marks = generate_ascii_multiscale(gray, gx, gy, edge, cfg)

        marks = limit_ascii_marks(marks, target=target, seed=(cfg.seed + len(method)))

        corr, mse = ascii_metrics(source_dark, marks)
        if (corr > best_corr) or (abs(corr - best_corr) < 1e-9 and mse < best_mse):
            best_corr = corr
            best_mse = mse
            best_method = method
            best_marks = marks

    return best_marks, best_method, best_corr, best_mse


def write_ascii_svg(
    output_path: str,
    width: int,
    height: int,
    marks: list[GlyphMark],
    background: bool,
    font_family: str,
    stroke_text: bool,
    font_file: str | None,
    text_as_paths: bool,
) -> None:
    title = escape("ASCII glyph SVG")
    family = escape(font_family)
    FontProperties = None
    TextPath = None
    embedded_family = None
    embedded_css = None
    if font_file and not text_as_paths:
        with open(font_file, "rb") as ff:
            blob = ff.read()
        b64 = base64.b64encode(blob).decode("ascii")
        embedded_family = "PlotterEmbeddedASCII"
        ext = font_file.lower()
        mime = "font/otf" if ext.endswith(".otf") else "font/ttf"
        embedded_css = (
            "@font-face {"
            f"font-family:'{embedded_family}';"
            f"src:url(data:{mime};base64,{b64}) format('opentype');"
            "font-style:normal;font-weight:normal;}"
        )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'version="1.1">\n'
        )
        f.write(f'  <title>{title}</title>\n')
        if embedded_css is not None:
            f.write("  <defs><style><![CDATA[")
            f.write(embedded_css)
            f.write("]]></style></defs>\n")
        if background:
            f.write(f'  <rect x="0" y="0" width="{width}" height="{height}" fill="white"/>\n')
        family_attr = embedded_family if embedded_family is not None else family

        if text_as_paths:
            import importlib

            FontProperties = None
            TextPath = None
            try:
                font_manager = importlib.import_module("matplotlib.font_manager")
                textpath_mod = importlib.import_module("matplotlib.textpath")
                FontProperties = font_manager.FontProperties
                TextPath = textpath_mod.TextPath
            except Exception:
                FontProperties = None
                TextPath = None

        if text_as_paths:
            use_matplotlib_paths = FontProperties is not None and TextPath is not None
            font_prop = None
            if use_matplotlib_paths and FontProperties is not None:
                font_prop = FontProperties(fname=font_file) if font_file else FontProperties(family=font_family)
            glyph_cache: dict[tuple[str, float], list[np.ndarray]] = {}

            f.write('  <g fill="none" stroke="black" stroke-linecap="round" stroke-linejoin="round">\n')
            for mark in marks:
                size_q = round(mark.size, 1)
                key = (mark.ch, size_q)
                polys = glyph_cache.get(key)
                if polys is None:
                    if use_matplotlib_paths and font_prop is not None and TextPath is not None:
                        text_path = TextPath((0, 0), mark.ch, size=size_q, prop=font_prop, usetex=False)
                        raw_polys = text_path.to_polygons()
                        if not raw_polys:
                            glyph_cache[key] = []
                            continue
                        stack = np.vstack(raw_polys)
                        cx = float((stack[:, 0].min() + stack[:, 0].max()) * 0.5)
                        cy = float((stack[:, 1].min() + stack[:, 1].max()) * 0.5)
                        polys = [poly - np.array([cx, cy], dtype=np.float64) for poly in raw_polys if len(poly) >= 3]
                    else:
                        polys = builtin_glyph_strokes(mark.ch, size_q)
                    glyph_cache[key] = polys

                if not polys:
                    continue

                rad = math.radians(mark.angle)
                cos_a = math.cos(rad)
                sin_a = math.sin(rad)
                op = format_num(mark.opacity, 3)
                sw = format_num(max(0.1, mark.size * 0.05), 3)

                for poly in polys:
                    tx = (poly[:, 0] * cos_a) - (poly[:, 1] * sin_a) + mark.x
                    ty = (poly[:, 0] * sin_a) + (poly[:, 1] * cos_a) + mark.y
                    d_parts = [f"M {format_num(float(tx[0]), 2)} {format_num(float(ty[0]), 2)}"]
                    for i in range(1, len(tx)):
                        d_parts.append(f"L {format_num(float(tx[i]), 2)} {format_num(float(ty[i]), 2)}")
                    d = " ".join(d_parts)
                    f.write(f'    <path d="{escape(d)}" opacity="{op}" stroke-width="{sw}"/>\n')

            f.write("  </g>\n")
        else:
            if stroke_text:
                f.write(
                    f'  <g fill="none" stroke="black" stroke-linecap="round" stroke-linejoin="round" '
                    f'font-family="{family_attr}" font-weight="normal" text-rendering="geometricPrecision">\n'
                )
            else:
                f.write(f'  <g fill="black" stroke="none" font-family="{family_attr}" text-rendering="geometricPrecision">\n')
            for mark in marks:
                x = format_num(mark.x, 2)
                y = format_num(mark.y, 2)
                size = format_num(mark.size, 2)
                angle = format_num(mark.angle, 2)
                op = format_num(mark.opacity, 3)
                ch = escape(mark.ch)
                if stroke_text:
                    sw = format_num(max(0.12, mark.size * 0.055), 3)
                    f.write(
                        f'    <text x="{x}" y="{y}" font-size="{size}" opacity="{op}" stroke-width="{sw}" '
                        f'text-anchor="middle" dominant-baseline="middle" '
                        f'transform="rotate({angle} {x} {y})">{ch}</text>\n'
                    )
                else:
                    f.write(
                        f'    <text x="{x}" y="{y}" font-size="{size}" opacity="{op}" '
                        f'text-anchor="middle" dominant-baseline="middle" '
                        f'transform="rotate({angle} {x} {y})">{ch}</text>\n'
                    )
            f.write("  </g>\n")
        f.write("</svg>\n")


def polygon_to_svg_d(points: list[tuple[float, float]], decimals: int, close_path: bool) -> str:
    if not points:
        return ""
    parts = [f"M {format_num(points[0][0], decimals)} {format_num(points[0][1], decimals)}"]
    for x, y in points[1:]:
        parts.append(f"L {format_num(x, decimals)} {format_num(y, decimals)}")
    if close_path:
        parts.append("Z")
    return " ".join(parts)


def noisy_blob_polygon(cx: float, cy: float, radius: float, jitter: float, seed: int) -> list[tuple[float, float]]:
    rng = random.Random(seed)
    pts: list[tuple[float, float]] = []
    segments = max(9, int(radius * 1.8))
    for i in range(segments):
        t = (2.0 * math.pi * i) / segments
        wave = 1.0 + 0.16 * math.sin((t * 3.0) + rng.uniform(-0.8, 0.8)) + 0.10 * math.sin((t * 5.0) + rng.uniform(-0.8, 0.8))
        rr = radius * wave + rng.uniform(-jitter, jitter)
        pts.append((cx + math.cos(t) * rr, cy + math.sin(t) * rr))
    return pts


def capsule_polygon(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    r0: float,
    r1: float,
    steps: int = 8,
) -> list[tuple[float, float]]:
    dx = x1 - x0
    dy = y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-6:
        rr = max(r0, r1)
        return noisy_blob_polygon(x0, y0, rr, rr * 0.22, seed=int((x0 * 1009) + (y0 * 917)))

    nx = -dy / length
    ny = dx / length

    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t = i / steps
        px = x0 + dx * t
        py = y0 + dy * t
        rr = r0 + (r1 - r0) * t
        left.append((px + nx * rr, py + ny * rr))
        right.append((px - nx * rr, py - ny * rr))
    return left + list(reversed(right))


def drip_ribbon_polygon(points: list[tuple[float, float]], w0: float, w1: float) -> list[tuple[float, float]]:
    n = len(points)
    if n < 2:
        return []

    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for i, (px, py) in enumerate(points):
        if i == 0:
            tx = points[1][0] - px
            ty = points[1][1] - py
        elif i == n - 1:
            tx = px - points[n - 2][0]
            ty = py - points[n - 2][1]
        else:
            tx = points[i + 1][0] - points[i - 1][0]
            ty = points[i + 1][1] - points[i - 1][1]

        tl = math.hypot(tx, ty)
        if tl < 1e-6:
            nx, ny = 0.0, 1.0
        else:
            nx, ny = -ty / tl, tx / tl

        t = i / max(1, n - 1)
        ww = w0 + (w1 - w0) * t
        left.append((px + nx * ww, py + ny * ww))
        right.append((px - nx * ww, py - ny * ww))

    return left + list(reversed(right))


def generate_splat_primitives(gray: np.ndarray, gx: np.ndarray, gy: np.ndarray, mag: np.ndarray, cfg: Config) -> list[SplatPrimitive]:
    h, w = gray.shape
    dark = np.clip(1.0 - gray, 0.0, 1.0)
    edge = mag / (mag.max() + 1e-8)
    rng_np = np.random.default_rng(cfg.seed)
    rng_py = random.Random(cfg.seed)

    weights = (dark ** 1.55) * (0.28 + cfg.splat_edge_bias * (edge ** 1.45))
    weights += 1e-9
    weights /= weights.sum()

    starts = sample_points(weights, cfg.strokes, rng_np)
    out: list[SplatPrimitive] = []

    for i, (sx, sy) in enumerate(starts):
        x = int(sx)
        y = int(sy)
        if not (0 <= x < w and 0 <= y < h):
            continue
        d = float(dark[y, x])
        if d < max(0.04, cfg.min_darkness):
            continue
        e = float(edge[y, x])

        base_r = max(0.7, cfg.step * (0.18 + d * 0.82 + e * 0.52))
        main_poly = noisy_blob_polygon(float(x), float(y), base_r, jitter=base_r * 0.26, seed=(cfg.seed * 1000003 + i))
        main_poly = [(clamp(px, 0, w - 1), clamp(py, 0, h - 1)) for px, py in main_poly]
        fill_opacity = clamp(0.06 + (d ** 1.5) * 0.92, 0.06, 1.0)
        out.append(SplatPrimitive(d=polygon_to_svg_d(main_poly, cfg.simplify_decimals, True), fill=True, opacity=fill_opacity))

        sat_count = min(cfg.splat_satellites, int(1 + (d * 1.6) + (e * 1.5)))
        for _ in range(sat_count):
            a = rng_py.uniform(0.0, 2.0 * math.pi)
            dist = base_r * rng_py.uniform(1.05, 2.6)
            cx = clamp(x + math.cos(a) * dist, 0, w - 1)
            cy = clamp(y + math.sin(a) * dist, 0, h - 1)
            rr = base_r * rng_py.uniform(0.18, 0.56)
            sat_poly = noisy_blob_polygon(cx, cy, rr, jitter=rr * 0.35, seed=rng_py.randrange(1, 2_000_000_000))
            sat_poly = [(clamp(px, 0, w - 1), clamp(py, 0, h - 1)) for px, py in sat_poly]
            sat_opacity = clamp(fill_opacity * rng_py.uniform(0.55, 0.9), 0.08, 0.9)
            out.append(SplatPrimitive(d=polygon_to_svg_d(sat_poly, cfg.simplify_decimals, True), fill=True, opacity=sat_opacity))

            if dist <= (base_r * 2.15):
                bridge_poly = capsule_polygon(float(x), float(y), cx, cy, base_r * 0.32, rr * 0.58, steps=6)
                bridge_poly = [(clamp(px, 0, w - 1), clamp(py, 0, h - 1)) for px, py in bridge_poly]
                bridge_opacity = clamp((fill_opacity + sat_opacity) * 0.42, 0.06, 0.8)
                out.append(SplatPrimitive(d=polygon_to_svg_d(bridge_poly, cfg.simplify_decimals, True), fill=True, opacity=bridge_opacity))

        drip_count = 0
        if e > 0.20 and d > 0.22:
            drip_count = min(cfg.splat_drips, int(e * 2.4) + (1 if d > 0.62 else 0))
        if drip_count > 0:
            angle = math.atan2(gy[y, x], gx[y, x]) + math.pi / 2.0
            for _ in range(drip_count):
                da = angle + rng_py.uniform(-0.65, 0.65)
                segs = max(2, int(2 + d * 5 + e * 4))
                px = float(x)
                py = float(y)
                drip: list[tuple[float, float]] = [(px, py)]
                for _ in range(segs):
                    step_len = cfg.step * rng_py.uniform(0.7, 1.9)
                    px = clamp(px + math.cos(da) * step_len + rng_py.uniform(-0.7, 0.7), 0, w - 1)
                    py = clamp(py + math.sin(da) * step_len + rng_py.uniform(-0.7, 0.7), 0, h - 1)
                    drip.append((px, py))
                    da += rng_py.uniform(-0.18, 0.18)
                drip_opacity = clamp(0.12 + d * 0.6, 0.08, 0.9)

                ww0 = max(0.35, base_r * rng_py.uniform(0.28, 0.55))
                ww1 = max(0.12, ww0 * rng_py.uniform(0.20, 0.42))
                ribbon = drip_ribbon_polygon(drip, ww0, ww1)
                if ribbon:
                    ribbon = [(clamp(rx, 0, w - 1), clamp(ry, 0, h - 1)) for rx, ry in ribbon]
                    out.append(SplatPrimitive(d=polygon_to_svg_d(ribbon, cfg.simplify_decimals, True), fill=True, opacity=drip_opacity))

                end_r = max(0.2, ww1 * rng_py.uniform(1.1, 2.0))
                end_poly = noisy_blob_polygon(drip[-1][0], drip[-1][1], end_r, jitter=end_r * 0.25, seed=rng_py.randrange(1, 2_000_000_000))
                end_poly = [(clamp(ex, 0, w - 1), clamp(ey, 0, h - 1)) for ex, ey in end_poly]
                out.append(SplatPrimitive(d=polygon_to_svg_d(end_poly, cfg.simplify_decimals, True), fill=True, opacity=clamp(drip_opacity * 0.9, 0.08, 0.9)))

    return out


def write_splat_svg(output_path: str, width: int, height: int, prims: list[SplatPrimitive], background: bool) -> None:
    title = escape("Paint splat SVG")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'version="1.1">\n'
        )
        f.write(f'  <title>{title}</title>\n')
        if background:
            f.write(f'  <rect x="0" y="0" width="{width}" height="{height}" fill="white"/>\n')

        f.write('  <g fill="black" stroke="none">\n')
        for prim in prims:
            op = format_num(prim.opacity, 3)
            f.write(f'    <path d="{escape(prim.d)}" opacity="{op}"/>\n')
        f.write("  </g>\n")
        f.write("</svg>\n")


def write_svg(
    output_path: str,
    width: int,
    height: int,
    paths: list[str],
    stroke_width: float,
    background: bool,
) -> None:
    title = escape("Squiggle line SVG")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'version="1.1">\n'
        )
        f.write(f'  <title>{title}</title>\n')
        if background:
            f.write(f'  <rect x="0" y="0" width="{width}" height="{height}" fill="white"/>\n')
        f.write(
            f'  <g fill="none" stroke="black" '
            f'stroke-width="{stroke_width}" '
            f'stroke-linecap="round" '
            f'stroke-linejoin="round">\n'
        )
        for d in paths:
            f.write(f'    <path d="{escape(d)}"/>\n')
        f.write("  </g>\n")
        f.write("</svg>\n")


def generate_paths(gray: np.ndarray, gx: np.ndarray, gy: np.ndarray, mag: np.ndarray, cfg: Config) -> list[str]:
    rng_np = np.random.default_rng(cfg.seed)
    rng_py = random.Random(cfg.seed)
    h, w = gray.shape
    paths: list[str] = []
    coverage = np.zeros((h, w), dtype=np.uint8)

    edge = mag / (mag.max() + 1e-8)
    residual_dark = np.clip(1.0 - gray, 0.0, 1.0)
    work_gray = gray.copy()

    pass_profiles = [
        {"fraction": 0.52, "len_scale": 1.90, "step_scale": 1.4, "edge_mix": 0.14, "dark_power": 1.22, "lighten": 0.068, "min_dark": 0.038, "straight_bias": 0.78, "long_line_bias": 0.58},
        {"fraction": 0.33, "len_scale": 1.15, "step_scale": 1.0, "edge_mix": 0.30, "dark_power": 1.42, "lighten": 0.048, "min_dark": 0.022, "straight_bias": 0.62, "long_line_bias": 0.32},
        {"fraction": 0.15, "len_scale": 0.62, "step_scale": 0.74, "edge_mix": 0.56, "dark_power": 1.70, "lighten": 0.034, "min_dark": 0.012, "straight_bias": 0.36, "long_line_bias": 0.14},
    ]

    pass_targets = [int(cfg.strokes * p["fraction"]) for p in pass_profiles]
    if pass_targets:
        pass_targets[-1] = cfg.strokes - sum(pass_targets[:-1])

    total_generated = 0
    for pass_idx, profile in enumerate(pass_profiles):
        target = pass_targets[pass_idx]
        if target <= 0:
            continue

        accepted = 0
        attempts = 0
        max_attempts = target * 8
        starts_cache = np.zeros((0, 2), dtype=np.int32)
        cache_idx = 0
        active_points: list[tuple[float, float]] = []
        chain_chunks = 0
        active_x = -1
        active_y = -1

        print(f"pass {pass_idx + 1}/3: target {target} paths")

        while accepted < target and attempts < max_attempts:
            x = -1
            y = -1
            use_active_start = False
            if cfg.continuous and active_points and chain_chunks < cfg.continuous_chain_max:
                ix = int(clamp(round(active_x), 0, w - 1))
                iy = int(clamp(round(active_y), 0, h - 1))
                endpoint_dark = residual_dark[iy, ix]
                local_cov_cap = cfg.max_pixel_coverage + (1 if endpoint_dark > 0.35 else 0)
                if endpoint_dark > (profile["min_dark"] * 0.95) and coverage[iy, ix] < local_cov_cap:
                    x = ix
                    y = iy
                    use_active_start = True

            if not use_active_start and active_points:
                paths.append(path_to_svg_d(active_points, cfg.simplify_decimals))
                active_points = []
                chain_chunks = 0

            if not use_active_start:
                if cache_idx >= len(starts_cache):
                    pass_dark_bias = max(0.2, cfg.dark_bias * profile["dark_power"])
                    pass_edge_bias = float(np.clip(cfg.edge_bias * profile["edge_mix"], 0.0, 1.0))
                    dynamic_weights = build_residual_sampling_map(
                        residual_dark=residual_dark,
                        edge=edge,
                        coverage=coverage,
                        dark_bias=pass_dark_bias,
                        edge_bias=pass_edge_bias,
                        min_darkness=cfg.min_darkness,
                        max_pixel_coverage=cfg.max_pixel_coverage,
                    )
                    batch_size = min(6000, max(1200, (target - accepted) * 2))
                    starts_cache = sample_points(dynamic_weights, batch_size, rng_np)
                    cache_idx = 0

                x = int(starts_cache[cache_idx, 0])
                y = int(starts_cache[cache_idx, 1])
                cache_idx += 1
            attempts += 1

            if not (0 <= x < w and 0 <= y < h):
                continue

            local_start_dark = residual_dark[y, x]
            local_max_cov = cfg.max_pixel_coverage + (1 if local_start_dark > 0.35 else 0) + (1 if local_start_dark > 0.62 else 0)
            if coverage[y, x] >= local_max_cov:
                continue

            dark = local_start_dark
            if dark <= profile["min_dark"]:
                if use_active_start and active_points:
                    paths.append(path_to_svg_d(active_points, cfg.simplify_decimals))
                    active_points = []
                    chain_chunks = 0
                continue

            segs = max(3, int(cfg.stroke_len * profile["len_scale"] * (0.40 + dark * 1.10)))
            step_size = max(1, int(round(cfg.step * profile["step_scale"])))

            if cfg.style == "hybrid":
                pts, chunk_chainable = choose_hybrid_mark(
                    x0=x,
                    y0=y,
                    gray=work_gray,
                    gx=gx,
                    gy=gy,
                    residual_dark=residual_dark,
                    edge=edge,
                    coverage=coverage,
                    max_segments=segs,
                    step=step_size,
                    max_pixel_coverage=cfg.max_pixel_coverage,
                    straight_bias=profile["straight_bias"],
                    long_line_bias=profile["long_line_bias"],
                    rng=rng_py,
                )
            else:
                pts = guided_squiggle(
                    x0=x,
                    y0=y,
                    gray=work_gray,
                    gx=gx,
                    gy=gy,
                    residual_dark=residual_dark,
                    edge=edge,
                    coverage=coverage,
                    max_segments=segs,
                    step=step_size,
                    max_pixel_coverage=cfg.max_pixel_coverage,
                    straight_bias=profile["straight_bias"],
                    long_line_bias=profile["long_line_bias"],
                    rng=rng_py,
                )
                chunk_chainable = True

            if len(pts) < 2:
                if use_active_start and active_points:
                    paths.append(path_to_svg_d(active_points, cfg.simplify_decimals))
                    active_points = []
                    chain_chunks = 0
                continue

            px, py = path_pixel_coords(pts, w, h)
            if px.size == 0:
                if use_active_start and active_points:
                    paths.append(path_to_svg_d(active_points, cfg.simplify_decimals))
                    active_points = []
                    chain_chunks = 0
                continue

            prior = coverage[py, px]
            path_dark_mean = float(np.mean(residual_dark[py, px]))
            overlap_ratio = float(np.mean(prior > 0))
            path_len_factor = min(1.0, float(px.size) / max(1.0, float(cfg.stroke_len * cfg.step * 2.2)))
            allowed_overlap = min(0.98, cfg.max_overlap_ratio + (0.35 * path_dark_mean) + (0.18 * path_len_factor))
            if overlap_ratio > allowed_overlap:
                if use_active_start and active_points:
                    paths.append(path_to_svg_d(active_points, cfg.simplify_decimals))
                    active_points = []
                    chain_chunks = 0
                continue

            mx, my = expand_pixel_coords(px, py, w, h, cfg.coverage_radius)
            touched = coverage[my, mx]
            coverage[my, mx] = np.minimum(cfg.max_pixel_coverage, touched + 1)

            lighten = profile["lighten"]
            residual_dark[py, px] = np.maximum(0.0, residual_dark[py, px] - lighten)
            work_gray[py, px] = 1.0 - residual_dark[py, px]
            if cfg.coverage_radius > 0:
                residual_dark[my, mx] = np.maximum(0.0, residual_dark[my, mx] - (lighten * 0.35))
                work_gray[my, mx] = 1.0 - residual_dark[my, mx]

            if cfg.continuous and chunk_chainable:
                if active_points:
                    active_points.extend(pts[1:])
                else:
                    active_points = list(pts)
                last_x, last_y = pts[-1]
                active_x = last_x
                active_y = last_y
                chain_chunks += 1
            else:
                if active_points:
                    paths.append(path_to_svg_d(active_points, cfg.simplify_decimals))
                    active_points = []
                    chain_chunks = 0
                paths.append(path_to_svg_d(pts, cfg.simplify_decimals))
            accepted += 1
            total_generated += 1

            if total_generated > 0 and total_generated % 10000 == 0:
                print(f"generated {total_generated}/{cfg.strokes} paths")

        if active_points:
            paths.append(path_to_svg_d(active_points, cfg.simplify_decimals))

        print(f"pass {pass_idx + 1}/3 accepted {accepted}/{target} after {attempts} attempts")

    return paths


def main() -> None:
    input_path, output_path, cfg = parse_args()

    if output_path is None:
        import os
        base = os.path.splitext(os.path.basename(input_path))[0]
        output_path = os.path.join(os.getcwd(), base + ".svg")

    print("loading image...")
    gray = load_grayscale(input_path, cfg.width)

    print("applying contrast...")
    if cfg.contrast is not None:
        gray = enhance_tone(gray, cfg.contrast)
    else:
        gray = auto_contrast(gray)

    if cfg.gamma is not None:
        gray = apply_gamma(gray, cfg.gamma)

    if cfg.clahe_clip_limit > 0:
        gray = local_contrast_enhance(gray, cfg.clahe_tile_size, cfg.clahe_clip_limit / 4.0)

    if cfg.unsharp_amount > 0:
        gray = unsharp_mask(gray, cfg.unsharp_amount)

    gray = gaussian_blur_array(gray, cfg.blur_radius)
    h, w = gray.shape

    print("computing gradients...")
    gx, gy, mag = sobel(gray)

    if cfg.style == "ascii":
        print("generating ASCII glyphs...")
        marks, method, corr, mse = generate_ascii_marks(gray, gx, gy, mag, cfg)
        print(f"selected ASCII method: {method} (corr={corr:.4f}, mse={mse:.4f})")
        print(f"writing ASCII SVG with {len(marks)} glyphs...")
        write_ascii_svg(
            output_path=output_path,
            width=w,
            height=h,
            marks=marks,
            background=cfg.background,
            font_family=cfg.ascii_font,
            stroke_text=cfg.ascii_stroke_text,
            font_file=cfg.ascii_font_file,
            text_as_paths=cfg.ascii_text_as_paths,
        )
        print(f"done: {output_path}")
        return

    if cfg.style == "splat":
        print("generating paint splats...")
        splats = generate_splat_primitives(gray, gx, gy, mag, cfg)
        print(f"writing splat SVG with {len(splats)} primitives...")
        write_splat_svg(
            output_path=output_path,
            width=w,
            height=h,
            prims=splats,
            background=cfg.background,
        )
        print(f"done: {output_path}")
        return

    print("generating SVG paths...")
    paths = generate_paths(gray, gx, gy, mag, cfg)

    print(f"writing SVG with {len(paths)} paths...")
    write_svg(
        output_path=output_path,
        width=w,
        height=h,
        paths=paths,
        stroke_width=cfg.stroke_width,
        background=cfg.background,
    )

    print(f"done: {output_path}")


if __name__ == "__main__":
    main()
