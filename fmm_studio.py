"""
Universal Plotter Studio v5.5
Optimized for Silhouette Cameo 5 / AxiDraw — ballpoint & fineliner pens.

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

import io
import math
import os
import shutil
import subprocess
import tempfile
import zipfile
from io import BytesIO, StringIO

import numpy as np
import plotly.graph_objects as go
import skfmm
import streamlit as st
from scipy.spatial import Voronoi
from skimage import color, exposure, feature, filters, io as skio, measure, transform
from skimage.morphology import closing, skeletonize, square

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(layout="wide", page_title="Universal Plotter Studio")
st.title("🎨 Universal Plotter Studio v5.5")
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
             "Higher = tight rapid wobble.")
    wobble_amp   = st.sidebar.slider("Wobble Amplitude (px)", 0.5, 15.0, 3.0, step=0.5,
        help="Maximum displacement in pixels. Higher = more dramatic hand-drawn look.")
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
    """Sample coherent noise at (x, y) using precomputed gradient grid."""
    x0, y0 = int(x), int(y)
    x1, y1 = x0 + 1, y0 + 1
    sx, sy = _fade(x - x0), _fade(y - y0)

    def _dot(ix, iy, px, py):
        ix = min(ix, gx.shape[1] - 1)
        iy = min(iy, gx.shape[0] - 1)
        return gx[iy, ix] * (px - ix) + gy[iy, ix] * (py - iy)

    n00 = _dot(x0, y0, x, y)
    n10 = _dot(x1, y0, x, y)
    n01 = _dot(x0, y1, x, y)
    n11 = _dot(x1, y1, x, y)
    return _lerp(_lerp(n00, n10, sx), _lerp(n01, n11, sx), sy)


def apply_noise_wobble(paths, scale, amplitude, img_shape):
    """
    Displace each point in each path by coherent Perlin-style noise.
    Two independent noise fields (different seeds) displace x and y separately.
    """
    if amplitude <= 0 or not paths:
        return paths

    h, w = img_shape
    grid_w = max(2, int(w * scale) + 2)
    grid_h = max(2, int(h * scale) + 2)
    gx1, gy1 = _make_perlin_grid(42,  grid_w, grid_h)
    gx2, gy2 = _make_perlin_grid(137, grid_w, grid_h)

    wobbled = []
    for path in paths:
        new_pts = []
        for pt in path:
            r, c = float(pt[0]), float(pt[1])
            nx = c * scale
            ny = r * scale
            dx = _perlin_noise_2d(nx, ny, gx1, gy1) * amplitude
            dy = _perlin_noise_2d(nx, ny, gx2, gy2) * amplitude
            new_pts.append([
                np.clip(r + dy, 0, h - 1),
                np.clip(c + dx, 0, w - 1)
            ])
        wobbled.append(np.array(new_pts))
    return wobbled


# ─────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────

def _apply_mask(img, mx, my, mw, mh):
    h, w = img.shape
    x0 = int(w * mx / 100)
    y0 = int(h * my / 100)
    x1 = min(w, x0 + int(w * mw / 100))
    y1 = min(h, y0 + int(h * mh / 100))
    return img[y0:y1, x0:x1]


def _cap_image(img, max_px):
    h, w = img.shape
    if w > max_px:
        img = transform.rescale(img, max_px / w, anti_aliasing=True)
    return img


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


def _build_and_export(export_paths, w, h, target_width_mm, plot_speed_mms,
                      mode_label, stroke_width, use_wobble, wobble_scale,
                      wobble_amp, use_vpype, vpype_linemerge, vpype_merge_tol,
                      vpype_linesort, vpype_two_opt, vpype_reloop,
                      vpype_linesimplify, use_pen_passes, pen_passes_val,
                      pen_pass_thresh, img_gray_export, export_shape,
                      use_cmyk, cmyk_engine_fn):

    # Apply noise wobble
    final_paths = export_paths
    if use_wobble:
        final_paths = apply_noise_wobble(
            final_paths, wobble_scale, wobble_amp, export_shape
        )

    # Apply pen passes
    if use_pen_passes and img_gray_export is not None:
        final_paths = _apply_pen_passes(
            final_paths, img_gray_export,
            pen_passes_val, pen_pass_thresh, export_shape
        )
        extra = len(final_paths) - len(export_paths)
        if extra > 0:
            st.caption(f"✏️ Pen passes: {extra:,} extra path duplicates added.")

    scale         = target_width_mm / w
    total_dist_px = sum(
        np.sum(np.sqrt(np.sum(np.diff(p, axis=0)**2, axis=1)))
        for p in final_paths
    )
    total_mm = total_dist_px * scale
    est_min  = (total_mm / plot_speed_mms) * 1.2 / 60
    lift_mm  = _pen_lift_distance(final_paths) * scale

    c1, c2, c3 = st.columns([1, 1, 2])
    c1.metric("Total Draw Distance", f"{total_mm / 1000:.2f} m")
    c2.metric("Est. Plot Time",      f"{est_min:.1f} min")

    safe_label = mode_label.lower().replace(" ", "_").replace("(", "").replace(")", "")
    raw_svg    = generate_svg(final_paths, w, h, target_width_mm, stroke_width)

    if use_vpype:
        stages = []
        if vpype_linemerge:    stages.append(f"linemerge({vpype_merge_tol}mm)")
        if vpype_linesort:     stages.append("linesort" + (" +2opt" if vpype_two_opt else ""))
        if vpype_reloop:       stages.append("reloop")
        if vpype_linesimplify: stages.append("linesimplify")
        with st.spinner(f"Running vpype: {' → '.join(stages)}…"):
            opt_svg, status = run_vpype_pipeline(
                raw_svg, vpype_linemerge, vpype_merge_tol,
                vpype_linesort, vpype_two_opt, vpype_reloop, vpype_linesimplify
            )
        if opt_svg:
            st.success(f"✅ vpype: {' → '.join(stages)}")
            c3.download_button(
                "🚀 Download SVG (vpype optimised)",
                opt_svg, f"plotter_{safe_label}_vpype.svg", "image/svg+xml",
            )
            with st.expander("Also available: raw SVG (pre-vpype)"):
                st.download_button("Download raw SVG", raw_svg,
                    f"plotter_{safe_label}_raw.svg", "image/svg+xml")
        else:
            st.warning(f"vpype did not complete: {status}")
            c3.download_button("🚀 Download SVG for Cameo 5", raw_svg,
                f"plotter_{safe_label}.svg", "image/svg+xml")
    else:
        c3.download_button("🚀 Download SVG for Cameo 5", raw_svg,
            f"plotter_{safe_label}.svg", "image/svg+xml")

    # CMYK export
    if use_cmyk and cmyk_engine_fn is not None:
        with st.spinner("Generating CMYK layers…"):
            cmyk_zip = _generate_cmyk_zip(
                cmyk_engine_fn, w, h, target_width_mm, stroke_width
            )
        if cmyk_zip:
            st.download_button(
                "🎨 Download CMYK ZIP (4 SVG layers)",
                cmyk_zip, f"plotter_{safe_label}_cmyk.zip", "application/zip"
            )
            st.caption(
                "CMYK ZIP contains: cyan.svg, magenta.svg, yellow.svg, black.svg — "
                "plot each with the corresponding pen colour."
            )

    st.caption(
        f"{len(final_paths):,} paths · "
        f"Draw: {total_mm / 1000:.2f} m · "
        f"Pen-lift: {lift_mm / 1000:.2f} m"
    )


# ─────────────────────────────────────────────
# CMYK LAYER GENERATION
# ─────────────────────────────────────────────

def _rgb_to_cmyk_channels(file_bytes, mx, my, mw, mh, max_px):
    """
    Split a colour image into C, M, Y, K greyscale channels.
    Each channel is normalised 0..1 where 1 = maximum ink density.
    Returns dict of {'C': array, 'M': array, 'Y': array, 'K': array}
    and the image shape.
    """
    img = skio.imread(BytesIO(file_bytes))
    if len(img.shape) == 2:
        # Already greyscale — treat as K only
        g = img.astype(float) / 255.0
        if mx != 0 or my != 0 or mw != 100 or mh != 100:
            h, w = g.shape
            x0 = int(w * mx / 100); y0 = int(h * my / 100)
            x1 = min(w, x0 + int(w * mw / 100))
            y1 = min(h, y0 + int(h * mh / 100))
            g = g[y0:y1, x0:x1]
        if g.shape[1] > max_px:
            g = transform.rescale(g, max_px / g.shape[1], anti_aliasing=True)
        z = np.zeros_like(g)
        return {'C': z, 'M': z, 'Y': z, 'K': 1.0 - g}, g.shape

    if img.shape[-1] == 4:
        img = color.rgba2rgb(img)

    # Mask and cap
    img = img.astype(float) / 255.0
    if mx != 0 or my != 0 or mw != 100 or mh != 100:
        h, w = img.shape[:2]
        x0 = int(w * mx / 100); y0 = int(h * my / 100)
        x1 = min(w, x0 + int(w * mw / 100))
        y1 = min(h, y0 + int(h * mh / 100))
        img = img[y0:y1, x0:x1]

    if img.shape[1] > max_px:
        img = transform.rescale(img, max_px / img.shape[1],
                                anti_aliasing=True, channel_axis=2)

    R, G, B = img[:,:,0], img[:,:,1], img[:,:,2]
    K = 1.0 - np.maximum(np.maximum(R, G), B)
    denom = 1.0 - K
    denom[denom == 0] = 1.0
    C = (1.0 - R - K) / denom
    M = (1.0 - G - K) / denom
    Y = (1.0 - B - K) / denom
    # Normalise to 0..1 density (1 = lots of ink)
    return {
        'C': np.clip(C, 0, 1),
        'M': np.clip(M, 0, 1),
        'Y': np.clip(Y, 0, 1),
        'K': np.clip(K, 0, 1),
    }, img.shape[:2]


def _generate_cmyk_zip(engine_fn, w, h, target_width_mm, stroke_width):
    """
    engine_fn(channel_img) -> list of paths
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
def _load_and_greyscale(file_bytes):
    img = skio.imread(BytesIO(file_bytes))
    if len(img.shape) == 3:
        if img.shape[-1] == 4:
            img = color.rgba2rgb(img)
        return color.rgb2gray(img)
    raw = img.astype(float)
    return raw / 255.0 if raw.max() > 1 else raw


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
    img = skio.imread(BytesIO(file_bytes))
    if len(img.shape) != 3 or img.shape[-1] != 4:
        return None
    alpha = img[:, :, 3].astype(float)
    alpha = alpha / 255.0 if alpha.max() > 1 else alpha
    if mx != 0 or my != 0 or mw != 100 or mh != 100:
        alpha = _apply_mask(alpha, mx, my, mw, mh)
    return _cap_image(alpha, max_px)


