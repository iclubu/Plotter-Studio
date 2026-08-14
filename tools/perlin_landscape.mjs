#!/usr/bin/env node
/**
 * Perlin noise landscape SVG generator.
 * Adapted from https://turtletoy.net/turtle/65cb465053
 *
 * Uses the turtletoy npm package to run the turtle drawing locally
 * and captures line output as SVG.
 *
 * Usage:
 *   node perlin_landscape.mjs [options]
 *
 * Examples:
 *   node perlin_landscape.mjs -o landscape.svg
 *   node perlin_landscape.mjs --lines 400 --panels 6 --seed 42
 *   node perlin_landscape.mjs --density tight --amplitude 150
 */

import { turtleDraw, Turtle, Canvas } from 'turtletoy';
import { writeFileSync } from 'fs';
import { parseArgs } from 'util';

// ── CLI argument parsing ────────────────────────────────────────────

const { values: args } = parseArgs({
  options: {
    output:       { type: 'string',  short: 'o', default: '-' },
    // Density presets: sparse, normal, dense, tight
    density:      { type: 'string',  short: 'd', default: 'normal' },
    // Or set lines directly (overrides density)
    lines:        { type: 'string',  short: 'l' },
    panels:       { type: 'string',  short: 'p', default: '4' },
    // Noise parameters
    octaves:      { type: 'string',  default: '3' },
    'noise-scale':{ type: 'string',  default: '0.5' },
    amplitude:    { type: 'string',  default: '200' },
    'x-freq':     { type: 'string',  default: '0.01' },
    'y-freq':     { type: 'string',  default: '0.01' },
    // Panel noise scale overrides (comma-separated)
    'panel-scales': { type: 'string' },
    // Minimum distance between lines at each x column (0 = no limit)
    'min-dist':   { type: 'string',  default: '0' },
    // Appearance
    opacity:      { type: 'string',  default: '0.33' },
    'stroke-width': { type: 'string', default: '0.5' },
    // SVG dimensions in mm
    width:        { type: 'string',  short: 'W', default: '200' },
    height:       { type: 'string',  short: 'H', default: '200' },
    // Reproducibility
    seed:         { type: 'string',  short: 's' },
    help:         { type: 'boolean', short: 'h', default: false },
  },
  strict: false,
});

if (args.help) {
  console.log(`
Perlin noise landscape SVG generator.
Adapted from https://turtletoy.net/turtle/65cb465053

Options:
  -o, --output FILE        Output file (- for stdout) [default: -]
  -d, --density PRESET     Line density: sparse|normal|dense|tight [default: normal]
  -l, --lines N            Number of scan lines (overrides --density)
  -p, --panels N           Number of horizontal panel segments [default: 4]
      --octaves N          Perlin noise octave count [default: 3]
      --noise-scale F      Base noise scale factor [default: 0.5]
      --amplitude F        Vertical noise displacement [default: 200]
      --x-freq F           Noise frequency in x [default: 0.01]
      --y-freq F           Noise frequency in y [default: 0.01]
      --panel-scales F,F   Per-panel noise scale overrides (comma-separated)
      --min-dist F         Min vertical distance between lines per x column [default: 0]
                           Thins out dense/dark regions. Try 1-3.
      --opacity F          Stroke opacity 0-1 [default: 0.33]
      --stroke-width F     Stroke width in mm [default: 0.5]
  -W, --width MM           SVG width in mm [default: 200]
  -H, --height MM          SVG height in mm [default: 200]
  -s, --seed N             Random seed for reproducibility
  -h, --help               Show this help
`);
  process.exit(0);
}

// ── Parse numeric options ───────────────────────────────────────────

const DENSITY_PRESETS = {
  sparse: { lines: 150, lineSpacing: 0.2 },
  normal: { lines: 300, lineSpacing: 0.1 },
  dense:  { lines: 600, lineSpacing: 0.05 },
  tight:  { lines: 1200, lineSpacing: 0.025 },
};

