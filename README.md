# 🎨 Universal Plotter Studio v5.5.2
An interactive generative art studio for pen plotters (Silhouette Cameo 5, AxiDraw, etc.).
Nine art engines, image masking, Perlin noise wobble, full vpype pipeline,
multi-pass pen pressure, and CMYK layer export.

<img width="2447" height="1343" alt="Screenshot 2026-10-14 at 1 44 31 PM" src="https://github.com/user-attachments/assets/6a557681-cfb7-4e43-964c-a4c292513549" />


---

## 🚀 The Nine Art Engines

### 1. Blueprint (Sketch Trace)
Adaptive local thresholding extracts drawn strokes from existing ink/pencil drawings.
Best for scanned hand-drawn art. Morphological welding closes gaps; **Centreline
(single stroke)** (on by default) skeletonises each stroke and traces its middle
once, so every drawn line is plotted once instead of as a two-sided outline.
**Spur Removal** trims the short hairs that bumps and ink blots grow on the
skeleton. Turn Centreline off to trace stroke outlines instead.

### 2. Sobel Edge (Outline)
Canny edge detection + contour tracing. Crisp single-stroke outlines from high-contrast
ink drawings. Based on DrawingBotV3's Sketch Sobel Edges PFM.
**Key controls:** Edge Blur, Low/High Threshold (hysteresis).

### 3. Squiggle (Scanline Shading)
Horizontal scanlines that oscillate based on pixel darkness — the SquiggleDraw technique.
Dark areas = high-amplitude waves, light areas = flat lines. Continuous single-stroke
output — minimal pen lifts. Excellent for portraits with a ballpoint pen.
**Key controls:** Line Spacing, Max Amplitude, Wave Frequency.

### 4. Isoline (Tonal Contour)
Traces lines of equal brightness at many intensity levels simultaneously. Dark areas
produce tight contour clusters reading as shadow hatching. Classic plotter tonal
rendering technique.

### 5. Fast Marching (Topographic) *(upgraded in v5.5)*
Wave propagation from a seed point — produces topographic ring aesthetics.

**New in v5.5:**
- **Manual seed point** — place the wave origin deliberately using X/Y % sliders.
  For a portrait, try X=50%, Y=40% (between the eyebrows) to reproduce the
  Contour-V diamond/chevron portrait ring style. The seed point renders as a
  red × in the preview.
- **Contour Mode toggle** — Angular (sharp, follows brightness edges) vs Smooth
  (Gaussian-softened contours, more organic).
- **Speed Offset slider** — controls the minimum wave speed added to the speed
  field. Lower values let dark regions compress contours more tightly
  (denser shadow detail); higher values keep ring spacing more even
  throughout. Previously hardcoded at 0.02.
- **Scale %** — downscales the working image specifically for this engine,
  independent of the global preview/export resolution. Useful for fast
  iteration on large source images.
- **Skip transparent** — for PNGs with an alpha channel, clips contour
  segments that fall in fully transparent regions so cut-out/isolated
  subjects don't pick up stray rings from the (invisible) background.

### 6. Line Sketch (Streamlines)
Inverts image brightness into a scalar field and traces iso-contours. Dark strokes
become dense clusters of flowing lines.