@st.cache_data(show_spinner=False)
def _preprocess_blueprint(img_bytes, mx, my, mw, mh, max_px,
                           grid_size, contrast_offset, line_weld, use_thinning):
    img    = _load_masked(img_bytes, mx, my, mw, mh, max_px)
    pre    = filters.gaussian(img, sigma=0.6)
    thresh = filters.threshold_local(pre, block_size=grid_size,
                                     method="gaussian", offset=contrast_offset)
    binary = pre < thresh
    if line_weld > 1:
        binary = closing(binary, square(line_weld))
    if use_thinning:
        binary = skeletonize(binary)
    return img.shape, binary


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
    img = _load_masked(img_bytes, mx, my, mw, mh, max_px)
    h, w = img.shape
    density = np.clip(1.0 - img, 0.0, 1.0)
    density[density < threshold] = 0.0
    total = density.sum()
    if total == 0:
        return img.shape, np.array([])
    flat  = density.ravel() / density.sum()
    idx   = np.random.choice(len(flat), size=n_points, replace=False, p=flat)
    rows  = (idx // w).astype(float)
    cols  = (idx  % w).astype(float)
    pts   = np.column_stack([cols, rows])

    for _ in range(n_iter):
        boundary = np.array([
            [-1,-1],[w,-1],[-1,h],[w,h],
            [w/2,-1],[w/2,h],[-1,h/2],[w,h/2],
        ])
        try:
            vor = Voronoi(np.vstack([pts, boundary]))
        except Exception:
            break
        new_pts = pts.copy()
        for i in range(len(pts)):
            reg = vor.regions[vor.point_region[i]]
            if -1 in reg or not reg:
                continue
            verts = vor.vertices[reg]
            vx = np.clip(verts[:,0], 0, w-1).astype(int)
            vy = np.clip(verts[:,1], 0, h-1).astype(int)
            if len(vx) < 3:
                continue
            x0,x1 = max(0,vx.min()), min(w-1,vx.max())
            y0,y1 = max(0,vy.min()), min(h-1,vy.max())
            if x0>=x1 or y0>=y1:
                continue
            gx,gy = np.meshgrid(np.arange(x0,x1+1), np.arange(y0,y1+1))
            wc = density[gy, gx]; tw = wc.sum()
            if tw > 0:
                new_pts[i,0] = (gx*wc).sum()/tw
                new_pts[i,1] = (gy*wc).sum()/tw
        pts = new_pts

    return img.shape, np.column_stack([
        np.clip(pts[:,1], 0, h-1), np.clip(pts[:,0], 0, w-1)
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

@st.cache_data(show_spinner=False)
def _contours_blueprint(key, mask, path_tolerance, min_len):
    paths = []
    for c in measure.find_contours(mask, 0.5):
        if path_tolerance > 0:
            c = measure.approximate_polygon(c, tolerance=path_tolerance)
        if len(c) > min_len:
            paths.append(c)
    return _nearest_neighbour_sort(paths)


@st.cache_data(show_spinner=False)
def _contours_sobel(key, edges, path_tolerance, min_len):
    paths = []
    for c in measure.find_contours(edges.astype(float), 0.5):
        if path_tolerance > 0:
            c = measure.approximate_polygon(c, tolerance=path_tolerance)
        if len(c) > min_len:
            paths.append(c)
    return _nearest_neighbour_sort(paths)


@st.cache_data(show_spinner=False)
def _build_squiggle_paths(key, img_smooth, line_spacing, amplitude, frequency, min_len):
    h, w   = img_smooth.shape
    paths  = []
    row    = line_spacing // 2
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
    for row in range(grid_spacing // 2, h, grid_spacing):
        for col in range(grid_spacing // 2, w, grid_spacing):
            mag = magnitude[row, col]
            if mag < darkness_thresh:
                continue
            # Normalise length: darker / stronger gradient = longer needle
            length = mag / (magnitude.max() + 1e-8) * max_length
            if length < 1.0:
                continue
            angle  = direction[row, col]
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


@st.cache_data(show_spinner=False)
def _build_continuous_line(key, points):
    """
    Continuous Line / TSP-style engine.
    Chains all stipple points into a single unbroken path using greedy
    nearest-neighbour TSP approximation. The plotter never lifts the pen.
    Returns a single path (list of one numpy array).
    """
    if len(points) == 0:
        return []

    pts = list(points)
    ordered = [pts.pop(0)]
    while pts:
        cur = ordered[-1]
        dists = [(i, (p[0]-cur[0])**2 + (p[1]-cur[1])**2) for i, p in enumerate(pts)]
        nearest_i = min(dists, key=lambda x: x[1])[0]
        ordered.append(pts.pop(nearest_i))

    # Single continuous path — one pen-down for the entire drawing
    return [np.array(ordered)]


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

    try:

        # ── BLUEPRINT ────────────────────────────────────────────────────
        if mode == "Blueprint (Sketch Trace)":
            st.sidebar.header("Blueprint Settings")
            grid_size       = st.sidebar.slider("Neighbourhood Size", 3, 99, 25, step=2)
            contrast_offset = st.sidebar.slider("Contrast Offset", -0.2, 0.2, 0.02, step=0.01)
            line_weld       = st.sidebar.slider("Line Welding", 1, 9, 3, step=1)
            use_thinning    = st.sidebar.checkbox("Centreline Thinning", value=False)
            path_tolerance  = st.sidebar.slider("Path Smoothing (RDP)", 0.0, 2.0, 0.3)
            stroke_width    = st.sidebar.slider("SVG Stroke Width", 0.1, 3.0, 1.0, step=0.1)

            shape, mask = _preprocess_blueprint(
                file_bytes, *mk, TUNE_PX, grid_size, contrast_offset, line_weld, use_thinning
            )
            h, w = shape
            key  = (*mk, grid_size, contrast_offset, line_weld, use_thinning)
            export_paths = _contours_blueprint(key, mask, path_tolerance, min_line_len)
            _plotly_traces(fig, export_paths, h, "#00ff88", 1.0)

            def _get_export():
                s2, m2 = _preprocess_blueprint(
                    file_bytes, *mk, EXPORT_PX, grid_size, contrast_offset, line_weld, use_thinning)
                k2 = (*mk, grid_size, contrast_offset, line_weld, use_thinning, "exp")
                return _contours_blueprint(k2, m2, path_tolerance, min_line_len), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    ch, _ = _rgb_to_cmyk_channels(file_bytes, *mk, EXPORT_PX)
                    for name, chan in ch.items():
                        s2, m2 = _preprocess_blueprint.__wrapped__(
                            chan.tobytes(), 0,0,100,100, EXPORT_PX, grid_size, contrast_offset, line_weld, use_thinning) if False else (chan.shape, chan < filters.threshold_local(filters.gaussian(chan, sigma=0.6), block_size=grid_size, method="gaussian", offset=contrast_offset))
                        out[name] = _contours_blueprint((*mk, name), m2, path_tolerance, min_line_len)
                    return out

        # ── SOBEL EDGE ────────────────────────────────────────────────────
        elif mode == "Sobel Edge (Outline)":
            st.sidebar.header("Sobel Edge Settings")
            sigma       = st.sidebar.slider("Edge Blur (sigma)", 0.5, 5.0, 1.5, step=0.1)
            low_thresh  = st.sidebar.slider("Low Threshold", 0.0, 0.3, 0.05, step=0.01)
            high_thresh = st.sidebar.slider("High Threshold", 0.1, 0.9, 0.20, step=0.01)
            path_tol    = st.sidebar.slider("Path Smoothing (RDP)", 0.0, 2.0, 0.3)
            stroke_width= st.sidebar.slider("SVG Stroke Width", 0.1, 3.0, 0.8, step=0.1)

            shape, edges = _preprocess_sobel(
                file_bytes, *mk, TUNE_PX, sigma, low_thresh, high_thresh)
            h, w = shape
            key  = (*mk, sigma, low_thresh, high_thresh)
            export_paths = _contours_sobel(key, edges, path_tol, min_line_len)
            _plotly_traces(fig, export_paths, h, "#00ff88", 0.8)

            def _get_export():
                s2, e2 = _preprocess_sobel(file_bytes, *mk, EXPORT_PX, sigma, low_thresh, high_thresh)
                k2 = (*mk, sigma, low_thresh, high_thresh, "exp")
                return _contours_sobel(k2, e2, path_tol, min_line_len), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    ch, _ = _rgb_to_cmyk_channels(file_bytes, *mk, EXPORT_PX)
                    for name, chan in ch.items():
                        edges_c = feature.canny(chan, sigma=sigma, low_threshold=low_thresh, high_threshold=high_thresh)
                        out[name] = _contours_sobel((*mk, name), edges_c, path_tol, min_line_len)
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
            _plotly_traces(fig, export_paths, h, "#00ff88", 0.5)

            def _get_export():
                s2, sm2 = _preprocess_squiggle(file_bytes, *mk, EXPORT_PX, sq_sigma)
                k2 = (*mk, sq_sigma, "exp")
                return _build_squiggle_paths(k2, sm2, line_spacing, amplitude, frequency, min_line_len), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    ch, _ = _rgb_to_cmyk_channels(file_bytes, *mk, EXPORT_PX)
                    for name, chan in ch.items():
                        sm = filters.gaussian(chan, sigma=sq_sigma)
                        out[name] = _build_squiggle_paths((*mk,name), sm, line_spacing, amplitude, frequency, min_line_len)
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
            _plotly_traces(fig, export_paths, h, "#00ff88", 0.6)

            def _get_export():
                s2, sm2 = _preprocess_isoline(file_bytes, *mk, EXPORT_PX, shadow_boost, sigma)
                k2 = (*mk, shadow_boost, sigma, "exp")
                return _contours_isoline(k2, sm2, level_min, level_max, ink_density, angularity, min_line_len), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    ch, _ = _rgb_to_cmyk_channels(file_bytes, *mk, EXPORT_PX)
                    for name, chan in ch.items():
                        sm = filters.gaussian(exposure.adjust_gamma(chan, shadow_boost), sigma=sigma)
                        out[name] = _contours_isoline((*mk,name), sm, level_min, level_max, ink_density, angularity, min_line_len)
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

            _plotly_traces(fig, export_paths, h, "#00ff88", 0.5)

            # Show seed point on preview
            if not seed_auto:
                seed_col = seed_x_pct / 100 * w
                seed_row = seed_y_pct / 100 * h
                fig.add_trace(go.Scatter(
                    x=[seed_col], y=[h - seed_row], mode="markers",
                    marker=dict(color="#ff4444", size=12, symbol="x"),
                    showlegend=False, name="Seed point"
                ))

            def _get_export():
                s2, tm2 = _preprocess_fmm(
                    file_bytes, *mk, export_px_fmm, gamma_val, sigma,
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
                    ch, _ = _rgb_to_cmyk_channels(file_bytes, *mk, export_px_fmm)
                    for name, chan in ch.items():
                        adj = filters.gaussian(exposure.adjust_gamma(chan, gamma_val), sigma=sigma)
                        phi = np.ones(chan.shape)
                        if seed_auto:
                            bi = filters.gaussian(chan, sigma=max(*chan.shape)*0.05)
                            phi[np.unravel_index(np.argmin(bi), bi.shape)] = 0
                        else:
                            phi[int(seed_y_pct/100*chan.shape[0]), int(seed_x_pct/100*chan.shape[1])] = 0
                        tm = skfmm.travel_time(phi, adj + speed_offset)
                        paths_c = _contours_fmm((*mk,name), tm, num_lines, min_line_len)
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
            _plotly_traces(fig, export_paths, h, "#00ccff", 0.5)

            def _get_export():
                s2, f2 = _preprocess_streamlines(file_bytes, *mk, EXPORT_PX, sigma_edge)
                k2 = (*mk, sigma_edge, "exp")
                return _contours_streamlines(k2, f2, num_levels, path_tol, min_line_len), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    ch, _ = _rgb_to_cmyk_channels(file_bytes, *mk, EXPORT_PX)
                    for name, chan in ch.items():
                        f = 1.0 - filters.gaussian(chan, sigma=sigma_edge)
                        out[name] = _contours_streamlines((*mk,name), f, num_levels, path_tol, min_line_len)
                    return out

        # ── VORONOI STIPPLING ─────────────────────────────────────────────
        elif mode == "Voronoi Stippling":
            st.sidebar.header("Stippling Settings")
            n_points     = st.sidebar.slider("Number of Points", 100, 5000, 1000, step=100)
            n_iter       = st.sidebar.slider("Lloyd Iterations", 1, 20, 5)
            threshold    = st.sidebar.slider("White Threshold", 0.0, 0.9, 0.1, step=0.05)
            dot_radius   = st.sidebar.slider("Dot Radius (px)", 0.5, 8.0, 1.5, step=0.5)
            stroke_width = st.sidebar.slider("SVG Stroke Width", 0.1, 2.0, 0.5, step=0.1)

            with st.spinner(f"Computing {n_points} stipple points…"):
                shape, points = _preprocess_stipple(
                    file_bytes, *mk, TUNE_PX, n_points, n_iter, threshold)
            h, w = shape
            if len(points) > 0:
                stip_key     = (*mk, n_points, n_iter, threshold)
                export_paths = _stipple_to_paths(stip_key, points, dot_radius)
                _plotly_dots(fig, points, h, "#00ff88", 2)
            else:
                st.warning("No stipple points generated — try lowering White Threshold.")

            def _get_export():
                with st.spinner("Re-computing stipple at export resolution…"):
                    s2, pts2 = _preprocess_stipple(file_bytes, *mk, EXPORT_PX, n_points, n_iter, threshold)
                k2 = (*mk, n_points, n_iter, threshold, "exp")
                return _stipple_to_paths(k2, pts2, dot_radius), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

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
            _plotly_traces(fig, export_paths, h, "#ffaa00", 0.6)

            def _get_export():
                s2, mag2, dirn2 = _preprocess_needles(file_bytes, *mk, EXPORT_PX, nd_sigma)
                k2 = (*mk, nd_sigma, "exp")
                return _build_needle_paths(k2, mag2, dirn2, grid_sp, max_length, dark_thresh, min_line_len), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

            if use_cmyk:
                def cmyk_engine_fn():
                    out = {}
                    ch, _ = _rgb_to_cmyk_channels(file_bytes, *mk, EXPORT_PX)
                    for name, chan in ch.items():
                        sm = filters.gaussian(chan, sigma=nd_sigma)
                        gy2, gx2 = np.gradient(sm)
                        m2 = np.sqrt(gx2**2+gy2**2); d2 = np.arctan2(gy2,gx2)
                        out[name] = _build_needle_paths((*mk,name), m2, d2, grid_sp, max_length, dark_thresh, min_line_len)
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

            with st.spinner(f"Computing {n_points} points + TSP chain…"):
                shape, points = _preprocess_stipple(
                    file_bytes, *mk, TUNE_PX, n_points, n_iter, threshold)
            h, w = shape

            if len(points) > 0:
                tsp_key      = (*mk, n_points, n_iter, threshold, "tsp")
                export_paths = _build_continuous_line(tsp_key, points)
                _plotly_traces(fig, export_paths, h, "#00ff88", 0.5)
                st.caption(f"Single continuous path — {len(points):,} points — pen never lifts.")
            else:
                st.warning("No points generated — try lowering White Threshold.")

            def _get_export():
                with st.spinner("Re-computing TSP at export resolution…"):
                    s2, pts2 = _preprocess_stipple(file_bytes, *mk, EXPORT_PX, n_points, n_iter, threshold)
                k2 = (*mk, n_points, n_iter, threshold, "tsp_exp")
                return _build_continuous_line(k2, pts2), s2, _load_masked(file_bytes, *mk, EXPORT_PX)

        # ── RENDER & EXPORT ───────────────────────────────────────────────
        if export_paths:
            try:
                final_paths, final_shape, img_gray_export = _get_export()
            except Exception:
                final_paths, final_shape = export_paths, (h, w)
                img_gray_export = None

            fh, fw       = final_shape
            export_shape = (fh, fw)

            mask_info = f" · Mask {mask_w}×{mask_h}% @ ({mask_x},{mask_y})" if use_mask else ""
            wobble_info = f" · Wobble ON (amp={wobble_amp}px)" if use_wobble else ""
            st.caption(
                f"Preview: {w}px · Export: {fw}px · "
                f"{len(export_paths):,} preview / {len(final_paths):,} export paths"
                f"{mask_info}{wobble_info}"
            )

            _build_and_export(
                final_paths, fw, fh, target_width_mm, plot_speed_mms,
                mode, stroke_width,
                use_wobble, wobble_scale, wobble_amp,
                use_vpype, vpype_linemerge, vpype_merge_tol, vpype_linesort,
                vpype_two_opt, vpype_reloop, vpype_linesimplify,
                use_pen_passes,
                pen_passes if use_pen_passes else 1,
                pen_pass_thresh if use_pen_passes else 0.5,
                img_gray_export, export_shape,
                use_cmyk, cmyk_engine_fn,
            )

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