const densityPreset = DENSITY_PRESETS[args.density] || DENSITY_PRESETS.normal;
const numLines     = parseInt(args.lines) || densityPreset.lines;
const lineSpacing  = densityPreset.lineSpacing;
const numPanels    = parseInt(args.panels);
const octaves      = parseInt(args.octaves);
const noiseScale   = parseFloat(args['noise-scale']);
const amplitude    = parseFloat(args.amplitude);
const xFreq        = parseFloat(args['x-freq']);
const yFreq        = parseFloat(args['y-freq']);
const minDist      = parseFloat(args['min-dist']);
const opacity      = parseFloat(args.opacity);
const strokeWidth  = parseFloat(args['stroke-width']);
const widthMM      = parseFloat(args.width);
const heightMM     = parseFloat(args.height);
const seed         = args.seed != null ? parseInt(args.seed) : null;

// Panel noise scales: outer panels lower, inner panels higher (like original)
let panelScales;
if (args['panel-scales']) {
  panelScales = args['panel-scales'].split(',').map(Number);
} else {
  panelScales = buildPanelScales(numPanels, noiseScale);
}

function buildPanelScales(n, baseScale) {
  if (n === 1) return [baseScale];
  if (n === 2) return [baseScale - 0.1, baseScale + 0.05];
  const scales = [];
  for (let p = 0; p < n; p++) {
    const t = p / (n - 1);
    const centerDist = Math.abs(t - 0.5) * 2; // 1 at edges, 0 at center
    scales.push(baseScale + 0.15 * (1 - centerDist) - 0.1 * centerDist);
  }
  return scales;
}

// ── Seeded RNG ──────────────────────────────────────────────────────

