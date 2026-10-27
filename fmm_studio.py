"""
Universal Plotter Studio v5.5.2
Optimized for Silhouette Cameo 5 / AxiDraw — ballpoint & fineliner pens.

v5.5.2 fixes over v5.5.1:
  - CMYK export: channels (ink density) are converted to brightness before
    Blueprint / Squiggle / Isoline / Fast Marching / Streamlines see them.
  - Export paths use pixel-unit settings scaled by the real preview->export
    resolution ratio, so the SVG matches what the preview showed.
  - Export (paths, wobble, pen passes, vpype, CMYK ZIP) runs only when
    "Build export" is clicked; the result is kept in st.session_state.
  - Perlin wobble is vectorised and also shown in the preview.
  - Continuous Line: cKDTree nearest-neighbour chain + time-limited 2-opt.
  - Image loading: EXIF rotation, 16-bit greyscale, gray+alpha, palette alpha.
  - Seeded stippling, true Voronoi-cell Lloyd centroids, pen-up travel in the
    time estimate, CMYK warning for engines that cannot use it.

v5.5 additions over v5.4:

  FMM ENGINE UPGRADE:
  - Manual seed point placement (X/Y % sliders) — reproduces Contour-V style
    diamond/chevron portrait rings by placing the wave origin deliberately
    (e.g. between the eyebrows). Auto-detect remains as the default.
  - Contour smoothing mode toggle: Angular (sharp, follows brightness edges)
    vs Smooth (Gaussian-softened contours, more organic).

  NEW POST-PROCESS:
  - Perlin noise wobble — applies coherent noise displacement to any engine's
    output paths, giving hand-drawn organic quality to clean computer lines.
    Inspired by LingDong-'s linedraw Perlin approach. Noise scale and amplitude
    are independently controllable. Applied before vpype and pen passes.

  NEW ENGINES:
  - Needles (Oriented Dashes) — short line segments placed in a grid, oriented
    by the local image gradient direction. Dark areas = more/longer dashes,
    light areas = sparse short marks. Very different aesthetic from stippling —
    marks have direction and follow the image's structural flow.
  - Continuous Line (TSP-style) — single unbroken line visiting all stipple
    points using a greedy nearest-neighbour TSP approximation. The plotter
    never lifts the pen. Inspired by PINTR and StringyPlotter.

  NEW EXPORT:
  - CMYK Layer Export — splits the uploaded colour image into C, M, Y, K
    channels, runs the selected engine on each channel independently, and
    exports a ZIP containing four SVG files (one per ink colour). Enables
    multi-pen colour plots. Available on all engines.
"""

import hashlib
import io
import math
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections import deque
from io import BytesIO, StringIO

import numpy as np
import plotly.graph_objects as go
import skfmm
import streamlit as st
from PIL import Image, ImageOps
from scipy.spatial import cKDTree
from skimage import color, exposure, feature, filters, measure, transform
from skimage.morphology import closing, skeletonize, square

from centreline import trace_centrelines

APP_VERSION = "5.5.2"
STIPPLE_SEED = 12345          # fixed RNG seed -> repeatable stipple/TSP layouts
TSP_TIME_PREVIEW = 2.0        # seconds of 2-opt while tuning
TSP_TIME_EXPORT = 10.0        # seconds of 2-opt for the final export

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(layout="wide", page_title="Universal Plotter Studio")
st.title(f"🎨 Universal Plotter Studio v{APP_VERSION}")
st.markdown("Cameo 5 / AxiDraw — Ballpoint & Fineliner Engine")

# ─────────────────────────────────────────────
# SIDEBAR — GLOBAL SETTINGS
# ─────────────────────────────────────────────
st.sidebar.header("1. Global Physical Settings")
target_width_mm = st.sidebar.number_input(
    "Physical Width (mm)", value=200, min_value=10, max_value=600
)
plot_speed_mms = st.sidebar.slider("Plot Speed (mm/s)", 10, 200, 40)
min_line_len   = st.sidebar.slider(
    "Min Line Length (points)", 1, 100, 5,
    help="Remove paths shorter than this. Raise to cut noise; lower for fine detail."
)

# ─────────────────────────────────────────────
# SIDEBAR — IMAGE MASKING
# ─────────────────────────────────────────────
st.sidebar.header("2. Image Mask (Crop)")
use_mask = st.sidebar.checkbox(
    "Enable Mask", value=False,
    help="Crop the image before processing to exclude signatures, borders, or background."
)
if use_mask:
    mask_x = st.sidebar.slider("Left offset (%)",  0, 90,  0)
    mask_y = st.sidebar.slider("Top offset (%)",   0, 90,  0)
    mask_w = st.sidebar.slider("Width (%)",        5, 100, 100)
    mask_h = st.sidebar.slider("Height (%)",       5, 100, 100)
else:
    mask_x = mask_y = 0
    mask_w = mask_h = 100

# ─────────────────────────────────────────────
# SIDEBAR — ENGINE
# ─────────────────────────────────────────────
st.sidebar.header("3. Choose Art Engine")
mode = st.sidebar.radio(
    "Engine",
    [
        "Blueprint (Sketch Trace)",
        "Sobel Edge (Outline)",
        "Squiggle (Scanline Shading)",
        "Isoline (Tonal Contour)",
        "Fast Marching (Topographic)",
        "Line Sketch (Streamlines)",
        "Voronoi Stippling",
        "Needles (Oriented Dashes)",
        "Continuous Line (TSP)",
    ],
    help=(
        "Blueprint/Sobel: best for existing drawings. "
        "Squiggle: scanline waves. Isoline: tonal contours. "
        "Fast Marching: topographic rings with manual seed. "
        "Streamlines: gradient flow lines. Voronoi Stippling: dot art. "
        "Needles: oriented gradient dashes. Continuous Line: single unbroken TSP path."
    )
)

uploaded_file = st.sidebar.file_uploader("Upload Image", type=["jpg", "jpeg", "png"])

# ─────────────────────────────────────────────
# SIDEBAR — PERLIN NOISE WOBBLE
# ─────────────────────────────────────────────
st.sidebar.header("4. Perlin Noise Wobble")
use_wobble = st.sidebar.checkbox(
    "Enable Noise Wobble", value=False,
    help="Apply coherent Perlin-style noise displacement to output paths — "
         "gives hand-drawn organic quality to clean computer lines. "
         "Inspired by LingDong-'s linedraw technique."
)
if use_wobble:
    wobble_scale = st.sidebar.slider("Noise Scale", 0.001, 0.05, 0.008, step=0.001,
        help="Spatial frequency of the noise. Lower = large smooth waves. "
             "Higher = tight rapid wobble. Defined at preview resolution; "
             "scaled automatically on export.")
    wobble_amp   = st.sidebar.slider("Wobble Amplitude (px)", 0.5, 15.0, 3.0, step=0.5,
        help="Maximum displacement in pixels at preview resolution (scaled "
             "automatically on export). Higher = more dramatic hand-drawn look.")
else:
    wobble_scale = 0.008
    wobble_amp   = 3.0

# ─────────────────────────────────────────────
# SIDEBAR — VPYPE
# ─────────────────────────────────────────────
st.sidebar.header("5. vpype Export Pipeline")
_vpype_bin       = shutil.which("vpype")
_vpype_available = _vpype_bin is not None

if _vpype_available:
    st.sidebar.success("✅ vpype detected")
    use_vpype = st.sidebar.toggle(
        "Run vpype pipeline on export", value=False,
        help="Turn ON before downloading your final SVG. Keep OFF while tuning."
    )
    if use_vpype:
        vpype_linemerge    = st.sidebar.checkbox("linemerge (join nearby endpoints)", value=True)
        vpype_merge_tol    = st.sidebar.slider("Merge tolerance (mm)", 0.05, 1.0, 0.1, step=0.05)
        vpype_linesort     = st.sidebar.checkbox("linesort (minimise pen travel)", value=True)
        vpype_two_opt      = st.sidebar.checkbox("  └ Two-Opt passes", value=True)
        vpype_reloop       = st.sidebar.checkbox("reloop (randomise seam positions)", value=True)
        vpype_linesimplify = st.sidebar.checkbox("linesimplify (remove redundant nodes)", value=True)
    else:
        vpype_linemerge = vpype_linesort = vpype_two_opt = True
        vpype_reloop = vpype_linesimplify = True
        vpype_merge_tol = 0.1
        st.sidebar.caption("💡 Turn ON before your final export.")
else:
    st.sidebar.warning(
        "vpype not found.\n\n"
        "```\npipx install vpype --python python3.12\n```"
    )
    use_vpype = vpype_linemerge = vpype_linesort = vpype_two_opt = False
    vpype_reloop = vpype_linesimplify = False
    vpype_merge_tol = 0.1

# ─────────────────────────────────────────────
# SIDEBAR — PEN PASSES
# ─────────────────────────────────────────────
st.sidebar.header("6. Pen Passes (Multi-pass Ink)")
use_pen_passes = st.sidebar.checkbox(
    "Enable Pen Passes", value=False,
    help="Duplicate dark paths N times so the plotter inks over them repeatedly."
)
if use_pen_passes:
    pen_passes      = st.sidebar.slider("Extra passes", 1, 4, 1)
    pen_pass_thresh = st.sidebar.slider("Shadow threshold", 0.1, 0.9, 0.4, step=0.05)

