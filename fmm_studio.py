import streamlit as st
import numpy as np
import skfmm
from skimage import io, color, transform, exposure, measure, filters
import plotly.graph_objects as go
import os
import random

# Page configuration
st.set_page_config(layout="wide", page_title="Universal Plotter Studio")
st.title("🎨 Universal Plotter Studio v3.2")
st.markdown("FMM Waves, Structured Squiggles, and Contour Streamlines")

# --- SIDEBAR: GLOBAL SETTINGS ---
st.sidebar.header("1. Global Settings")
target_width_mm = st.sidebar.number_input("Physical Width (mm)", value=200)
plot_speed_mms = st.sidebar.slider("Estimated Plot Speed (mm/s)", 10, 200, 50)

# --- SIDEBAR: MODE SELECTION ---
st.sidebar.header("2. Choose Art Style")
mode = st.sidebar.radio("Art Engine", 
                        ["Fast Marching (Topographic)", 
                         "Squiggle (Structured Shading)", 
                         "Line Sketch (Contour Streamlines)"])

uploaded_file = st.sidebar.file_uploader("Upload Image", type=['jpg', 'jpeg', 'png'])

# --- SIDEBAR: ENGINE SPECIFIC SETTINGS ---
if mode == "Fast Marching (Topographic)":
    st.sidebar.header("FMM Settings")
    num_lines = st.sidebar.slider("Line Density", 50, 500, 200)
    gamma = st.sidebar.slider("Gamma", 0.1, 1.0, 0.4)
    blur = st.sidebar.slider("Smoothing (Blur)", 0.0, 5.0, 1.5)
    speed_offset = st.sidebar.slider("Shadow Density", 0.01, 0.1, 0.02)

elif mode == "Squiggle (Structured Shading)":
    st.sidebar.header("Squiggle Settings")
    line_spacing = st.sidebar.slider("Line Spacing", 2, 20, 8)
    amplitude = st.sidebar.slider("Scribble Intensity", 0.5, 10.0, 3.0)
    img_blur = st.sidebar.slider("Image Softness", 0.0, 5.0, 1.0)

elif mode == "Line Sketch (Contour Streamlines)":
    st.sidebar.header("Streamline Settings")
    line_count = st.sidebar.slider("Number of Streamlines", 500, 10000, 3000)
    max_steps = st.sidebar.slider("Stroke Continuity", 5, 100, 30)
    step_size = st.sidebar.slider("Step Size", 1.0, 5.0, 2.0)
    flow_smoothness = st.sidebar.slider("Flow Smoothness", 1, 10, 3)

