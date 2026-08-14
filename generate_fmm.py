import numpy as np
import skfmm
from skimage import io, color, transform, exposure, measure, filters

def create_adaptive_fmm_svg(input_path, output_path, num_lines=250):
    # 1. Load and Pre-prep
    img = io.imread(input_path)
    if img.shape[-1] == 4: # Handle PNG transparency
        img = color.rgba2rgb(img)
    img_gray = color.rgb2gray(img)
    
    # Auto-Rescale (Keeping it manageable for the Cameo 5)
    img_gray = transform.rescale(img_gray, 0.5, anti_aliasing=True)
    
    # 2. THE "CLEANUP" TRICKS
    # Slight blur removes the 'noisy circles' from the Reddit post
    img_gray = filters.gaussian(img_gray, sigma=1.5)
    
    # Auto-Contrast (Stretches the range to ensure detail in dark/light)
    img_gray = exposure.rescale_intensity(img_gray)
    
    # Adaptive Gamma: 0.4 is usually the 'sweet spot' for Vader-style art
    img_gray = exposure.adjust_gamma(img_gray, 0.4)
    
    # 3. FMM Setup
    h, w = img_gray.shape
    phi = np.ones((h, w))
    phi[h // 2, w // 2] = 0 # Center source
    
    # Speed map: Lower offset (0.02) makes dark areas even more dense
    speed = img_gray + 0.02
    t_map = skfmm.travel_time(phi, speed)
    
    # 4. Exporting the SVG
    levels = np.linspace(t_map.min(), t_map.max(), num_lines)
    
    with open(output_path, 'w') as f:
        f.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n')
        for level in levels:
            contours = measure.find_contours(t_map, level)
            for c in contours:
                # Format to 2 decimal places to keep the SVG file size smaller
                path_str = "M " + " L ".join([f"{p[1]:.2f},{p[0]:.2f}" for p in c])
                f.write(f'  <path d="{path_str}" fill="none" stroke="black" stroke-width="0.3" />\n')
        f.write('</svg>')
    print(f"Adaptive SVG created: {output_path}")

if __name__ == "__main__":
    create_adaptive_fmm_svg('input.jpg', 'vader_final.svg')