### 7. Voronoi Stippling
Adrian Secord's Weighted Voronoi algorithm with Lloyd's relaxation (same method as
DrawingBotV3's Voronoi Stippling PFM). Points cluster in dark areas, spread in
highlights. Each dot exports as a small polygon path.

### 8. Needles (Oriented Dashes) *(new in v5.5)*
Short line segments placed in a grid, oriented by the local image gradient direction.
Inspired by Mitxela's Needles algorithm and the plotter community's oriented-dash
technique. Dark areas and strong edges get longer, denser needles. Light/flat areas
get sparse short marks or nothing at all. Very different from stippling — the marks
have direction and follow the image's structural flow.
**Key controls:** Grid Spacing, Max Needle Length, Darkness Threshold, Blur.

### 9. Continuous Line (TSP) *(new in v5.5)*
Single unbroken line visiting all points — the pen never lifts for the entire drawing.
Inspired by PINTR and StringyPlotter. Uses Voronoi stippling for point placement
(Lloyd's relaxation), then chains all points into one continuous path using a greedy
nearest-neighbour TSP approximation.
**Key controls:** Number of Points, White Threshold, Lloyd Iterations.
**How it works:** points are chained with a cKDTree nearest-neighbour tour, then a
time-limited 2-opt pass (2 s while tuning, 10 s on export) removes the long crossing
jumps the greedy chain leaves behind (typically ~10% shorter). True TSP is NP-hard, so
this is a good local optimum, not the optimum.
**Note:** `vpype linesort --two-opt` does *not* improve this engine's output — it
reorders separate paths, and this engine emits a single path, so there is nothing for
it to reorder. Raise the point count or 2-opt time instead.

---

## ✂️ Image Masking (Section 2)
Define a rectangular crop region as % of image dimensions before any engine runs.
Excludes signatures, borders, or unwanted background without editing the source file.

---

## 〰️ Perlin Noise Wobble (Section 4) *(new in v5.5)*
Applies coherent Perlin-style noise displacement to all output paths after the engine
runs but before vpype and pen passes. Gives a hand-drawn, organic quality to clean
computer-generated lines — inspired by LingDong-'s linedraw technique.

Two independent noise fields displace X and Y separately so the wobble feels
genuinely two-dimensional rather than a simple wave.

**Key controls:**
- **Noise Scale** — spatial frequency. Lower = large smooth waves. Higher = tight rapid wobble.
- **Wobble Amplitude** — maximum displacement in pixels.

---

## 🔧 vpype Export Pipeline (Section 5)
Full community-recommended pipeline with individual toggles:

| Stage | What it does |
|---|---|
| `linemerge` | Joins endpoints within tolerance into continuous strokes |
| `linesort` | Reorders paths to minimise pen-up travel |
| Two-Opt | Multi-pass improvement after greedy sort |
| `reloop` | Randomises closed-path start positions |
| `linesimplify` | Removes redundant nodes — smaller SVG, faster plotting |

Keep vpype **OFF** while tuning. Turn **ON** before final export.

---

## ✏️ Pen Passes (Section 6)
Duplicates dark paths N times for multi-pass ballpoint ink density.
Shadow Threshold controls which paths qualify for extra passes.

---

## 🎨 CMYK Layer Export (Section 7) *(new in v5.5)*
Splits the uploaded colour image into C, M, Y, K channels and runs the current
engine independently on each. Exports a ZIP containing four SVGs:

- `cyan.svg` — plot with cyan pen
- `magenta.svg` — plot with magenta pen
- `yellow.svg` — plot with yellow pen
- `black.svg` — plot with black pen

Available on all engines except Voronoi Stippling and Continuous Line (which use
greyscale point placement; the app shows a warning if CMYK is on with those). Channels
with no ink are skipped. Enables full multi-pen colour plotting on the Cameo 5.
Note: wobble, pen passes and vpype are applied to the main SVG only, not the CMYK layers.

---

## ✅ Changelog

### v5.5.2
- **Build export button** — the full-resolution export (wobble, pen passes, vpype,
  CMYK ZIP) now runs only when clicked and is kept in session state; sliders only
  update the fast preview. A warning shows if settings changed since the last build.
- **Preview matches export** — pixel-unit settings (blur sigmas, RDP tolerances,
  squiggle spacing/amplitude, needle grid/length, dot radius, Blueprint
  neighbourhood/welding, wobble) are scaled by the real preview→export width ratio.
  Canny and Needles gradient thresholds are scaled by its inverse.
- **CMYK fix** — channels are ink density; Blueprint, Squiggle, Isoline, Fast Marching
  and Streamlines now receive brightness. Blueprint CMYK now honours Line Welding and
  Centreline Thinning. RGBA/16-bit sources no longer produce near-black CMYK.
- **Wobble** is vectorised (~35x faster), shown in the preview, and its amplitude/scale
  are defined at preview resolution.
- **Continuous Line** — cKDTree nearest neighbour + time-limited 2-opt.
- **Image loading** — EXIF rotation, 16-bit greyscale, gray+alpha and palette alpha.
- Seeded stippling; Lloyd centroids use the true Voronoi cell; stipple no longer
  errors when fewer dark pixels than points exist; plot-time estimate includes
  pen-up travel; one version number across README, pyproject and the app.

### v5.5.1
- **Fast Marching: Speed Offset slider** — minimum wave speed is now tunable
  (was hardcoded at 0.02). Lower = denser contour compression in shadows.
- **Fast Marching: Scale %** — per-engine downscale control for faster
  iteration on large images, independent of the global preview/export size.
- **Fast Marching: Skip transparent** — clips contour lines that fall in
  transparent regions of alpha-channel PNGs.

### v5.5
- **Fast Marching upgraded** — manual seed point (X/Y % sliders) + Contour Mode
  toggle (Angular vs Smooth). Seed renders as red × in preview.
- **New engine: Needles (Oriented Dashes)** — gradient-oriented short line segments.
- **New engine: Continuous Line (TSP)** — single unbroken path, pen never lifts.
- **Perlin Noise Wobble** — coherent noise displacement post-process on all engines.
- **CMYK Layer Export** — four-channel SVG ZIP for multi-pen colour plotting.

### v5.4
- Squiggle (Scanline Shading) engine.
- Full vpype pipeline: linemerge → linesort → reloop → linesimplify.
- Pen Passes — multi-pass shadow ink density.

### v5.3
- Sobel Edge (Outline) engine — Canny edge detection.
- Voronoi Stippling — Adrian Secord's weighted Voronoi algorithm.
- Image Masking — rectangular crop before processing.

### v5.2
- All contour/path steps cached — near-instant slider response after first run.
- Dual resolution: 600px preview, auto 1200px on export.
- Isoline rename. vpype defaults OFF during tuning.

### v5.1
- vpype linesort integration.

### v5.0
- Caching, nearest-neighbour sort, auto downscale, Streamlines engine,
  FMM seed auto-detection. generate_fmm.py retired.

---

## 🛠 Setup

### ⚡ Faster installs with uv

### If you have `uv` installed, you can install dependencies even faster:

```bash
uv pip install -r requirements.txt

For fully reproducible builds, use the lockfile:
```
```bash
uv pip sync uv.lock
```

```bash
git clone https://github.com/iclubu/Plotter-Studio.git
cd Plotter-Studio
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Install vpype (Python 3.12 required)

```bash
brew install python@3.12
brew install pipx
pipx install vpype --python /usr/local/bin/python3.12
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### Run

```bash
alias plotter='export PATH="$HOME/.local/bin:$PATH" && cd /path/to/Plotter-Studio && source venv/bin/activate && python3 -m streamlit run fmm_studio.py'
plotter
```

---

## 📐 Cameo 5 Workflow

1. Upload image → use **Image Mask** to crop signatures/borders.
2. Choose engine. For existing drawings: **Sobel Edge** or **Blueprint**.
   For portraits: **Fast Marching** with manual seed between the eyebrows.
   For dot art: **Voronoi Stippling**. For single continuous line: **Continuous Line (TSP)**.
3. Tune sliders. The preview shows wobble; vpype, Pen Passes and CMYK only run on export.
4. When happy:
   - Enable **Perlin Noise Wobble** for hand-drawn quality (optional).
   - Enable **Pen Passes** for ballpoint shadow density (optional).
   - Enable **CMYK Export** for multi-pen colour plotting (optional).
   - Turn **vpype pipeline ON**.
5. Click **🛠 Build export**, then **Download SVG (vpype optimised)** or **Download CMYK ZIP**.
6. Open in **Silhouette Studio** → Sketch Pen mode → lock physical dimensions.

---

## 📂 Project Structure

| File | Purpose |
|---|---|
| `fmm_studio.py` | Main Streamlit app — all nine engines |
| `centreline.py` | Skeleton → single-stroke polyline tracing (Blueprint, Sobel Edge) |
| `server.py` | FastAPI plotter control backend (WebSocket + G-code queue) |
| `start.sh` | Launch script for the FastAPI server |
| `requirements.txt` | Python dependencies |
| `push.sh` | Git push helper |

`generate_fmm.py` is no longer needed and can be deleted.