function mulberry32(a) {
  return function () {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

// If seed is set, override Math.random for reproducibility
const origRandom = Math.random;
if (seed != null) {
  const rng = mulberry32(seed);
  Math.random = rng;
}

// ── Perlin noise class (same as original turtletoy script) ──────────

class Noise {
  constructor(octavesN = 1) {
    this.p = new Uint8Array(512);
    this.octaves = octavesN;
    for (let i = 0; i < 512; ++i) {
      this.p[i] = Math.random() * 256 * 100;
    }
  }
  lerp(t, a, b) {
    return a + t * (b - a);
  }
  grad2d(i, x, y) {
    const v = (i & 1) === 0 ? x : y;
    return (i & 2) === 0 ? -v : v;
  }
  noise2d(x2d, y2d) {
    const X = Math.floor(x2d) & 255;
    const Y = Math.floor(y2d) & 255;
    const x = x2d - Math.floor(x2d);
    const y = y2d - Math.floor(y2d);
    const fx = (3 - 2 * x) * x * x;
    const fy = (3 - 2 * y) * y * y;
    const p0 = this.p[X] + Y;
    const p1 = this.p[X + 1] + Y;
    return this.lerp(
      fy,
      this.lerp(fx, this.grad2d(this.p[p0], x, y), this.grad2d(this.p[p1], x - 1, y)),
      this.lerp(fx, this.grad2d(this.p[p0 + 1], x, y - 1), this.grad2d(this.p[p1 + 1], x - 1, y - 1)),
    );
  }
  noise(x, y, scale = 0.5) {
    let e = 1, k = 1, s = 0;
    for (let i = 0; i < this.octaves; ++i) {
      e *= scale;
      s += e * (1 + this.noise2d(k * x, k * y)) / 2;
      k *= 2;
    }
    return s;
  }
}

// ── Compute line ranges from panel config ───────────────────────────
// TurtleToy coordinate space is -100 to 100.
// The original script uses 4 panels spanning x: -100 to 100.

const halfRange = 100; // turtletoy default viewbox is -100..100
const panelWidth = (halfRange * 2) / numPanels;

// Walk range: center around 1000 like the original
const iStart = 1000 - Math.floor(numLines / 2);
const iEnd   = iStart + numLines;

// Build the panels array: each panel is { xStart, xEnd, scale }
// Original draws right-to-left (panel 3, 2, 1, 0) for layering
const panels = [];
for (let p = numPanels - 1; p >= 0; p--) {
  panels.push({
    xStart: -halfRange + p * panelWidth,
    xEnd:   -halfRange + (p + 1) * panelWidth,
    scale:  panelScales[p % panelScales.length],
  });
}

// ── Run turtletoy drawing ───────────────────────────────────────────

// Per-panel, track the last drawn y-values so we can skip entire line
// segments that would land too close (min-dist culling).
// Key: panel index, Value: array of y values at each x column.
const lastDrawnByPanel = new Map();

const lines = [];     // collected SVG polyline segments
let currentPath = []; // points in the current pen-down stroke

function flushPath() {
  if (currentPath.length >= 2) {
    lines.push([...currentPath]);
  }
  currentPath = [];
}

turtleDraw(() => {
  Canvas.setpenopacity(opacity);
  const turtle = new Turtle();

  const perlinNoise = new Noise(octaves);

  turtle.up();

  return (i) => {
    const walkI = iStart + i;

    for (let pi = 0; pi < panels.length; pi++) {
      const panel = panels[pi];
      flushPath();

      // Precompute y values for this line segment
      const points = [];
      for (let j = panel.xStart; j < panel.xEnd; j++) {
        const h = perlinNoise.noise(
          100 + j * xFreq,
          100 + walkI * yFreq,
          panel.scale,
        );
        const y = lineSpacing * (walkI - 1000) + h * amplitude - amplitude / 2;
        points.push({ x: j, y });
      }

      // Check if this whole segment is too close to the last drawn one.
      // Use the median distance — this way the decision is driven by the
      // typical gap, not thrown off by a few outlier points at the ends.
      if (minDist > 0 && lastDrawnByPanel.has(pi)) {
        const prev = lastDrawnByPanel.get(pi);
        const gaps = [];
        for (let k = 0; k < points.length && k < prev.length; k++) {
          gaps.push(Math.abs(points[k].y - prev[k].y));
        }
        gaps.sort((a, b) => a - b);
        const median = gaps[Math.floor(gaps.length / 2)];
        if (median < minDist) {
          // Skip this entire segment — too close to previous
          turtle.up();
          continue;
        }
      }

      // Draw the full line and record it
      for (const pt of points) {
        turtle.goto(pt.x, pt.y);
        turtle.down();
      }
      lastDrawnByPanel.set(pi, points);

      turtle.up();
      flushPath();
    }

    return i < numLines - 1;
  };
}, {
  onDrawLine: (x1, y1, x2, y2) => {
    if (currentPath.length === 0) {
      currentPath.push([x1, y1]);
    }
    currentPath.push([x2, y2]);
  },
});

// Final flush
flushPath();

// Restore Math.random
Math.random = origRandom;

// ── Generate SVG ────────────────────────────────────────────────────

const viewBox = `-100 -100 200 200`;
const pathData = lines.map(pts => {
  const d = pts.map((p, i) =>
    `${i === 0 ? 'M' : 'L'}${p[0].toFixed(2)},${p[1].toFixed(2)}`
  ).join(' ');
  return `    <path d="${d}"/>`;
}).join('\n');

const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     width="${widthMM}mm" height="${heightMM}mm"
     viewBox="${viewBox}">
  <g fill="none"
     stroke="black"
     stroke-width="${strokeWidth}"
     stroke-opacity="${opacity}"
     stroke-linecap="round"
     stroke-linejoin="round">
${pathData}
  </g>
</svg>
`;

// ── Write output ────────────────────────────────────────────────────

if (args.output === '-') {
  process.stdout.write(svg);
} else {
  writeFileSync(args.output, svg);
  console.error(`Written to ${args.output} (${lines.length} paths)`);
}
