// Canvas rendering for the bed preview. All state is passed in explicitly
// so the renderer has no direct DOM dependencies beyond the canvas context.

// ── Helpers ──

// Convert printer bed coords to canvas pixel coords
function bedToCanvas(bx, by, cw, ch, bedX, bedY) {
  return { x: bx * (cw / bedX), y: ch - by * (ch / bedY) };
}

// Convert pen size (mm) to canvas pixel line width
function penSizeToPixels(penSize, cw, ch, bedX, bedY) {
  if (!penSize || penSize <= 0) return 1.5;
  const avgScale = (cw / bedX + ch / bedY) / 2;
  return Math.max(0.5, penSize * avgScale);
}

// Draw SVG paths onto the canvas (for import preview, not during a job).
// Filled regions are shown with a semi-transparent fill and lighter outlines;
// stroke-only paths are drawn in full blue.
function drawPathsOnCanvas(ctx, opts, fromIdx) {
  const { svgPaths, svgFilled, svgBBox, bedX, bedY, scale, ox, oy } = opts;
  if (!svgPaths.length || !svgBBox) return;
  const svgH = svgBBox.maxY - svgBBox.minY;
  const cw = ctx.canvas.width, ch = ctx.canvas.height;
  const scaleX = cw / bedX, scaleY = ch / bedY;
  const penPx = penSizeToPixels(opts.penSize, cw, ch, bedX, bedY);

  function toPx(pt) {
    return {
      x: ((pt.x - svgBBox.minX) * scale + ox) * scaleX,
      y: ch - ((svgH - (pt.y - svgBBox.minY)) * scale + oy) * scaleY,
    };
  }

  function tracePath(path) {
    for (let i = 0; i < path.length; i++) {
      const p = toPx(path[i]);
      if (i === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    }
  }

  const hasFilled = svgFilled && svgFilled.length === svgPaths.length;

  // Pass 1: Draw filled regions with a visible blue tint.  Darker SVG fills
  // get more opaque; lighter fills get less opaque but remain visible.
  if (hasFilled) {
    for (let p = fromIdx; p < svgPaths.length; p++) {
      if (svgFilled[p] === null || svgFilled[p] === undefined) continue;
      const path = svgPaths[p];
      if (path.length < 3) continue;
      const brightness = svgFilled[p];
      const alpha = (0.15 + (1 - brightness) * 0.30).toFixed(2);
      ctx.fillStyle = `rgba(80, 140, 210, ${alpha})`;
      ctx.beginPath();
      tracePath(path);
      ctx.fill();
    }
  }

  // Pass 2: Stroke non-filled paths (blue, full weight)
  ctx.strokeStyle = "#2471a3";
  ctx.lineWidth = penPx;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.beginPath();
  for (let p = fromIdx; p < svgPaths.length; p++) {
    if (hasFilled && svgFilled[p] !== null && svgFilled[p] !== undefined) continue;
    tracePath(svgPaths[p]);
  }
  ctx.stroke();

  // Pass 3: Filled-path outlines — very faint so the fill dominates
  if (hasFilled) {
    ctx.strokeStyle = "rgba(36, 113, 163, 0.10)";
    ctx.lineWidth = Math.max(0.3, penPx * 0.3);
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    for (let p = fromIdx; p < svgPaths.length; p++) {
      if (svgFilled[p] === null || svgFilled[p] === undefined) continue;
      tracePath(svgPaths[p]);
    }
    ctx.stroke();
  }
}

// ── Progress Rendering ──

// Cached draw state for incremental updates (avoids O(n) rescan from 0)
let _drawStateCache = { lastCmd: 0, completedUpToPath: -1, currentPath: -1, currentPoint: -1, headX: 0, headY: 0 };

function resetDrawStateCache() {
  _drawStateCache = { lastCmd: 0, completedUpToPath: -1, currentPath: -1, currentPoint: -1, headX: 0, headY: 0 };
}

// Scan the command map up to the current command index and return draw state
function computeDrawState(jobCommandMap, jobPlotPaths, jobCurrentCmd) {
  let { lastCmd, completedUpToPath, currentPath, currentPoint, headX, headY } = _drawStateCache;

  // If jobCurrentCmd went backwards (e.g. new job), reset
  if (jobCurrentCmd < lastCmd) {
    lastCmd = 0;
    completedUpToPath = -1;
    currentPath = -1;
    currentPoint = -1;
    headX = 0;
    headY = 0;
  }

  for (let i = lastCmd; i < jobCurrentCmd && i < jobCommandMap.length; i++) {
    const m = jobCommandMap[i];
    if (!m) continue;
    if (m.type === "draw" || m.type === "travel") {
      currentPath = m.pathIndex;
      currentPoint = m.pointIndex;
      headX = jobPlotPaths[m.pathIndex][m.pointIndex].x;
      headY = jobPlotPaths[m.pathIndex][m.pointIndex].y;
    }
    if (m.type === "penup") {
      completedUpToPath = m.pathIndex;
    }
  }

  _drawStateCache = { lastCmd: jobCurrentCmd, completedUpToPath, currentPath, currentPoint, headX, headY };
  return { completedUpToPath, currentPath, currentPoint, headX, headY };
}

// Draw paths colored by progress: completed (green), pending (dimmed), current pos (red dot)
function drawProgressPaths(ctx, opts) {
  const { bedX, bedY, jobPlotPaths, camZoom } = opts;
  const cw = ctx.canvas.width, ch = ctx.canvas.height;
  const scaleX = cw / bedX, scaleY = ch / bedY;
  const penPx = penSizeToPixels(opts.penSize, cw, ch, bedX, bedY);
  const state = computeDrawState(opts.jobCommandMap, jobPlotPaths, opts.jobCurrentCmd);
  const { completedUpToPath, currentPath, currentPoint, headX, headY } = state;

  ctx.lineJoin = "round";
  ctx.lineCap = "round";

  // Pass 1: Completed paths — green
  ctx.strokeStyle = "#27ae60";
  ctx.lineWidth = penPx;
  ctx.beginPath();
  for (let p = 0; p <= completedUpToPath && p < jobPlotPaths.length; p++) {
    const path = jobPlotPaths[p];
    for (let i = 0; i < path.length; i++) {
      const pt = bedToCanvas(path[i].x, path[i].y, cw, ch, bedX, bedY);
      if (i === 0) ctx.moveTo(pt.x, pt.y);
      else ctx.lineTo(pt.x, pt.y);
    }
  }
  ctx.stroke();

  // Pass 1b: Partial current path (completed portion) — green
  if (currentPath > completedUpToPath && currentPath >= 0) {
    ctx.strokeStyle = "#27ae60";
    ctx.lineWidth = penPx;
    ctx.beginPath();
    const path = jobPlotPaths[currentPath];
    for (let i = 0; i <= currentPoint && i < path.length; i++) {
      const pt = bedToCanvas(path[i].x, path[i].y, cw, ch, bedX, bedY);
      if (i === 0) ctx.moveTo(pt.x, pt.y);
      else ctx.lineTo(pt.x, pt.y);
    }
    ctx.stroke();
  }

  // Pass 2: Pending segments — dimmed
  ctx.strokeStyle = "rgba(36, 113, 163, 0.25)";
  ctx.lineWidth = penPx;
  ctx.beginPath();
  // Remainder of current path
  if (currentPath >= 0 && currentPoint < jobPlotPaths[currentPath].length - 1) {
    const path = jobPlotPaths[currentPath];
    for (let i = currentPoint; i < path.length; i++) {
      const pt = bedToCanvas(path[i].x, path[i].y, cw, ch, bedX, bedY);
      if (i === currentPoint) ctx.moveTo(pt.x, pt.y);
      else ctx.lineTo(pt.x, pt.y);
    }
  }
  // All subsequent paths
  const startPath = Math.max(currentPath + 1, completedUpToPath + 1);
  for (let p = startPath; p < jobPlotPaths.length; p++) {
    const path = jobPlotPaths[p];
    for (let i = 0; i < path.length; i++) {
      const pt = bedToCanvas(path[i].x, path[i].y, cw, ch, bedX, bedY);
      if (i === 0) ctx.moveTo(pt.x, pt.y);
      else ctx.lineTo(pt.x, pt.y);
    }
  }
  ctx.stroke();

  // Pass 3: Current position indicator — red dot
  if (currentPath >= 0) {
    const head = bedToCanvas(headX, headY, cw, ch, bedX, bedY);
    const r = 4 / (camZoom || 1);
    ctx.fillStyle = "#e74c3c";
    ctx.beginPath();
    ctx.arc(head.x, head.y, r, 0, Math.PI * 2);
    ctx.fill();
  }
}

// Draw fill pattern preview (paths already in printer bed coordinates)
function drawFillPreview(ctx, opts) {
  const { bedX, bedY, fillPaths } = opts;
  if (!fillPaths || !fillPaths.length) return;
  const cw = ctx.canvas.width, ch = ctx.canvas.height;
  const penPx = penSizeToPixels(opts.penSize, cw, ch, bedX, bedY);

  ctx.strokeStyle = "rgba(155, 89, 182, 0.5)";
  ctx.lineWidth = penPx;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";

  ctx.beginPath();
  for (const path of fillPaths) {
    for (let i = 0; i < path.length; i++) {
      const pt = bedToCanvas(path[i].x, path[i].y, cw, ch, bedX, bedY);
      if (i === 0) ctx.moveTo(pt.x, pt.y);
      else ctx.lineTo(pt.x, pt.y);
    }
  }
  ctx.stroke();
}

// ── Main Render ──

// Full preview render: bed grid, safe area, SVG paths or progress, bounding box
function renderPreview(ctx, opts) {
  const { bedX, bedY, pad, svgPaths, svgBBox, scale, ox, oy } = opts;
  const z = opts.camZoom || 1;
  const px = opts.camPanX || 0;
  const py = opts.camPanY || 0;
  const hasJob = opts.jobCommandMap && opts.jobPlotPaths && opts.jobCurrentCmd > 0;

  // Fit canvas to container while keeping aspect ratio
  const maxPx = 400;
  const aspect = bedX / bedY;
  let cw, ch;
  if (aspect >= 1) { cw = maxPx; ch = maxPx / aspect; }
  else { ch = maxPx; cw = maxPx * aspect; }
  ctx.canvas.width = cw;
  ctx.canvas.height = ch;

  const scaleX = cw / bedX;
  const scaleY = ch / bedY;

  // Background (bed) — drawn without transform so it fills the canvas
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, cw, ch);

  // Apply camera transform
  ctx.save();
  ctx.translate(px, py);
  ctx.scale(z, z);

  // Grid every 50mm
  ctx.strokeStyle = "#ddd";
  ctx.lineWidth = 0.5 / z;
  for (let x = 0; x <= bedX; x += 50) {
    const gx = x * scaleX;
    ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, ch); ctx.stroke();
  }
  for (let y = 0; y <= bedY; y += 50) {
    const gy = ch - y * scaleY;
    ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(cw, gy); ctx.stroke();
  }

  // Safe area (padding border)
  if (pad > 0) {
    ctx.fillStyle = "rgba(231, 76, 60, 0.08)";
    ctx.fillRect(0, 0, cw, pad * scaleY);
    ctx.fillRect(0, ch - pad * scaleY, cw, pad * scaleY);
    ctx.fillRect(0, pad * scaleY, pad * scaleX, ch - pad * scaleY * 2);
    ctx.fillRect(cw - pad * scaleX, pad * scaleY, pad * scaleX, ch - pad * scaleY * 2);

    ctx.strokeStyle = "rgba(231, 76, 60, 0.4)";
    ctx.lineWidth = 1 / z;
    ctx.setLineDash([4 / z, 4 / z]);
    ctx.strokeRect(pad * scaleX, ch - (bedY - pad) * scaleY, (bedX - pad * 2) * scaleX, (bedY - pad * 2) * scaleY);
    ctx.setLineDash([]);
  }

  // Grid labels
  ctx.fillStyle = "#999";
  ctx.font = `${9 / z}px sans-serif`;
  for (let x = 0; x <= bedX; x += 50) {
    ctx.fillText(x, x * scaleX + 2 / z, ch - 3 / z);
  }
  for (let y = 50; y <= bedY; y += 50) {
    ctx.fillText(y, 2 / z, ch - y * scaleY + 10 / z);
  }

  // Draw fill preview (when not in a job and fill paths are provided)
  if (!hasJob && opts.fillPaths && opts.fillPaths.length > 0) {
    drawFillPreview(ctx, opts);
  }

  // Draw paths — either progress overlay or plain SVG preview
  if (hasJob) {
    drawProgressPaths(ctx, opts);
  } else {
    drawPathsOnCanvas(ctx, opts, 0);
  }

  // Bounding box of transformed paths (only when no active job)
  if (!hasJob && svgPaths.length > 0 && svgBBox) {
    const svgH = svgBBox.maxY - svgBBox.minY;
    const svgW = svgBBox.maxX - svgBBox.minX;
    const bx = ox * scaleX;
    const by = ch - (svgH * scale + oy) * scaleY;
    const bw = svgW * scale * scaleX;
    const bh = svgH * scale * scaleY;
    ctx.strokeStyle = "rgba(231, 76, 60, 0.5)";
    ctx.lineWidth = 1 / z;
    ctx.setLineDash([4 / z, 4 / z]);
    ctx.strokeRect(bx, by, bw, bh);
    ctx.setLineDash([]);
  }

  ctx.restore();

  // Border — drawn outside transform so it stays crisp at canvas edge
  ctx.strokeStyle = "#444";
  ctx.lineWidth = 2;
  ctx.strokeRect(0, 0, cw, ch);
}