# ─────────────────────────────────────────────
# SIDEBAR — CMYK EXPORT
# ─────────────────────────────────────────────
st.sidebar.header("7. CMYK Layer Export")
use_cmyk = st.sidebar.checkbox(
    "Enable CMYK Export", value=False,
    help="Split the colour image into C, M, Y, K channels and run the engine on each. "
         "Exports a ZIP with four SVG files for multi-pen colour plotting. "
         "Uses the current engine settings for all four channels."
)


# ─────────────────────────────────────────────
# PERLIN NOISE
# Simple 2D gradient noise (no extra deps — pure numpy)
# ─────────────────────────────────────────────

def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)

def _lerp(a, b, t):
    return a + t * (b - a)

@st.cache_data(show_spinner=False)
def _make_perlin_grid(seed, grid_w, grid_h):
    """Pre-compute a random gradient grid for coherent noise."""
    rng = np.random.default_rng(seed)
    angles = rng.uniform(0, 2 * math.pi, (grid_h + 1, grid_w + 1))
    gx = np.cos(angles)
    gy = np.sin(angles)
    return gx, gy

def _perlin_noise_2d(x, y, gx, gy):
    """Sample coherent noise at arrays x, y using a precomputed gradient grid."""
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    sx, sy = _fade(x - x0), _fade(y - y0)
    max_ix, max_iy = gx.shape[1] - 1, gx.shape[0] - 1

    def _dot(ix, iy):
        ix = np.clip(ix, 0, max_ix)
        iy = np.clip(iy, 0, max_iy)
        return gx[iy, ix] * (x - ix) + gy[iy, ix] * (y - iy)

    n00 = _dot(x0,     y0)
    n10 = _dot(x0 + 1, y0)
    n01 = _dot(x0,     y0 + 1)
    n11 = _dot(x0 + 1, y0 + 1)
    return _lerp(_lerp(n00, n10, sx), _lerp(n01, n11, sx), sy)


def apply_noise_wobble(paths, scale, amplitude, img_shape):
    """
    Displace each point in each path by coherent Perlin-style noise.
    Two independent noise fields (different seeds) displace x and y separately.
    Vectorised: all paths are concatenated, displaced in one numpy pass, and
    split back.
    """
    if amplitude <= 0 or not paths:
        return paths

    h, w = img_shape
    grid_w = max(2, int(w * scale) + 2)
    grid_h = max(2, int(h * scale) + 2)
    gx1, gy1 = _make_perlin_grid(42,  grid_w, grid_h)
    gx2, gy2 = _make_perlin_grid(137, grid_w, grid_h)

    lens = [len(p) for p in paths]
    allpts = np.concatenate([np.asarray(p, dtype=float) for p in paths])
    r, c = allpts[:, 0], allpts[:, 1]
    nx, ny = c * scale, r * scale
    dx = _perlin_noise_2d(nx, ny, gx1, gy1) * amplitude
    dy = _perlin_noise_2d(nx, ny, gx2, gy2) * amplitude
    out = np.column_stack([np.clip(r + dy, 0, h - 1), np.clip(c + dx, 0, w - 1)])
    return np.split(out, np.cumsum(lens)[:-1])


# ─────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────

def _apply_mask(img, mx, my, mw, mh):
    h, w = img.shape[:2]
    x0 = int(w * mx / 100)
    y0 = int(h * my / 100)
    x1 = min(w, x0 + int(w * mw / 100))
    y1 = min(h, y0 + int(h * mh / 100))
    return img[y0:y1, x0:x1]


def _cap_image(img, max_px):
    h, w = img.shape[:2]
    if w > max_px:
        img = transform.rescale(img, max_px / w, anti_aliasing=True,
                                channel_axis=2 if img.ndim == 3 else None)
    return img


def _odd(n):
    """Round to the nearest odd integer >= 3 (for threshold_local block sizes)."""
    n = int(round(n))
    n = n if n % 2 == 1 else n + 1
    return max(3, n)


def _scale_weld(weld, s):
    """Scale a morphological closing size; 1 means 'off' and stays off."""
    return 1 if weld <= 1 else max(2, int(round(weld * s)))


def _effective_scale(file_bytes, mk, tune_px, export_px):
    """
    Real export/preview width ratio. _cap_image only downsizes, so a source
    narrower than EXPORT_PX (or TUNE_PX) is not resized to those widths and the
    ratio is then smaller than EXPORT_PX / TUNE_PX.
    """
    mx, my, mw, mh = mk
    W = _load_and_greyscale(file_bytes).shape[1]
    x0 = int(W * mx / 100)
    x1 = min(W, x0 + int(W * mw / 100))
    wc = max(1, x1 - x0)
    return min(wc, export_px) / min(wc, tune_px)


def _nearest_neighbour_sort(paths):
    if len(paths) <= 1:
        return paths
    remaining    = list(paths)
    sorted_paths = [remaining.pop(0)]
    cur          = sorted_paths[-1][-1]
    while remaining:
        best_idx, best_dist, best_rev = 0, math.inf, False
        for i, p in enumerate(remaining):
            d_s = (p[0][0]  - cur[0])**2 + (p[0][1]  - cur[1])**2
            d_e = (p[-1][0] - cur[0])**2 + (p[-1][1] - cur[1])**2
            d, rev = (d_s, False) if d_s <= d_e else (d_e, True)
            if d < best_dist:
                best_dist, best_idx, best_rev = d, i, rev
        chosen = remaining.pop(best_idx)
        if best_rev:
            chosen = chosen[::-1]
        sorted_paths.append(chosen)
        cur = sorted_paths[-1][-1]
    return sorted_paths


def _pen_lift_distance(paths):
    total = 0.0
    for i in range(len(paths) - 1):
        ex, ey = paths[i][-1][1],  paths[i][-1][0]
        sx, sy = paths[i+1][0][1], paths[i+1][0][0]
        total += math.sqrt((sx - ex)**2 + (sy - ey)**2)
    return total


def _apply_pen_passes(paths, img_gray, passes, threshold, img_shape):
    if passes < 1 or img_gray is None:
        return paths
    h, w = img_shape
    result = []
    for p in paths:
        result.append(p)
        rows = np.clip(p[:, 0].astype(int), 0, h - 1)
        cols = np.clip(p[:, 1].astype(int), 0, w - 1)
        darkness = 1.0 - img_gray[rows, cols].mean()
        if darkness >= threshold:
            for _ in range(passes):
                result.append(p)
    return result


def generate_svg(paths, width_px, height_px, target_width_mm, stroke_width=0.5):
    scale            = target_width_mm / width_px
    target_height_mm = height_px * scale
    svg = StringIO()
    svg.write(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{target_width_mm:.2f}mm" height="{target_height_mm:.2f}mm" '
        f'viewBox="0 0 {width_px} {height_px}">\n'
    )
    for path in paths:
        d = "M " + " L ".join(f"{pt[1]:.2f},{pt[0]:.2f}" for pt in path)
        svg.write(
            f'  <path d="{d}" fill="none" stroke="black" '
            f'stroke-width="{stroke_width}" '
            f'vector-effect="non-scaling-stroke" '
            f'stroke-linecap="round" stroke-linejoin="round" />\n'
        )
    svg.write("</svg>")
    return svg.getvalue()


def run_vpype_pipeline(svg_str, linemerge, merge_tol, linesort,
                       two_opt, reloop, linesimplify):
    with tempfile.TemporaryDirectory() as tmpdir:
        in_path  = os.path.join(tmpdir, "input.svg")
        out_path = os.path.join(tmpdir, "output.svg")
        with open(in_path, "w", encoding="utf-8") as f:
            f.write(svg_str)
        cmd = ["vpype", "read", in_path]
        if linemerge:
            cmd += ["linemerge", f"--tolerance={merge_tol}mm"]
        if linesort:
            cmd += ["linesort", "--two-opt"] if two_opt else ["linesort"]
        if reloop:
            cmd += ["reloop"]
        if linesimplify:
            cmd += ["linesimplify"]
        cmd += ["write", out_path]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                return None, f"vpype error: {result.stderr.strip()}"
            with open(out_path, "r", encoding="utf-8") as f:
                return f.read(), "ok"
        except subprocess.TimeoutExpired:
            return None, "vpype timed out (>180 s)."
        except FileNotFoundError:
            return None, "vpype binary not found."
        except Exception as e:
            return None, f"Unexpected vpype error: {e}"


def _plotly_traces(fig, paths, h, colour, width):
    for p in paths:
        fig.add_trace(go.Scatter(
            x=p[:, 1], y=h - p[:, 0], mode="lines",
            line=dict(color=colour, width=width), showlegend=False,
        ))


def _plotly_dots(fig, points, h, colour, size):
    fig.add_trace(go.Scatter(
        x=points[:, 1], y=h - points[:, 0], mode="markers",
        marker=dict(color=colour, size=size, opacity=0.8), showlegend=False,
    ))


def _path_length_px(paths):
    return float(sum(
        np.sum(np.sqrt(np.sum(np.diff(p, axis=0) ** 2, axis=1))) for p in paths
    ))


def _estimate_plot(paths, width_px, target_width_mm, speed_mms):
    """Returns (draw_mm, pen_up_mm, est_minutes). Pen-up moves are assumed to
    travel at the same speed as drawing moves; 1.2 is the existing
    acceleration allowance."""
    scale = target_width_mm / width_px
    draw_mm = _path_length_px(paths) * scale
    lift_mm = _pen_lift_distance(paths) * scale
    est_min = ((draw_mm + lift_mm) / speed_mms) * 1.2 / 60
    return draw_mm, lift_mm, est_min


