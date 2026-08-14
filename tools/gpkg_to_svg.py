#!/usr/bin/env python3
"""Convert swisstopo GeoPackage layers to SVG for pen plotter use.

Usage:
    python3 tools/gpkg_to_svg.py <input.gpkg> [--layers buildings,contours,roads,water] [--output out.svg]

    # Extract a 2km area around Bern at 1:5000
    python3 tools/gpkg_to_svg.py input.gpkg --bbox 2600000,1199000,2602000,1201000 --scale 5000

    # Just buildings and contours
    python3 tools/gpkg_to_svg.py input.gpkg --layers buildings,contours

Layers:
    buildings  - Building footprints (T48_DKM10_GEBAEUDE)
    contours   - Contour/height lines (T62_DKM10_HOEHENKURVE)
    roads      - Roads and paths (T45_DKM10_STRASSE)
    water_line - Waterways (T57_DKM10_GEWAESSER_LIN)
    water_poly - Water bodies (T56_DKM10_GEWAESSER_PLY)
    landcover  - Land cover (T65_DKM10_BODENBEDECKUNG)
    railroad   - Railways (T44_DKM10_EISENBAHN)

Coordinates are in EPSG:2056 (Swiss LV95).
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
from shapely import affinity
from shapely.geometry import (
    LineString, MultiLineString, MultiPolygon, Polygon,
    Point, MultiPoint, box,
)

LAYER_MAP = {
    "buildings":  "T48_DKM10_GEBAEUDE",
    "contours":   "T62_DKM10_HOEHENKURVE",
    "roads":      "T45_DKM10_STRASSE",
    "water_line": "T57_DKM10_GEWAESSER_LIN",
    "water_poly": "T56_DKM10_GEWAESSER_PLY",
    "landcover":  "T65_DKM10_BODENBEDECKUNG",
    "railroad":   "T44_DKM10_EISENBAHN",
    "boundaries": "T53_DKM10_HOHEITSGRENZE",
}

LAYER_STYLES = {
    "buildings":  {"stroke": "#222",    "stroke-width": "0.2", "fill": "#222"},
    "contours":   {"stroke": "#8B4513", "stroke-width": "0.2", "fill": "none"},
    "roads":      {"stroke": "#666",    "stroke-width": "0.4", "fill": "none"},
    "water_line": {"stroke": "#88bbee", "stroke-width": "0.3", "fill": "none"},
    "water_poly": {"stroke": "#88bbee", "stroke-width": "0.3", "fill": "#88bbee"},
    "landcover":  {"stroke": "#2a7a2a", "stroke-width": "0.2", "fill": "none"},
    "railroad":   {"stroke": "#444",    "stroke-width": "0.3", "fill": "none",
                   "stroke-dasharray": "2,1"},
    "boundaries": {"stroke": "#999",    "stroke-width": "0.4", "fill": "none",
                   "stroke-dasharray": "4,2"},
}

# Default SVG output size in mm (A3 landscape with margin)
SVG_WIDTH_MM = 400
SVG_HEIGHT_MM = 280


def coords_to_path(coords):
    """Convert a sequence of (x, y) coordinates to an SVG path d string."""
    parts = []
    for i, (x, y) in enumerate(coords):
        cmd = "M" if i == 0 else "L"
        parts.append(f"{cmd}{x:.2f},{y:.2f}")
    return "".join(parts)


def geometry_to_paths(geom):
    """Yield SVG path 'd' strings from a Shapely geometry."""
    if geom is None or geom.is_empty:
        return

    if isinstance(geom, (Point, MultiPoint)):
        return

    if isinstance(geom, LineString):
        yield coords_to_path(geom.coords)

    elif isinstance(geom, Polygon):
        d = coords_to_path(geom.exterior.coords) + "Z"
        for interior in geom.interiors:
            d += coords_to_path(interior.coords) + "Z"
        yield d

    elif isinstance(geom, (MultiLineString, MultiPolygon)):
        for part in geom.geoms:
            yield from geometry_to_paths(part)

    elif hasattr(geom, "geoms"):
        for part in geom.geoms:
            yield from geometry_to_paths(part)


def main():
    parser = argparse.ArgumentParser(description="Convert swisstopo GPKG to SVG")
    parser.add_argument("input", help="Path to .gpkg file")
    parser.add_argument("--layers", default="buildings,contours,roads,water_line,water_poly",
                        help="Comma-separated layer names")
    parser.add_argument("--output", "-o", default="map.svg", help="Output SVG path")
    parser.add_argument("--scale", type=float, default=None,
                        help="Map scale denominator, e.g. 10000 for 1:10000. "
                             "If not set, fits to SVG canvas.")
    parser.add_argument("--bbox", default=None,
                        help="Crop to bounding box: min_x,min_y,max_x,max_y (EPSG:2056 coords)")
    parser.add_argument("--list-layers", action="store_true",
                        help="List all available layers in the GPKG and exit")
    args = parser.parse_args()

    if args.list_layers:
        import sqlite3
        conn = sqlite3.connect(args.input)
        cur = conn.cursor()
        cur.execute("SELECT table_name, identifier FROM gpkg_contents")
        for row in cur.fetchall():
            print(f"  {row[0]}")
        conn.close()
        print(f"\nShorthand names: {', '.join(LAYER_MAP.keys())}")
        return

    requested = [l.strip() for l in args.layers.split(",")]
    for name in requested:
        if name not in LAYER_MAP:
            print(f"Unknown layer: {name}. Available: {', '.join(LAYER_MAP.keys())}")
            sys.exit(1)

    # Parse bbox
    clip_box = None
    if args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        if len(parts) != 4:
            print("--bbox requires 4 values: min_x,min_y,max_x,max_y")
            sys.exit(1)
        clip_box = box(parts[0], parts[1], parts[2], parts[3])
        print(f"Clipping to bbox: {parts}")

    # Read all requested layers
    all_gdfs = {}
    for name in requested:
        table = LAYER_MAP[name]
        print(f"Reading {name} ({table})...", end=" ", flush=True)
        try:
            if clip_box:
                gdf = gpd.read_file(args.input, layer=table, bbox=clip_box.bounds)
                # Clip geometries to bbox
                gdf = gdf.clip(clip_box)
            else:
                gdf = gpd.read_file(args.input, layer=table)
            gdf = gdf[~gdf.is_empty]
            print(f"{len(gdf)} features")
            if len(gdf) > 0:
                all_gdfs[name] = gdf
        except Exception as e:
            print(f"Error: {e}")

    if not all_gdfs:
        print("No layers loaded.")
        sys.exit(1)

    # Compute bounds (from bbox if given, otherwise from data)
    if clip_box:
        min_x, min_y, max_x, max_y = clip_box.bounds
    else:
        min_x = min(gdf.total_bounds[0] for gdf in all_gdfs.values())
        min_y = min(gdf.total_bounds[1] for gdf in all_gdfs.values())
        max_x = max(gdf.total_bounds[2] for gdf in all_gdfs.values())
        max_y = max(gdf.total_bounds[3] for gdf in all_gdfs.values())

    data_width = max_x - min_x
    data_height = max_y - min_y
    print(f"Map extent: {data_width:.0f} x {data_height:.0f} meters")

    if args.scale:
        scale_factor = 1000.0 / args.scale  # mm per meter
        svg_w = data_width * scale_factor
        svg_h = data_height * scale_factor
    else:
        scale_x = SVG_WIDTH_MM / data_width
        scale_y = SVG_HEIGHT_MM / data_height
        scale_factor = min(scale_x, scale_y)
        svg_w = data_width * scale_factor
        svg_h = data_height * scale_factor

    print(f"SVG size: {svg_w:.1f} x {svg_h:.1f} mm (scale 1:{1000.0/scale_factor:.0f})")

    # Build SVG
    svg = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "width": f"{svg_w:.2f}mm",
        "height": f"{svg_h:.2f}mm",
        "viewBox": f"0 0 {svg_w:.2f} {svg_h:.2f}",
    })

    for name in requested:
        if name not in all_gdfs:
            continue
        gdf = all_gdfs[name]
        style = LAYER_STYLES.get(name, {"stroke": "black", "stroke-width": "0.3", "fill": "none"})

        group = ET.SubElement(svg, "g", {"id": name, **style})

        for geom in gdf.geometry:
            if geom is None or geom.is_empty:
                continue
            # Transform: translate to origin, scale to mm, flip Y
            transformed = affinity.translate(geom, xoff=-min_x, yoff=-min_y)
            transformed = affinity.scale(transformed, xfact=scale_factor, yfact=-scale_factor, origin=(0, 0))
            transformed = affinity.translate(transformed, xoff=0, yoff=svg_h)

            for d in geometry_to_paths(transformed):
                ET.SubElement(group, "path", {"d": d})

        print(f"  {name}: {len(list(group))} paths")

    # Write SVG
    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output_path), xml_declaration=True, encoding="utf-8")
    print(f"\nWritten to {output_path} ({output_path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