if uploaded_file is not None:
    # 1. Base Image Processing
    img = io.imread(uploaded_file)
    if img.shape[-1] == 4: img = color.rgba2rgb(img)
    img_gray = color.rgb2gray(img)
    img_gray = transform.rescale(img_gray, 0.6, anti_aliasing=True)
    img_gray = exposure.rescale_intensity(img_gray)
    
    h, w = img_gray.shape
    fig = go.Figure()
    total_dist_px = 0
    all_paths = []

    # --- ENGINE 1: FAST MARCHING ---
    if mode == "Fast Marching (Topographic)":
        img_proc = filters.gaussian(img_gray, sigma=blur)
        img_proc = exposure.adjust_gamma(img_proc, gamma)
        phi = np.ones((h, w))
        phi[h // 2, w // 2] = 0
        t_map = skfmm.travel_time(phi, img_proc + speed_offset)
        levels = np.linspace(t_map.min(), t_map.max(), num_lines)
        for level in levels:
            contours = measure.find_contours(t_map, level)
            for c in contours:
                all_paths.append(c)
                total_dist_px += np.sum(np.sqrt(np.sum(np.diff(c, axis=0)**2, axis=1)))
                fig.add_trace(go.Scatter(x=c[:, 1], y=h - c[:, 0], mode='lines', 
                                         line=dict(color='#00d4ff', width=0.8), showlegend=False))

    # --- ENGINE 2: SQUIGGLE ---
    elif mode == "Squiggle (Structured Shading)":
        img_proc = filters.gaussian(img_gray, sigma=img_blur)
        for row in range(0, h, line_spacing):
            line = []
            cols = range(w) if (row // line_spacing) % 2 == 0 else range(w-1, -1, -1)
            for col in cols:
                darkness = 1.0 - img_proc[row, col]
                offset = np.sin(col * 0.8) * (darkness * amplitude * line_spacing)
                line.append([row + offset, col])
            c = np.array(line)
            all_paths.append(c)
            total_dist_px += np.sum(np.sqrt(np.sum(np.diff(c, axis=0)**2, axis=1)))
            fig.add_trace(go.Scatter(x=c[:, 1], y=h - c[:, 0], mode='lines', 
                                     line=dict(color='#ffcc00', width=0.7), showlegend=False))

    # --- ENGINE 3: CONTOUR STREAMLINES ---
    elif mode == "Line Sketch (Contour Streamlines)":
        img_blur_sketch = filters.gaussian(img_gray, sigma=flow_smoothness)
        gx = filters.scharr_v(img_blur_sketch)
        gy = filters.scharr_h(img_blur_sketch)
        
        for _ in range(line_count):
            ry, rx = random.randint(0, h-1), random.randint(0, w-1)
            if random.random() < (1.0 - img_gray[ry, rx])**2:
                path = [[ry, rx]]
                curr_y, curr_x = float(ry), float(rx)
                for _ in range(max_steps):
                    iy, ix = int(curr_y), int(curr_x)
                    if not (0 <= iy < h and 0 <= ix < w): break
                    angle = np.arctan2(gy[iy, ix], gx[iy, ix]) + np.pi/2
                    curr_y += np.sin(angle) * step_size
                    curr_x += np.cos(angle) * step_size
                    path.append([curr_y, curr_x])
                    if 0 <= int(curr_y) < h and 0 <= int(curr_x) < w:
                        if img_gray[int(curr_y), int(curr_x)] > 0.9: break
                
                if len(path) > 2:
                    c = np.array(path)
                    all_paths.append(c)
                    total_dist_px += np.sum(np.sqrt(np.sum(np.diff(c, axis=0)**2, axis=1)))
                    fig.add_trace(go.Scatter(x=c[:, 1], y=h - c[:, 0], mode='lines', 
                                             line=dict(color='#ffffff', width=0.6), showlegend=False))

    # --- METRICS & PREVIEW ---
    pixel_to_mm_scale = target_width_mm / w
    total_dist_mm = total_dist_px * pixel_to_mm_scale
    est_minutes = (total_dist_mm / plot_speed_mms) * 1.2 / 60
    
    col1, col2 = st.columns(2)
    col1.metric("Total Line Distance", f"{total_dist_mm/1000:.2f} m")
    col2.metric("Est. Plot Time", f"{est_minutes:.1f} min")

    fig.update_layout(width=850, height=850, plot_bgcolor='#0E1117', paper_bgcolor='#0E1117',
                      xaxis=dict(visible=False), yaxis=dict(visible=False, scaleanchor="x"),
                      margin=dict(l=0, r=0, t=0, b=0), dragmode='pan')
    st.plotly_chart(fig, use_container_width=True)

    # --- EXPORT ---
    if st.button("🚀 Export SVG"):
        svg_filename = "plotter_studio_output.svg"
        final_h_mm = h * pixel_to_mm_scale
        with open(svg_filename, 'w') as f:
            f.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{target_width_mm}mm" height="{final_h_mm}mm" viewBox="0 0 {w} {h}">\n')
            for path in all_paths:
                path_str = "M " + " L ".join([f"{p[1]:.2f},{h - p[0]:.2f}" for p in path])
                f.write(f'  <path d="{path_str}" fill="none" stroke="black" stroke-width="0.3" />\n')
            f.write('</svg>')
        st.success(f"Exported! Saved as {svg_filename}.")