def _export_signature(file_bytes, mode, cfg, preview_paths):
    """Fingerprint of everything that changes the export. Engine parameters are
    captured through the (cheap, cached) preview paths they produce."""
    h = hashlib.md5()
    h.update(hashlib.md5(file_bytes).digest())
    h.update(repr((mode, sorted(cfg.items()))).encode())
    for p in preview_paths:
        h.update(np.ascontiguousarray(p).tobytes())
    return h.hexdigest()


def _run_export(get_export, cmyk_engine_fn, cfg, exp_scale):
    """Full-resolution export. Called only when 'Build export' is clicked.
    Returns a plain dict that is stored in st.session_state."""
    with st.spinner("Computing export-resolution paths…"):
        final_paths, (fh, fw), img_gray = get_export()
    n_base = len(final_paths)

    # Wobble: slider values are defined at preview resolution.
    if cfg["use_wobble"]:
        final_paths = apply_noise_wobble(
            final_paths, cfg["wobble_scale"] / exp_scale,
            cfg["wobble_amp"] * exp_scale, (fh, fw)
        )

    extra = 0
    if cfg["use_pen_passes"] and img_gray is not None:
        final_paths = _apply_pen_passes(
            final_paths, img_gray, cfg["pen_passes"], cfg["pen_pass_thresh"], (fh, fw)
        )
        extra = len(final_paths) - n_base

    draw_mm, lift_mm, est_min = _estimate_plot(
        final_paths, fw, cfg["target_width_mm"], cfg["plot_speed_mms"]
    )

    safe_label = (cfg["mode"].lower().replace(" ", "_")
                  .replace("(", "").replace(")", ""))
    raw_svg = generate_svg(final_paths, fw, fh, cfg["target_width_mm"], cfg["stroke_width"])

    bundle = dict(
        safe_label=safe_label, raw_svg=raw_svg, opt_svg=None, vpype_status=None,
        vpype_stages=None, cmyk_zip=None, extra=extra, n_paths=len(final_paths),
        draw_mm=draw_mm, lift_mm=lift_mm, est_min=est_min, export_w=fw, export_h=fh,
    )

    if cfg["use_vpype"]:
        stages = []
        if cfg["vpype_linemerge"]:    stages.append(f"linemerge({cfg['vpype_merge_tol']}mm)")
        if cfg["vpype_linesort"]:     stages.append("linesort" + (" +2opt" if cfg["vpype_two_opt"] else ""))
        if cfg["vpype_reloop"]:       stages.append("reloop")
        if cfg["vpype_linesimplify"]: stages.append("linesimplify")
        with st.spinner(f"Running vpype: {' → '.join(stages)}…"):
            opt_svg, status = run_vpype_pipeline(
                raw_svg, cfg["vpype_linemerge"], cfg["vpype_merge_tol"],
                cfg["vpype_linesort"], cfg["vpype_two_opt"],
                cfg["vpype_reloop"], cfg["vpype_linesimplify"]
            )
        bundle.update(opt_svg=opt_svg, vpype_status=status, vpype_stages=stages)

    if cfg["use_cmyk"] and cmyk_engine_fn is not None:
        with st.spinner("Generating CMYK layers…"):
            bundle["cmyk_zip"] = _generate_cmyk_zip(
                cmyk_engine_fn, fw, fh, cfg["target_width_mm"], cfg["stroke_width"]
            )
    return bundle


def _render_export(bundle, stale):
    """Metrics + download buttons for a built export (survives reruns)."""
    if stale:
        st.warning("⚠️ Settings changed since this export was built. "
                   "Click **Build export** again to refresh it.")
    c1, c2, c3 = st.columns([1, 1, 2])
    c1.metric("Total Draw Distance", f"{bundle['draw_mm'] / 1000:.2f} m")
    c2.metric("Est. Plot Time", f"{bundle['est_min']:.1f} min")
    if bundle["extra"] > 0:
        st.caption(f"✏️ Pen passes: {bundle['extra']:,} extra path duplicates added.")

    label = bundle["safe_label"]
    if bundle["vpype_stages"] is not None:
        if bundle["opt_svg"]:
            st.success(f"✅ vpype: {' → '.join(bundle['vpype_stages'])}")
            c3.download_button(
                "🚀 Download SVG (vpype optimised)", bundle["opt_svg"],
                f"plotter_{label}_vpype.svg", "image/svg+xml", key="dl_svg_vpype")
            with st.expander("Also available: raw SVG (pre-vpype)"):
                st.download_button("Download raw SVG", bundle["raw_svg"],
                    f"plotter_{label}_raw.svg", "image/svg+xml", key="dl_svg_raw")
        else:
            st.warning(f"vpype did not complete: {bundle['vpype_status']}")
            c3.download_button("🚀 Download SVG for Cameo 5", bundle["raw_svg"],
                f"plotter_{label}.svg", "image/svg+xml", key="dl_svg_plain")
    else:
        c3.download_button("🚀 Download SVG for Cameo 5", bundle["raw_svg"],
            f"plotter_{label}.svg", "image/svg+xml", key="dl_svg_plain")

    if bundle["cmyk_zip"]:
        st.download_button(
            "🎨 Download CMYK ZIP (4 SVG layers)", bundle["cmyk_zip"],
            f"plotter_{label}_cmyk.zip", "application/zip", key="dl_cmyk")
        st.caption(
            "CMYK ZIP contains: cyan.svg, magenta.svg, yellow.svg, black.svg — "
            "plot each with the corresponding pen colour. (Channels with no ink are omitted.)"
        )

    st.caption(
        f"{bundle['n_paths']:,} paths · Export: {bundle['export_w']}px · "
        f"Draw: {bundle['draw_mm'] / 1000:.2f} m · "
        f"Pen-up: {bundle['lift_mm'] / 1000:.2f} m"
    )


# ─────────────────────────────────────────────
# CMYK LAYER GENERATION
# ─────────────────────────────────────────────

def _rgb_to_cmyk_channels(file_bytes, mx, my, mw, mh, max_px):
    """
    Split a colour image into C, M, Y, K channels.
    Each channel is INK DENSITY in 0..1 where 1 = maximum ink (so 1 = dark).
    Engines that expect brightness (0 = dark) must be given ``1 - channel``.
    Returns dict of {'C','M','Y','K'} arrays and the image shape.
    """
    img, _alpha = _decode_image(file_bytes)
    if mx != 0 or my != 0 or mw != 100 or mh != 100:
        img = _apply_mask(img, mx, my, mw, mh)
    img = _cap_image(img, max_px)

    if img.ndim == 2:
        # Greyscale source: black ink only
        z = np.zeros_like(img)
        return {'C': z, 'M': z, 'Y': z, 'K': np.clip(1.0 - img, 0, 1)}, img.shape

    R, G, B = img[:, :, 0], img[:, :, 1], img[:, :, 2]
    K = 1.0 - np.maximum(np.maximum(R, G), B)
    denom = 1.0 - K
    denom[denom == 0] = 1.0
    C = (1.0 - R - K) / denom
    M = (1.0 - G - K) / denom
    Y = (1.0 - B - K) / denom
    return {
        'C': np.clip(C, 0, 1),
        'M': np.clip(M, 0, 1),
        'Y': np.clip(Y, 0, 1),
        'K': np.clip(K, 0, 1),
    }, img.shape[:2]


def _cmyk_ink_channels(file_bytes, mk, max_px, min_ink=1e-3):
    """[(name, ink_density)] for channels that actually carry ink."""
    ch, _ = _rgb_to_cmyk_channels(file_bytes, *mk, max_px)
    return [(n, c) for n, c in ch.items() if c.max() >= min_ink]


def _generate_cmyk_zip(engine_fn, w, h, target_width_mm, stroke_width):
    """
    engine_fn() -> {'C': paths, 'M': paths, 'Y': paths, 'K': paths}
    Returns zip bytes or None on error.
    """
    try:
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, paths in engine_fn().items():
                if paths:
                    svg = generate_svg(paths, w, h, target_width_mm, stroke_width)
                    zf.writestr(f"{name.lower()}.svg", svg)
        return buf.getvalue()
    except Exception as e:
        st.warning(f"CMYK export error: {e}")
        return None


