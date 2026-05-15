# 🎨 Universal Plotter Studio v3.2

An interactive generative art studio designed specifically for pen plotters (Silhouette Cameo 5, AxiDraw, etc.). This tool transforms standard images into high-fidelity vector paths using advanced mathematical engines.

<img width="1438" height="837" alt="Screenshot 2026-05-14 at 11 32 13 PM" src="https://github.com/user-attachments/assets/537df02b-f308-4a5f-bc93-d43cdf7257c1" />


## 🚀 The Three Art Engines

### 1. Fast Marching (Topographic)
* **Best for:** High-contrast portraits (e.g., Darth Vader, Batman).
* **How it works:** It uses the Fast Marching Method to simulate wave propagation across the image. Darker areas slow the "waves" down, creating topographic-like contour lines.
* **Pro Tip:** Use the "Shadow Density" slider to pack lines tighter in dark areas.

### 2. Squiggle (Structured Shading)
* **Best for:** Abstract portraits and architectural sketches.
* **How it works:** Generates horizontal scanlines that "vibrate" based on image darkness. More darkness = higher amplitude squiggles.
* **Pro Tip:** Adjust "Line Spacing" to match your pen tip width (0.3mm to 0.8mm).

### 3. Contour Streamlines (Hand-Drawn Style)
* **Best for:** Organic shapes, eyes, and realistic pencil-sketch looks.
* **How it works:** Analyzes the "flow" (gradients) of the image. It drops thousands of "seeds" that grow into long, flowing lines following the contours of the face.
* **Pro Tip:** Increase "Stroke Continuity" for long, elegant lines; decrease it for a "scratchy" charcoal effect.

## 🛠 Setup & Usage

1.  **Clone & Setup:**
    ```bash
    git clone [https://github.com/iclubu/Plotter-Studio.git](https://github.com/iclubu/Plotter-Studio.git)
    cd Plotter-Studio
    source venv/bin/activate
    pip install -r requirements.txt
    ```
2.  **Run:**
    ```bash
    streamlit run fmm_studio.py
    ```
3.  **Plot:** Upload an image, tune the sliders for a desirable "Total Line Distance," and hit **Export SVG**.

## 📂 Physical Scaling
The app calculates real-world scale (mm). Ensure your **Physical Width** matches your paper size to avoid clipping in your print software.

Final GitHub Push

## 📂 Project Structure
* `fmm_studio.py`: The main application logic.
* `requirements.txt`: Python dependencies.