# ─────────────────────────────────────────────
# CACHED PREPROCESSING
# ─────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _decode_image(file_bytes):
    """
    Decode with PIL. Returns (img, alpha):
      img   float64 in [0, 1], HxW (grey) or HxWx3 (RGB); transparency is
            composited over white (same as skimage rgba2rgb).
      alpha HxW float in [0, 1], or None when the file has no transparency.
    Applies EXIF orientation and scales by bit depth (not by max value).
    """
    im = ImageOps.exif_transpose(Image.open(BytesIO(file_bytes)))
    mode = im.mode
    if mode in ("I;16", "I;16L", "I;16B", "I;16N", "I"):
        return np.clip(np.asarray(im).astype(np.float64) / 65535.0, 0, 1), None
    if mode == "F":
        return np.clip(np.asarray(im).astype(np.float64), 0, 1), None
    if mode in ("LA", "La"):
        a = np.asarray(im.convert("LA")).astype(np.float64) / 255.0
        gray, alpha = a[..., 0], a[..., 1]
        return gray * alpha + (1.0 - alpha), alpha
    if mode in ("PA", "RGBa") or (mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        mode = "RGBA"
    if mode == "RGBA":
        a = np.asarray(im).astype(np.float64) / 255.0
        alpha = a[..., 3]
        return a[..., :3] * alpha[..., None] + (1.0 - alpha[..., None]), alpha
    if mode == "L":
        return np.asarray(im).astype(np.float64) / 255.0, None
    if mode == "1":
        return np.asarray(im.convert("L")).astype(np.float64) / 255.0, None
    return np.asarray(im.convert("RGB")).astype(np.float64) / 255.0, None


@st.cache_data(show_spinner=False)
def _load_and_greyscale(file_bytes):
    img, _ = _decode_image(file_bytes)
    return color.rgb2gray(img) if img.ndim == 3 else img


@st.cache_data(show_spinner=False)
def _load_masked(file_bytes, mx, my, mw, mh, max_px):
    img = _load_and_greyscale(file_bytes)
    if mx != 0 or my != 0 or mw != 100 or mh != 100:
        img = _apply_mask(img, mx, my, mw, mh)
    return _cap_image(img, max_px)


@st.cache_data(show_spinner=False)
def _load_alpha_mask(file_bytes, mx, my, mw, mh, max_px):
    """
    Returns an alpha/opacity map (1.0 = opaque, 0.0 = fully transparent) matched
    to the same crop + resize pipeline as _load_masked, or None if the source
    image has no alpha channel. Used by 'Skip transparent' (FMM engine).
    """
    _, alpha = _decode_image(file_bytes)
    if alpha is None:
        return None
    if mx != 0 or my != 0 or mw != 100 or mh != 100:
        alpha = _apply_mask(alpha, mx, my, mw, mh)
    return _cap_image(alpha, max_px)


def _blueprint_mask(img, grid_size, contrast_offset, line_weld, pre_sigma=0.6):
    """img is BRIGHTNESS (0 = dark). Returns a boolean ink mask. Centreline
    thinning happens in _contours_blueprint, which needs the full-width mask."""
    pre    = filters.gaussian(img, sigma=pre_sigma)
    thresh = filters.threshold_local(pre, block_size=grid_size,
                                     method="gaussian", offset=contrast_offset)
    binary = pre < thresh
    if line_weld > 1:
        binary = closing(binary, square(line_weld))
    return binary


@st.cache_data(show_spinner=False)
def _preprocess_blueprint(img_bytes, mx, my, mw, mh, max_px,
                           grid_size, contrast_offset, line_weld, pre_sigma=0.6):
    img = _load_masked(img_bytes, mx, my, mw, mh, max_px)
    return img.shape, _blueprint_mask(img, grid_size, contrast_offset,
                                      line_weld, pre_sigma)


@st.cache_data(show_spinner=False)
def _preprocess_sobel(img_bytes, mx, my, mw, mh, max_px,
                      sigma, low_thresh, high_thresh):
    img   = _load_masked(img_bytes, mx, my, mw, mh, max_px)
    edges = feature.canny(img, sigma=sigma,
                          low_threshold=low_thresh, high_threshold=high_thresh)
    return img.shape, edges


@st.cache_data(show_spinner=False)
def _preprocess_squiggle(img_bytes, mx, my, mw, mh, max_px, sigma):
    img = _load_masked(img_bytes, mx, my, mw, mh, max_px)
    return img.shape, filters.gaussian(img, sigma=sigma)


@st.cache_data(show_spinner=False)
def _preprocess_isoline(img_bytes, mx, my, mw, mh, max_px, shadow_boost, sigma):
    img = _load_masked(img_bytes, mx, my, mw, mh, max_px)
    return img.shape, filters.gaussian(exposure.adjust_gamma(img, shadow_boost), sigma=sigma)


@st.cache_data(show_spinner=False)
def _preprocess_fmm(img_bytes, mx, my, mw, mh, max_px,
                    gamma_val, sigma, smooth_mode,
                    seed_auto, seed_x_pct, seed_y_pct, speed_offset=0.02):
    """
    Fast Marching with:
    - smooth_mode: 'angular' (sharp edges) or 'smooth' (Gaussian-softened)
    - manual seed point via seed_x_pct / seed_y_pct (% of image dimensions)
      when seed_auto is False
    - speed_offset: minimum wave speed added to the speed field. Prevents the
      wavefront from stalling in pure-black areas. Higher = less density
      build-up in shadows; lower = more compression/detail in dark regions.
    """
    img = _load_masked(img_bytes, mx, my, mw, mh, max_px)
    h, w = img.shape

    if smooth_mode == "Smooth":
        adj = filters.gaussian(exposure.adjust_gamma(img, gamma_val), sigma=sigma * 2)
    else:
        adj = filters.gaussian(exposure.adjust_gamma(img, gamma_val), sigma=sigma)

    phi = np.ones((h, w))
    if seed_auto:
        blur_big = filters.gaussian(img, sigma=max(h, w) * 0.05)
        dark_idx = np.unravel_index(np.argmin(blur_big), blur_big.shape)
        phi[dark_idx] = 0
    else:
        sr = int(np.clip(seed_y_pct / 100 * h, 0, h - 1))
        sc = int(np.clip(seed_x_pct / 100 * w, 0, w - 1))
        phi[sr, sc] = 0

    return img.shape, skfmm.travel_time(phi, adj + speed_offset)


@st.cache_data(show_spinner=False)
def _preprocess_streamlines(img_bytes, mx, my, mw, mh, max_px, sigma_edge):
    img = _load_masked(img_bytes, mx, my, mw, mh, max_px)
    return img.shape, 1.0 - filters.gaussian(img, sigma=sigma_edge)


@st.cache_data(show_spinner=False)
def _preprocess_stipple(img_bytes, mx, my, mw, mh, max_px,
                        n_points, n_iter, threshold):
    """Weighted Voronoi stippling. Seeded RNG; Lloyd centroids are computed
    over each cell's actual pixels (nearest-site assignment via cKDTree)."""
    img = _load_masked(img_bytes, mx, my, mw, mh, max_px)
    h, w = img.shape
    density = np.clip(1.0 - img, 0.0, 1.0)
    density[density < threshold] = 0.0
    total = density.sum()
    if total == 0:
        return img.shape, np.array([])
    flat  = density.ravel() / total
    n_pts = int(min(n_points, np.count_nonzero(flat)))
    rng   = np.random.default_rng(STIPPLE_SEED)
    idx   = rng.choice(len(flat), size=n_pts, replace=False, p=flat)
    pts   = np.column_stack([(idx % w).astype(float), (idx // w).astype(float)])  # (x, y)

    # Only pixels with weight can move a centroid
    ys, xs = np.nonzero(density)
    wts    = density[ys, xs]
    pix    = np.column_stack([xs, ys]).astype(float)
    wx, wy = wts * xs, wts * ys

    for _ in range(n_iter):
        _, lab = cKDTree(pts).query(pix, workers=-1)
        tw = np.bincount(lab, weights=wts, minlength=n_pts)
        cx = np.bincount(lab, weights=wx,  minlength=n_pts)
        cy = np.bincount(lab, weights=wy,  minlength=n_pts)
        ok = tw > 0
        new_pts = pts.copy()
        new_pts[ok, 0] = cx[ok] / tw[ok]
        new_pts[ok, 1] = cy[ok] / tw[ok]
        pts = new_pts

    return img.shape, np.column_stack([
        np.clip(pts[:, 1], 0, h - 1), np.clip(pts[:, 0], 0, w - 1)
    ])


@st.cache_data(show_spinner=False)
def _preprocess_needles(img_bytes, mx, my, mw, mh, max_px, sigma):
    """
    Compute image gradient magnitude and direction for the Needles engine.
    Returns (shape, magnitude, direction_radians).
    """
    img   = _load_masked(img_bytes, mx, my, mw, mh, max_px)
    smooth = filters.gaussian(img, sigma=sigma)
    gy, gx = np.gradient(smooth)
    magnitude  = np.sqrt(gx**2 + gy**2)
    direction  = np.arctan2(gy, gx)
    return img.shape, magnitude, direction


# ─────────────────────────────────────────────
# CACHED CONTOUR / PATH STEPS
# ─────────────────────────────────────────────

def _simplify_filter_sort(raw_paths, path_tolerance, min_len):
    paths = []
    for c in raw_paths:
        if path_tolerance > 0:
            c = measure.approximate_polygon(c, tolerance=path_tolerance)
        if len(c) > min_len:
            paths.append(c)
    return _nearest_neighbour_sort(paths)


@st.cache_data(show_spinner=False)
def _contours_blueprint(key, mask, path_tolerance, min_len,
                        centreline=False, spur_len=8):
    """centreline=True: one stroke per drawn line (skeleton tracing).
    centreline=False: outline of each inked region (find_contours)."""
    if centreline:
        raw = trace_centrelines(skeletonize(mask), mask=mask, spur_len=spur_len)
    else:
        raw = measure.find_contours(mask, 0.5)
    return _simplify_filter_sort(raw, path_tolerance, min_len)


@st.cache_data(show_spinner=False)
def _contours_sobel(key, edges, path_tolerance, min_len,
                    centreline=False, spur_len=8):
    """Canny edges are ~1 px wide, so find_contours traces both sides of every
    edge; centreline=True traces each edge once."""
    if centreline:
        raw = trace_centrelines(skeletonize(edges), spur_len=spur_len)
    else:
        raw = measure.find_contours(edges.astype(float), 0.5)
    return _simplify_filter_sort(raw, path_tolerance, min_len)


@st.cache_data(show_spinner=False)
def _build_squiggle_paths(key, img_smooth, line_spacing, amplitude, frequency, min_len):
    h, w   = img_smooth.shape
    paths  = []
    row    = line_spacing // 2          # float spacing is allowed (export scaling)
    while row < h:
        cols       = np.arange(w)
        brightness = img_smooth[np.clip(int(row), 0, h-1), :]
        darkness   = 1.0 - brightness
        phase      = cols * frequency * 2 * math.pi / w
        y_offset   = darkness * amplitude * np.sin(phase)
        path = np.column_stack([
            np.clip(row + y_offset, 0, h-1), cols.astype(float)
        ])
        if len(path) > min_len:
            paths.append(path)
        row += line_spacing
    return paths


@st.cache_data(show_spinner=False)
def _contours_isoline(key, smooth, level_min, level_max,
                      ink_density, angularity, min_len):
    paths = []
    for level in np.linspace(level_min, level_max, ink_density):
        for c in measure.find_contours(smooth, level):
            if angularity > 0:
                c = measure.approximate_polygon(c, tolerance=angularity)
            if len(c) > min_len:
                paths.append(c)
    return _nearest_neighbour_sort(paths)


def _clip_paths_to_alpha(paths, alpha_map, alpha_thresh=0.5):
    """
    Split/trim each path so that only the segments lying in opaque regions
    (alpha_map >= alpha_thresh) are kept. Used by FMM's 'Skip transparent'.
    Operates on (row, col) point arrays, matching contour path convention.
    """
    if alpha_map is None:
        return paths
    h, w = alpha_map.shape
    out = []
    for p in paths:
        rows = np.clip(p[:, 0].astype(int), 0, h - 1)
        cols = np.clip(p[:, 1].astype(int), 0, w - 1)
        opaque = alpha_map[rows, cols] >= alpha_thresh
        if opaque.all():
            out.append(p)
            continue
        if not opaque.any():
            continue
        # Break the path into contiguous opaque runs
        run = []
        for pt, keep in zip(p, opaque):
            if keep:
                run.append(pt)
            elif run:
                if len(run) > 1:
                    out.append(np.array(run))
                run = []
        if len(run) > 1:
            out.append(np.array(run))
    return out


@st.cache_data(show_spinner=False)
def _contours_fmm(key, t_map, num_lines, min_len):
    paths = []
    for level in np.linspace(t_map.min(), t_map.max(), num_lines):
        for c in measure.find_contours(t_map, level):
            if len(c) > min_len:
                paths.append(c)
    return _nearest_neighbour_sort(paths)


@st.cache_data(show_spinner=False)
def _contours_streamlines(key, field, num_levels, path_tol, min_len):
    paths = []
    for level in np.linspace(0.05, 0.95, num_levels):
        for c in measure.find_contours(field, level):
            if path_tol > 0:
                c = measure.approximate_polygon(c, tolerance=path_tol)
            if len(c) > min_len:
                paths.append(c)
    return _nearest_neighbour_sort(paths)


@st.cache_data(show_spinner=False)
def _stipple_to_paths(key, points, dot_radius):
    if len(points) == 0:
        return []
    paths  = []
    steps  = 8
    angles = np.linspace(0, 2*math.pi, steps, endpoint=False)
    for pt in points:
        r, c = pt[0], pt[1]
        circle = np.array([
            [r + dot_radius*math.sin(a), c + dot_radius*math.cos(a)]
            for a in angles
        ] + [[r + dot_radius*math.sin(angles[0]),
              c + dot_radius*math.cos(angles[0])]])
        paths.append(np.array(circle))
    return _nearest_neighbour_sort(paths)


@st.cache_data(show_spinner=False)
def _build_needle_paths(key, magnitude, direction, grid_spacing,
                        max_length, darkness_thresh, min_len):
    """
    Needles engine — oriented short line segments following local gradient direction.
    Place a needle at every grid_spacing interval. Length proportional to local
    darkness. Direction follows gradient angle.
    """
    h, w  = magnitude.shape
    paths = []
    for row in np.arange(grid_spacing // 2, h, grid_spacing):
        for col in np.arange(grid_spacing // 2, w, grid_spacing):
            mag = magnitude[int(row), int(col)]
            if mag < darkness_thresh:
                continue
            # Normalise length: darker / stronger gradient = longer needle
            length = mag / (magnitude.max() + 1e-8) * max_length
            if length < 1.0:
                continue
            angle  = direction[int(row), int(col)]
            dr     = math.cos(angle) * length / 2
            dc     = math.sin(angle) * length / 2
            r0 = np.clip(row - dr, 0, h - 1)
            c0 = np.clip(col - dc, 0, w - 1)
            r1 = np.clip(row + dr, 0, h - 1)
            c1 = np.clip(col + dc, 0, w - 1)
            needle = np.array([[r0, c0], [r1, c1]])
            if len(needle) >= min_len or min_len <= 2:
                paths.append(needle)
    return _nearest_neighbour_sort(paths)


def _tsp_greedy_order(pts):
    """Nearest-neighbour chain using a cKDTree (visited points are skipped by
    widening k, so no O(n^2) scan). Starts at point 0."""
    n = len(pts)
    if n <= 2:
        return np.arange(n)
    tree    = cKDTree(pts)
    visited = np.zeros(n, dtype=bool)
    order   = np.empty(n, dtype=np.int64)
    cur, visited[0], order[0] = 0, True, 0
    for i in range(1, n):
        k = min(n, 12)
        while True:
            _, idx = tree.query(pts[cur], k=k)
            idx  = np.atleast_1d(idx)
            free = idx[~visited[idx]]
            if free.size:
                nxt = int(free[0]); break
            if k >= n:
                nxt = int(np.flatnonzero(~visited)[0]); break
            k = min(n, k * 4)
        visited[nxt] = True
        order[i] = nxt
        cur = nxt
    return order


def _tsp_two_opt(pts, order, time_limit, k_nbr=8):
    """
    2-opt for an OPEN path (both ends free) using k-nearest-neighbour candidate
    lists and a work queue. Stops at a local optimum or after time_limit
    seconds, whichever comes first. Returns an improved permutation.
    """
    n = len(order)
    if n < 4 or time_limit <= 0:
        return order
    deadline = time.monotonic() + time_limit
    _, nbr = cKDTree(pts).query(pts, k=min(k_nbr + 1, n))
    nbr = nbr[:, 1:].tolist()
    X, Y = pts[:, 0].tolist(), pts[:, 1].tolist()
    order = order.copy()
    pos = np.empty(n, dtype=np.int64)
    pos[order] = np.arange(n)
    o, p_at, hyp = order.item, pos.item, math.hypot

    def d(a, b):
        return hyp(X[a] - X[b], Y[a] - Y[b])

    def gain(i, j):            # gain of reversing order[i..j]
        old = new = 0.0
        if i > 0:
            a, b, c = o(i - 1), o(i), o(j)
            old += d(a, b); new += d(a, c)
        if j < n - 1:
            b, c, e = o(i), o(j), o(j + 1)
            old += d(c, e); new += d(b, e)
        return old - new

    queue = deque(order.tolist())
    inq = [True] * n
    iters = 0
    while queue:
        iters += 1
        if (iters & 63) == 0 and time.monotonic() > deadline:
            break
        u = queue.popleft()
        inq[u] = False
        while True:
            pu = p_at(u)
            lim = max(d(u, o(pu + 1)) if pu < n - 1 else 0.0,
                      d(u, o(pu - 1)) if pu > 0 else 0.0)
            best = None
            for v in nbr[u]:
                if d(u, v) >= lim:
                    break
                pv = p_at(v)
                lo, hi = (pu, pv) if pu < pv else (pv, pu)
                for i, j in ((lo + 1, hi), (lo, hi - 1)):
                    if i < j:
                        g = gain(i, j)
                        if g > 1e-9 and (best is None or g > best[0]):
                            best = (g, i, j)
            if best is None:
                break
            _, i, j = best
            order[i:j + 1] = order[i:j + 1][::-1]
            pos[order[i:j + 1]] = np.arange(i, j + 1)
            for q in (i - 1, i, j, j + 1):
                if 0 <= q < n:
                    node = o(q)
                    if not inq[node]:
                        inq[node] = True
                        queue.append(node)
    return order


@st.cache_data(show_spinner=False)
def _build_continuous_line(key, points, time_limit=0.0):
    """
    Continuous Line / TSP-style engine. Chains all stipple points into one
    unbroken path: cKDTree nearest-neighbour tour, then a time-limited 2-opt
    pass. The plotter never lifts the pen. Returns [one (n, 2) array].
    """
    if len(points) == 0:
        return []
    pts = np.asarray(points, dtype=float)
    order = _tsp_greedy_order(pts)
    order = _tsp_two_opt(pts, order, time_limit)
    return [pts[order]]


# ─────────────────────────────────────────────
# RESOLUTION CONSTANTS
# ─────────────────────────────────────────────
TUNE_PX   = 600
EXPORT_PX = 1200


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if uploaded_file is not None:
    uploaded_file.seek(0)
    file_bytes = bytes(uploaded_file.read())

    st.sidebar.image(BytesIO(file_bytes), caption="Input Preview", use_container_width=True)

    fig          = go.Figure()
    export_paths = []
    w = h = 1
    mk             = (mask_x, mask_y, mask_w, mask_h)
    img_gray_export = None
    export_shape    = (1, 1)
    cmyk_engine_fn  = None   # callable that returns {'C':paths,'M':paths,'Y':paths,'K':paths}
    # Real export/preview width ratio, used to scale pixel-unit settings at export
    exp_scale       = _effective_scale(file_bytes, mk, TUNE_PX, EXPORT_PX)
    preview_px      = TUNE_PX
    preview_style   = ("#00ff88", 0.5)   # (colour, line width) for the preview
    preview_points  = None               # stipple dots (drawn as markers)
    seed_marker     = None               # FMM seed (x, y) for the preview

    try:

        # ── BLUEPRINT ────────────────────────────────────────────────────
        if mode == "Blueprint (Sketch Trace)":
            st.sidebar.header("Blueprint Settings")
            grid_size       = st.sidebar.slider("Neighbourhood Size", 3, 99, 25, step=2)
            contrast_offset = st.sidebar.slider("Contrast Offset", -0.2, 0.2, 0.02, step=0.01)
            line_weld       = st.sidebar.slider("Line Welding", 1, 9, 3, step=1)
            use_thinning    = st.sidebar.checkbox(
                "Centreline (single stroke)", value=True,
                help="Trace the middle of each drawn line once. Off = trace the "
                     "outline of each stroke (two lines per stroke).")
            spur_len        = st.sidebar.slider(
                "Spur Removal (px)", 0, 30, 8, disabled=not use_thinning,
                help="Remove short side-branches (hairs) the skeleton grows from "
                     "bumps and ink blots. Also scales with stroke width.")
            path_tolerance  = st.sidebar.slider("Path Smoothing (RDP)", 0.0, 2.0, 0.3)
            stroke_width    = st.sidebar.slider("SVG Stroke Width", 0.1, 3.0, 1.0, step=0.1)

            shape, mask = _preprocess_blueprint(
                file_bytes, *mk, TUNE_PX, grid_size, contrast_offset, line_weld
            )
            h, w = shape
            key  = (*mk, grid_size, contrast_offset, line_weld, use_thinning)
            export_paths = _contours_blueprint(key, mask, path_tolerance, min_line_len,
                                               use_thinning, spur_len)
            preview_style = ("#00ff88", 1.0)

            # Pixel-unit settings at export resolution
            bp_grid_x = _odd(grid_size * exp_scale)
            bp_weld_x = _scale_weld(line_weld, exp_scale)
            bp_sig_x  = 0.6 * exp_scale
            bp_tol_x  = path_tolerance * exp_scale
            bp_spur_x = spur_len * exp_scale

            def _get_export():
                s2, m2 = _preprocess_blueprint(
                    file_bytes, *mk, EXPORT_PX, bp_grid_x, contrast_offset, bp_weld_x,
                    bp_sig_x)
                k2 = (*mk, grid_size, contrast_offset, line_weld, use_thinning, "exp")
                return _contours_blueprint(k2, m2, bp_tol_x, min_line_len,
                                           use_thinning, bp_spur_x), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    for name, ink in _cmyk_ink_channels(file_bytes, mk, EXPORT_PX):
                        m2 = _blueprint_mask(1.0 - ink, bp_grid_x, contrast_offset,
                                             bp_weld_x, bp_sig_x)
                        out[name] = _contours_blueprint((*mk, name), m2, bp_tol_x, min_line_len,
                                                        use_thinning, bp_spur_x)
                    return out

        # ── SOBEL EDGE ────────────────────────────────────────────────────
        elif mode == "Sobel Edge (Outline)":
            st.sidebar.header("Sobel Edge Settings")
            sigma       = st.sidebar.slider("Edge Blur (sigma)", 0.5, 5.0, 1.5, step=0.1)
            low_thresh  = st.sidebar.slider("Low Threshold", 0.0, 0.3, 0.05, step=0.01)
            high_thresh = st.sidebar.slider("High Threshold", 0.1, 0.9, 0.20, step=0.01)
            sb_centre   = st.sidebar.checkbox(
                "Centreline (single stroke)", value=True,
                help="Trace each edge once. Off = legacy tracing, which draws both "
                     "sides of every 1-px edge.")
            sb_spur     = st.sidebar.slider("Spur Removal (px)", 0, 30, 6,
                                            disabled=not sb_centre)
            path_tol    = st.sidebar.slider("Path Smoothing (RDP)", 0.0, 2.0, 0.3)
            stroke_width= st.sidebar.slider("SVG Stroke Width", 0.1, 3.0, 0.8, step=0.1)

            shape, edges = _preprocess_sobel(
                file_bytes, *mk, TUNE_PX, sigma, low_thresh, high_thresh)
            h, w = shape
            key  = (*mk, sigma, low_thresh, high_thresh)
            export_paths = _contours_sobel(key, edges, path_tol, min_line_len,
                                           sb_centre, sb_spur)
            preview_style = ("#00ff88", 0.8)

            # Gradient magnitude is per-pixel, so it shrinks by 1/scale at higher
            # resolution: Canny thresholds are divided by exp_scale.
            sb_sig_x  = sigma * exp_scale
            sb_low_x  = low_thresh / exp_scale
            sb_high_x = high_thresh / exp_scale
            sb_tol_x  = path_tol * exp_scale
            sb_spur_x = sb_spur * exp_scale

            def _get_export():
                s2, e2 = _preprocess_sobel(file_bytes, *mk, EXPORT_PX, sb_sig_x, sb_low_x, sb_high_x)
                k2 = (*mk, sigma, low_thresh, high_thresh, "exp")
                return _contours_sobel(k2, e2, sb_tol_x, min_line_len,
                                       sb_centre, sb_spur_x), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    # Canny is symmetric under inversion, so ink density is fine as-is.
                    for name, ink in _cmyk_ink_channels(file_bytes, mk, EXPORT_PX):
                        edges_c = feature.canny(ink, sigma=sb_sig_x, low_threshold=sb_low_x,
                                                high_threshold=sb_high_x)
                        out[name] = _contours_sobel((*mk, name), edges_c, sb_tol_x, min_line_len,
                                                    sb_centre, sb_spur_x)
                    return out

        # ── SQUIGGLE ──────────────────────────────────────────────────────
        elif mode == "Squiggle (Scanline Shading)":
            st.sidebar.header("Squiggle Settings")
            sq_sigma     = st.sidebar.slider("Pre-blur", 0.5, 5.0, 1.5, step=0.5)
            line_spacing = st.sidebar.slider("Line Spacing (px)", 2, 30, 6)
            amplitude    = st.sidebar.slider("Max Amplitude (px)", 1, 30, 8)
            frequency    = st.sidebar.slider("Wave Frequency", 1, 20, 6)
            stroke_width = st.sidebar.slider("SVG Stroke Width", 0.1, 2.0, 0.5, step=0.1)

            shape, img_smooth = _preprocess_squiggle(file_bytes, *mk, TUNE_PX, sq_sigma)
            h, w = shape
            key  = (*mk, sq_sigma)
            export_paths = _build_squiggle_paths(key, img_smooth, line_spacing, amplitude, frequency, min_line_len)
            preview_style = ("#00ff88", 0.5)

            sq_sig_x = sq_sigma * exp_scale
            sq_sp_x  = line_spacing * exp_scale
            sq_amp_x = amplitude * exp_scale      # frequency is per image width: unchanged

            def _get_export():
                s2, sm2 = _preprocess_squiggle(file_bytes, *mk, EXPORT_PX, sq_sig_x)
                k2 = (*mk, sq_sigma, "exp")
                return _build_squiggle_paths(k2, sm2, sq_sp_x, sq_amp_x, frequency, min_line_len), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    for name, ink in _cmyk_ink_channels(file_bytes, mk, EXPORT_PX):
                        sm = filters.gaussian(1.0 - ink, sigma=sq_sig_x)     # brightness
                        out[name] = _build_squiggle_paths((*mk, name), sm, sq_sp_x, sq_amp_x, frequency, min_line_len)
                    return out

        # ── ISOLINE ───────────────────────────────────────────────────────
        elif mode == "Isoline (Tonal Contour)":
            st.sidebar.header("Isoline Settings")
            ink_density  = st.sidebar.slider("Line Density", 10, 120, 40)
            angularity   = st.sidebar.slider("Line Sharpness (RDP)", 0.0, 3.0, 0.8)
            shadow_boost = st.sidebar.slider("Shadow Boost (Gamma)", 0.1, 1.0, 0.4)
            sigma        = st.sidebar.slider("Pre-blur", 0.3, 3.0, 0.8, step=0.1)
            level_min    = st.sidebar.slider("Level Min", 0.0, 0.5, 0.05, step=0.01)
            level_max    = st.sidebar.slider("Level Max", 0.3, 1.0, 0.70, step=0.01)
            stroke_width = st.sidebar.slider("SVG Stroke Width", 0.1, 3.0, 0.6, step=0.1)

            shape, smooth = _preprocess_isoline(file_bytes, *mk, TUNE_PX, shadow_boost, sigma)
            h, w = shape
            key  = (*mk, shadow_boost, sigma)
            export_paths = _contours_isoline(key, smooth, level_min, level_max, ink_density, angularity, min_line_len)
            preview_style = ("#00ff88", 0.6)

            iso_sig_x = sigma * exp_scale
            iso_ang_x = angularity * exp_scale

            def _get_export():
                s2, sm2 = _preprocess_isoline(file_bytes, *mk, EXPORT_PX, shadow_boost, iso_sig_x)
                k2 = (*mk, shadow_boost, sigma, "exp")
                return _contours_isoline(k2, sm2, level_min, level_max, ink_density, iso_ang_x, min_line_len), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    for name, ink in _cmyk_ink_channels(file_bytes, mk, EXPORT_PX):
                        sm = filters.gaussian(exposure.adjust_gamma(1.0 - ink, shadow_boost), sigma=iso_sig_x)
                        out[name] = _contours_isoline((*mk, name), sm, level_min, level_max, ink_density, iso_ang_x, min_line_len)
                    return out

        # ── FAST MARCHING ─────────────────────────────────────────────────
        elif mode == "Fast Marching (Topographic)":
            st.sidebar.header("Fast Marching Settings")
            num_lines    = st.sidebar.slider("Line Density", 50, 1000, 250)
            gamma_val    = st.sidebar.slider("Gamma", 0.1, 2.0, 0.4)
            sigma        = st.sidebar.slider("Pre-blur", 0.5, 3.0, 1.2, step=0.1)
            speed_offset = st.sidebar.slider("Speed Offset", 0.01, 0.50, 0.02, step=0.01,
                help="Minimum wave speed — prevents the wavefront from stalling in "
                     "pure-black areas. Lower = more contour compression/detail in "
                     "shadows. Higher = more even spacing throughout.")
            fmm_scale_pct = st.sidebar.slider("Scale %", 10, 100, 100, step=5,
                help="Downscale the working image before running Fast Marching. "
                     "Lower = faster preview, less fine detail. 100% = use the "
                     "normal tuning/export resolution unchanged.")
            smooth_mode  = st.sidebar.radio("Contour Mode", ["Angular", "Smooth"],
                help="Angular: sharp contours follow brightness edges. "
                     "Smooth: Gaussian-softened contours — more organic, less topographic.")
            st.sidebar.markdown("**Seed Point**")
            seed_auto    = st.sidebar.checkbox("Auto-detect seed (darkest region)", value=True,
                help="Uncheck to place the seed point manually.")
            if seed_auto:
                seed_x_pct = seed_y_pct = 50
            else:
                seed_x_pct = st.sidebar.slider("Seed X (%)", 0, 100, 50,
                    help="Horizontal position of the wave origin as % of image width. "
                         "50% = centre. For a portrait, try ~50% X, ~40% Y (between eyebrows).")
                seed_y_pct = st.sidebar.slider("Seed Y (%)", 0, 100, 40,
                    help="Vertical position of the wave origin as % of image height.")
            skip_transparent = st.sidebar.checkbox("Skip transparent", value=False,
                help="For PNGs with an alpha channel: clip contour lines that fall "
                     "in fully transparent regions. No effect on images without alpha.")
            stroke_width = st.sidebar.slider("SVG Stroke Width", 0.1, 2.0, 0.4, step=0.1)

            tune_px_fmm   = max(50, int(TUNE_PX * fmm_scale_pct / 100))
            export_px_fmm = max(50, int(EXPORT_PX * fmm_scale_pct / 100))
            preview_px    = tune_px_fmm
            # Actual ratio for this engine's own (possibly reduced) resolutions
            exp_scale     = _effective_scale(file_bytes, mk, tune_px_fmm, export_px_fmm)
            fmm_sig_x     = sigma * exp_scale

            shape, t_map = _preprocess_fmm(
                file_bytes, *mk, tune_px_fmm, gamma_val, sigma,
                smooth_mode, seed_auto, seed_x_pct, seed_y_pct, speed_offset
            )
            h, w = shape
            key  = (*mk, gamma_val, sigma, smooth_mode, seed_auto, seed_x_pct, seed_y_pct,
                    speed_offset, fmm_scale_pct)
            export_paths = _contours_fmm(key, t_map, num_lines, min_line_len)

            if skip_transparent:
                alpha_map = _load_alpha_mask(file_bytes, *mk, tune_px_fmm)
                if alpha_map is not None:
                    export_paths = _clip_paths_to_alpha(export_paths, alpha_map)

            preview_style = ("#00ff88", 0.5)
            if not seed_auto:
                seed_marker = (seed_x_pct / 100 * w, h - seed_y_pct / 100 * h)

            def _get_export():
                s2, tm2 = _preprocess_fmm(
                    file_bytes, *mk, export_px_fmm, gamma_val, fmm_sig_x,
                    smooth_mode, seed_auto, seed_x_pct, seed_y_pct, speed_offset)
                k2 = (*mk, gamma_val, sigma, smooth_mode, seed_auto, seed_x_pct, seed_y_pct,
                      speed_offset, fmm_scale_pct, "exp")
                paths2 = _contours_fmm(k2, tm2, num_lines, min_line_len)
                if skip_transparent:
                    alpha2 = _load_alpha_mask(file_bytes, *mk, export_px_fmm)
                    if alpha2 is not None:
                        paths2 = _clip_paths_to_alpha(paths2, alpha2)
                return paths2, s2, _load_masked(file_bytes, *mk, export_px_fmm)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    for name, ink in _cmyk_ink_channels(file_bytes, mk, export_px_fmm):
                        bright = 1.0 - ink
                        adj = filters.gaussian(exposure.adjust_gamma(bright, gamma_val), sigma=fmm_sig_x)
                        phi = np.ones(bright.shape)
                        if seed_auto:
                            bi = filters.gaussian(bright, sigma=max(*bright.shape) * 0.05)
                            phi[np.unravel_index(np.argmin(bi), bi.shape)] = 0
                        else:
                            phi[min(bright.shape[0] - 1, int(seed_y_pct / 100 * bright.shape[0])),
                                min(bright.shape[1] - 1, int(seed_x_pct / 100 * bright.shape[1]))] = 0
                        tm = skfmm.travel_time(phi, adj + speed_offset)
                        paths_c = _contours_fmm((*mk, name), tm, num_lines, min_line_len)
                        if skip_transparent:
                            alpha_c = _load_alpha_mask(file_bytes, *mk, export_px_fmm)
                            if alpha_c is not None:
                                paths_c = _clip_paths_to_alpha(paths_c, alpha_c)
                        out[name] = paths_c
                    return out

        # ── STREAMLINES ───────────────────────────────────────────────────
        elif mode == "Line Sketch (Streamlines)":
            st.sidebar.header("Streamlines Settings")
            sigma_edge   = st.sidebar.slider("Edge Smoothness", 0.5, 5.0, 1.5, step=0.1)
            num_levels   = st.sidebar.slider("Number of Flow Lines", 20, 400, 80)
            path_tol     = st.sidebar.slider("Path Smoothing (RDP)", 0.0, 3.0, 0.5)
            stroke_width = st.sidebar.slider("SVG Stroke Width", 0.1, 2.0, 0.5, step=0.1)

            shape, field = _preprocess_streamlines(file_bytes, *mk, TUNE_PX, sigma_edge)
            h, w = shape
            key  = (*mk, sigma_edge)
            export_paths = _contours_streamlines(key, field, num_levels, path_tol, min_line_len)
            preview_style = ("#00ccff", 0.5)

            sl_sig_x = sigma_edge * exp_scale
            sl_tol_x = path_tol * exp_scale

            def _get_export():
                s2, f2 = _preprocess_streamlines(file_bytes, *mk, EXPORT_PX, sl_sig_x)
                k2 = (*mk, sigma_edge, "exp")
                return _contours_streamlines(k2, f2, num_levels, sl_tol_x, min_line_len), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    for name, ink in _cmyk_ink_channels(file_bytes, mk, EXPORT_PX):
                        f = 1.0 - filters.gaussian(1.0 - ink, sigma=sl_sig_x)   # == ink, smoothed
                        out[name] = _contours_streamlines((*mk, name), f, num_levels, sl_tol_x, min_line_len)
                    return out

        # ── VORONOI STIPPLING ─────────────────────────────────────────────
        elif mode == "Voronoi Stippling":
            st.sidebar.header("Stippling Settings")
            n_points     = st.sidebar.slider("Number of Points", 100, 5000, 1000, step=100)
            n_iter       = st.sidebar.slider("Lloyd Iterations", 1, 20, 5)
            threshold    = st.sidebar.slider("White Threshold", 0.0, 0.9, 0.1, step=0.05)
            dot_radius   = st.sidebar.slider("Dot Radius (px)", 0.5, 8.0, 1.5, step=0.5)
            stroke_width = st.sidebar.slider("SVG Stroke Width", 0.1, 2.0, 0.5, step=0.1)

            if use_cmyk:
                st.warning("CMYK export is not available for Voronoi Stippling "
                           "(greyscale point placement) — only the single SVG is exported.")

            with st.spinner(f"Computing {n_points} stipple points…"):
                shape, points = _preprocess_stipple(
                    file_bytes, *mk, TUNE_PX, n_points, n_iter, threshold)
            h, w = shape
            if len(points) > 0:
                stip_key     = (*mk, n_points, n_iter, threshold)
                export_paths = _stipple_to_paths(stip_key, points, dot_radius)
                preview_points = points
                preview_style  = ("#00ff88", 2)
            else:
                st.warning("No stipple points generated — try lowering White Threshold.")

            def _get_export():
                with st.spinner("Re-computing stipple at export resolution…"):
                    s2, pts2 = _preprocess_stipple(file_bytes, *mk, EXPORT_PX, n_points, n_iter, threshold)
                k2 = (*mk, n_points, n_iter, threshold, "exp")
                return _stipple_to_paths(k2, pts2, dot_radius * exp_scale), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

        # ── NEEDLES ───────────────────────────────────────────────────────
        elif mode == "Needles (Oriented Dashes)":
            st.sidebar.header("Needles Settings")
            nd_sigma    = st.sidebar.slider("Blur (sigma)", 0.5, 5.0, 2.0, step=0.5,
                help="Smooth the image before computing gradients. Higher = fewer noisy needles.")
            grid_sp     = st.sidebar.slider("Grid Spacing (px)", 2, 30, 8,
                help="Distance between needle centres. Lower = denser field.")
            max_length  = st.sidebar.slider("Max Needle Length (px)", 2, 40, 12,
                help="Length of the longest needles (in darkest/strongest gradient areas).")
            dark_thresh = st.sidebar.slider("Darkness Threshold", 0.0, 0.5, 0.05, step=0.01,
                help="Ignore areas where gradient magnitude is below this. "
                     "Raise to place needles only in strong edge/shadow areas.")
            stroke_width= st.sidebar.slider("SVG Stroke Width", 0.1, 2.0, 0.6, step=0.1)

            shape, mag, dirn = _preprocess_needles(file_bytes, *mk, TUNE_PX, nd_sigma)
            h, w = shape
            nd_key       = (*mk, nd_sigma)
            export_paths = _build_needle_paths(nd_key, mag, dirn, grid_sp, max_length, dark_thresh, min_line_len)
            preview_style = ("#ffaa00", 0.6)

            nd_sig_x    = nd_sigma * exp_scale
            nd_grid_x   = grid_sp * exp_scale
            nd_len_x    = max_length * exp_scale
            nd_thresh_x = dark_thresh / exp_scale   # per-pixel gradient shrinks with resolution

            def _get_export():
                s2, mag2, dirn2 = _preprocess_needles(file_bytes, *mk, EXPORT_PX, nd_sig_x)
                k2 = (*mk, nd_sigma, "exp")
                return _build_needle_paths(k2, mag2, dirn2, nd_grid_x, nd_len_x, nd_thresh_x, min_line_len), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    # Gradient magnitude is invariant to inversion and the needle is a
                    # centred segment, so ink density can be used directly.
                    for name, ink in _cmyk_ink_channels(file_bytes, mk, EXPORT_PX):
                        sm = filters.gaussian(ink, sigma=nd_sig_x)
                        gy2, gx2 = np.gradient(sm)
                        m2 = np.sqrt(gx2**2+gy2**2); d2 = np.arctan2(gy2,gx2)
                        out[name] = _build_needle_paths((*mk, name), m2, d2, nd_grid_x, nd_len_x, nd_thresh_x, min_line_len)
                    return out

        # ── CONTINUOUS LINE (TSP) ─────────────────────────────────────────
        elif mode == "Continuous Line (TSP)":
            st.sidebar.header("Continuous Line Settings")
            st.sidebar.markdown(
                "Single unbroken line visiting all points — the pen never lifts. "
                "Uses greedy nearest-neighbour TSP approximation."
            )
            n_points     = st.sidebar.slider("Number of Points", 100, 3000, 800, step=100,
                help="More points = more detail but slower to compute. Keep ≤1000 while tuning.")
            threshold    = st.sidebar.slider("White Threshold", 0.0, 0.9, 0.15, step=0.05,
                help="Points won't be placed in areas brighter than this.")
            n_iter       = st.sidebar.slider("Lloyd Iterations", 1, 10, 3,
                help="Pre-distribute points with Voronoi relaxation before chaining. "
                     "More = better point distribution.")
            stroke_width = st.sidebar.slider("SVG Stroke Width", 0.1, 2.0, 0.5, step=0.1)

            if use_cmyk:
                st.warning("CMYK export is not available for Continuous Line (TSP) "
                           "(greyscale point placement) — only the single SVG is exported.")

            with st.spinner(f"Computing {n_points} points + TSP chain…"):
                shape, points = _preprocess_stipple(
                    file_bytes, *mk, TUNE_PX, n_points, n_iter, threshold)
                h, w = shape

                if len(points) > 0:
                    tsp_key      = (*mk, n_points, n_iter, threshold, "tsp")
                    export_paths = _build_continuous_line(tsp_key, points, TSP_TIME_PREVIEW)
            if len(points) > 0:
                st.caption(f"Single continuous path — {len(points):,} points — pen never lifts.")
            else:
                st.warning("No points generated — try lowering White Threshold.")

            def _get_export():
                with st.spinner("Re-computing TSP at export resolution…"):
                    s2, pts2 = _preprocess_stipple(file_bytes, *mk, EXPORT_PX, n_points, n_iter, threshold)
                    k2 = (*mk, n_points, n_iter, threshold, "tsp_exp")
                    paths2 = _build_continuous_line(k2, pts2, TSP_TIME_EXPORT)
                return paths2, s2, _load_masked(file_bytes, *mk, EXPORT_PX)

        # ── PREVIEW + BUILD EXPORT ────────────────────────────────────────
        if export_paths:
            mask_info   = f" · Mask {mask_w}×{mask_h}% @ ({mask_x},{mask_y})" if use_mask else ""
            wobble_info = f" · Wobble ON (amp={wobble_amp}px)" if use_wobble else ""

            # Preview: wobble is applied at preview resolution so it can be tuned by eye.
            preview_paths = export_paths
            if use_wobble:
                preview_paths = apply_noise_wobble(export_paths, wobble_scale, wobble_amp, (h, w))
            est_paths = preview_paths
            if use_pen_passes:
                est_paths = _apply_pen_passes(
                    preview_paths, _load_masked(file_bytes, *mk, preview_px),
                    pen_passes, pen_pass_thresh, (h, w))
            p_draw, p_lift, p_min = _estimate_plot(est_paths, w, target_width_mm, plot_speed_mms)
            st.caption(f"Preview: {w}px · {len(export_paths):,} paths{mask_info}{wobble_info}")
            st.caption(
                f"Preview estimate: draw {p_draw / 1000:.2f} m · pen-up {p_lift / 1000:.2f} m · "
                f"~{p_min:.1f} min"
                + (f" · pen passes add {len(est_paths) - len(export_paths):,} paths" if use_pen_passes else "")
            )

            cfg = dict(
                target_width_mm=target_width_mm, plot_speed_mms=plot_speed_mms,
                mode=mode, stroke_width=stroke_width, mk=mk,
                use_wobble=use_wobble, wobble_scale=wobble_scale, wobble_amp=wobble_amp,
                use_vpype=use_vpype, vpype_linemerge=vpype_linemerge,
                vpype_merge_tol=vpype_merge_tol, vpype_linesort=vpype_linesort,
                vpype_two_opt=vpype_two_opt, vpype_reloop=vpype_reloop,
                vpype_linesimplify=vpype_linesimplify,
                use_pen_passes=use_pen_passes,
                pen_passes=pen_passes if use_pen_passes else 1,
                pen_pass_thresh=pen_pass_thresh if use_pen_passes else 0.5,
                use_cmyk=bool(use_cmyk and cmyk_engine_fn is not None),
            )
            sig = _export_signature(file_bytes, mode, cfg, export_paths)

            if st.button("🛠 Build export", type="primary",
                         help="Runs the full-resolution export (wobble, pen passes, vpype, "
                              "CMYK). Slider changes only update the preview."):
                try:
                    bundle = _run_export(_get_export, cmyk_engine_fn, cfg, exp_scale)
                    bundle["sig"] = sig
                    st.session_state["export_bundle"] = bundle
                except Exception as e:
                    st.error(f"Export failed: {type(e).__name__}: {e}")

            bundle = st.session_state.get("export_bundle")
            if bundle:
                _render_export(bundle, stale=(bundle["sig"] != sig))
            else:
                st.info("Tune the preview, then click **Build export** to generate the "
                        "full-resolution SVG.")

            if preview_points is not None:
                pts_draw = preview_points
                if use_wobble:
                    pts_draw = apply_noise_wobble([pts_draw], wobble_scale, wobble_amp, (h, w))[0]
                _plotly_dots(fig, pts_draw, h, preview_style[0], preview_style[1])
            else:
                _plotly_traces(fig, preview_paths, h, *preview_style)
            if seed_marker is not None:
                fig.add_trace(go.Scatter(
                    x=[seed_marker[0]], y=[seed_marker[1]], mode="markers",
                    marker=dict(color="#ff4444", size=12, symbol="x"),
                    showlegend=False, name="Seed point"
                ))

            fig.update_layout(
                width=950, height=800,
                plot_bgcolor="#0E1117", paper_bgcolor="#0E1117",
                xaxis=dict(visible=False),
                yaxis=dict(visible=False, scaleanchor="x"),
                margin=dict(l=0, r=0, t=0, b=0),
                dragmode="pan",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning(
                "No paths found. Try lowering **Min Line Length**, "
                "adjusting thresholds, or switching engines."
            )

    except MemoryError:
        st.error("Image too large. Lower Max Processing Width or reduce point count.")
    except ValueError as e:
        st.error(f"Processing error — check slider values. Detail: {e}")
    except Exception as e:
        st.error(f"Unexpected error: {type(e).__name__}: {e}")

else:
    st.info("⬆️ Upload an image in the sidebar to begin.")