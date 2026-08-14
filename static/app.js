// Application wiring: WebSocket, state management, SVG import, event handlers.
// Depends on plotter.js (pure functions) and preview.js (canvas rendering).

// ── State ──
const consoleEl = document.getElementById("console");
const statusEl = document.getElementById("status");
const cmdInput = document.getElementById("cmd-input");
const canvas = document.getElementById("preview");
const ctx = canvas.getContext("2d");
const cmdHistory = [];
let historyIdx = -1;
let ws;

let queuedCount = 0;
let isPaused = false;
let jobStartTime = null;
let jobCommandMap = null;
let jobPlotPaths = null;
let jobCurrentCmd = 0;

let svgPaths = [];
let svgFilled = [];
let svgBBox = null;

// Camera (zoom/pan)
let camZoom = 1;
let camPanX = 0;
let camPanY = 0;

// ── DOM helpers ──

function el(id) { return document.getElementById(id); }
function numVal(id, fallback) { return parseFloat(el(id).value) || fallback; }

function getBounds() {
  const bedX = numVal("bed-x", 300);
  const bedY = numVal("bed-y", 300);
  const pad = numVal("bed-padding", 0);
  return { minX: pad, minY: pad, maxX: bedX - pad, maxY: bedY - pad, bedX, bedY, pad };
}

function getPenUpZ() { return parseFloat(el("pen-up-z").value); }
function getPenDownZ() { return parseFloat(el("pen-down-z").value); }
function getPenZSpeed() { return parseFloat(el("pen-z-speed").value); }

function computeFillPreview() {
  if (!svgPaths.length || !svgBBox) return [];
  const fillMode = el("svg-fill-mode").value;
  if (fillMode === "none") return [];
  const scale = numVal("svg-scale", 1);
  const ox = numVal("svg-ox", 0);
  const oy = numVal("svg-oy", 0);
  const fillAngle = numVal("svg-fill-angle", 45);
  const minArea = numVal("svg-fill-min-area", 5);
  const minSpacing = numVal("svg-fill-min-spacing", 0.4);
  const maxSpacing = numVal("svg-fill-max-spacing", 5.0);
  const maxBrightness = numVal("svg-fill-max-brightness", 0.8);
  const paths = transformPaths(svgPaths, svgBBox, scale, ox, oy);
  return generateFillPaths(paths, fillMode, fillAngle, svgFilled, minArea, minSpacing, maxSpacing, maxBrightness);
}

function getPreviewOpts() {
  return {
    bedX: numVal("bed-x", 300),
    bedY: numVal("bed-y", 300),
    pad: numVal("bed-padding", 0),
    scale: numVal("svg-scale", 1),
    ox: numVal("svg-ox", 0),
    oy: numVal("svg-oy", 0),
    svgPaths,
    svgFilled,
    svgBBox,
    jobCommandMap,
    jobPlotPaths,
    jobCurrentCmd,
    camZoom,
    camPanX,
    camPanY,
    penSize: numVal("pen-size", 0.5),
    fillPaths: computeFillPreview(),
  };
}

// ── Console ──

function addLine(text, type) {
  const div = document.createElement("div");
  div.className = `line-${type}`;
  div.textContent = text;
  consoleEl.appendChild(div);
  consoleEl.scrollTop = consoleEl.scrollHeight;
  while (consoleEl.children.length > 2000)
    consoleEl.removeChild(consoleEl.firstChild);
}

// ── WebSocket ──

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => addLine("Connected to server", "info");
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "serial") addLine(msg.data, "rx");
    else if (msg.type === "sent") {
      addLine(msg.data, "tx");
      if (queuedCount > 0) { queuedCount--; updateQueueStatus(); }
    }
    else if (msg.type === "error") addLine(msg.data, "err");
    else if (msg.type === "status") {
      if (msg.connected) {
        statusEl.textContent = `Connected: ${msg.port}`;
        statusEl.classList.add("connected");
      } else {
        statusEl.textContent = "No printer";
        statusEl.classList.remove("connected");
      }
    } else if (msg.type === "stopped") {
      addLine(msg.data, "err");
      queuedCount = 0;
      isPaused = false;
      clearJobState();
      updateQueueStatus();
      updatePauseBtn();
      updateProgress(0, 0);
    } else if (msg.type === "progress") {
      updateProgress(msg.sent, msg.total, msg.pct);
    } else if (msg.type === "paused") {
      isPaused = true;
      updatePauseBtn();
      addLine("Paused", "info");
    } else if (msg.type === "resumed") {
      isPaused = false;
      updatePauseBtn();
      addLine("Resumed", "info");
    } else if (msg.type === "serial_stats") {
      updateSerialStats(msg);
    }
  };
  ws.onclose = () => {
    statusEl.textContent = "Disconnected";
    statusEl.classList.remove("connected");
    addLine("Connection lost. Reconnecting...", "info");
    setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();
}

let queueStatusPending = false;
function scheduleQueueStatus() {
  if (!queueStatusPending) {
    queueStatusPending = true;
    requestAnimationFrame(() => {
      updateQueueStatus();
      queueStatusPending = false;
    });
  }
}

function send(cmd) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "command", data: cmd }));
    queuedCount++;
    scheduleQueueStatus();
  }
}

function sendDirect(cmd) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "direct_command", data: cmd }));
  }
}

function startJob(totalCommands) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "job_start", total: totalCommands }));
  }
}

// ── Serial Stats UI ──

function formatBps(bps) {
  if (bps >= 1000) return (bps / 1000).toFixed(1) + " KB/s";
  return bps + " B/s";
}

function updateSerialStats(stats) {
  el("serial-throughput").textContent = `TX ${formatBps(stats.tx_bps)} / RX ${formatBps(stats.rx_bps)}`;
  const satEl = el("serial-saturation");
  satEl.textContent = stats.saturation_pct.toFixed(1) + "%";
  satEl.classList.remove("low", "mid", "high");
  if (stats.saturation_pct >= 70) satEl.classList.add("high");
  else if (stats.saturation_pct >= 30) satEl.classList.add("mid");
  else satEl.classList.add("low");
  el("serial-rtt").textContent = stats.avg_rtt_ms > 0 ? `RTT ${stats.avg_rtt_ms.toFixed(0)}ms` : "RTT --";
  el("serial-cmds").textContent = stats.cmds_per_sec.toFixed(1) + " cmd/s";
}

// ── Queue & Progress UI ──

function updateQueueStatus() {
  el("queue-status").textContent = queuedCount > 0 ? `Queue: ${queuedCount}` : "";
}

function updatePauseBtn() {
  const btn = el("pause-btn");
  if (isPaused) {
    btn.textContent = "Resume";
    btn.classList.add("primary");
  } else {
    btn.textContent = "Pause";
    btn.classList.remove("primary");
  }
}

let progressRedrawPending = false;

function schedulePreviewRedraw() {
  if (!progressRedrawPending) {
    progressRedrawPending = true;
    requestAnimationFrame(() => {
      renderPreview(ctx, getPreviewOpts());
      progressRedrawPending = false;
    });
  }
}

function updateProgress(sent, total, pct) {
  const wrap = el("progress-bar-wrap");
  const fill = el("progress-bar-fill");
  const text = el("progress-bar-text");
  const pauseBtn = el("pause-btn");
  if (total > 0) {
    wrap.style.display = "block";
    pauseBtn.style.display = "block";
    fill.style.width = pct + "%";
    text.textContent = `${pct.toFixed(2)}%  (${sent} / ${total} commands)`;
    jobCurrentCmd = sent;
    if (jobStartTime && sent > 0 && sent < total) {
      const elapsed = Date.now() - jobStartTime;
      const remaining = elapsed * (total - sent) / sent;
      text.textContent += `  Elapsed: ${formatDuration(elapsed)}  ETA: ${formatDuration(remaining)}`;
    }
    schedulePreviewRedraw();
    if (sent >= total && jobStartTime !== null) {
      const elapsed = Date.now() - jobStartTime;
      const timeStr = formatDuration(elapsed);
      text.textContent = `Complete!  Total time: ${timeStr}`;
      fill.style.width = "100%";
      pauseBtn.style.display = "none";
      addLine(`Job complete. Total time: ${timeStr}`, "info");
      jobStartTime = null;
      // Home all axes
      send("G28");
      // Keep job paths visible briefly, then clear after final redraw
      setTimeout(() => {
        clearJobState();
        renderPreview(ctx, getPreviewOpts());
      }, 3000);
    }
  } else {
    wrap.style.display = "none";
    pauseBtn.style.display = "none";
    fill.style.width = "0%";
    text.textContent = "";
  }
}

// ── Actions (exposed to onclick handlers) ──

function clearJobState() {
  jobStartTime = null;
  jobCommandMap = null;
  jobPlotPaths = null;
  jobCurrentCmd = 0;
  resetDrawStateCache();
}

function emergencyStop() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "emergency_stop" }));
    addLine("EMERGENCY STOP sent", "err");
    queuedCount = 0;
    clearJobState();
    updateQueueStatus();
  }
}

function resetPrinter() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "reset" }));
    addLine("Resetting serial connection...", "info");
    queuedCount = 0;
    isPaused = false;
    clearJobState();
    updateQueueStatus();
    updatePauseBtn();
    updateProgress(0, 0);
  }
}

function togglePause() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (isPaused) {
    ws.send(JSON.stringify({ type: "resume", pen_down_z: getPenDownZ(), z_speed: getPenZSpeed() }));
  } else {
    ws.send(JSON.stringify({ type: "pause", pen_up_z: getPenUpZ(), z_speed: getPenZSpeed() }));
  }
}

function sendInput() {
  const cmd = cmdInput.value.trim();
  if (!cmd) return;
  if (isPaused) sendDirect(cmd);
  else send(cmd);
  cmdHistory.unshift(cmd);
  historyIdx = -1;
  cmdInput.value = "";
}

// ── Pen ──

function penUp() {
  const s = isPaused ? sendDirect : send;
  s("G90");
  s(`G1 Z${getPenUpZ()} F${getPenZSpeed()}`);
  addLine("Pen up", "info");
}

function penDown() {
  const s = isPaused ? sendDirect : send;
  s("G90");
  s(`G1 Z${getPenDownZ()} F${getPenZSpeed()}`);
  addLine("Pen down", "info");
}

function setPenSize(mm) {
  el("pen-size").value = mm;
  renderPreview(ctx, getPreviewOpts());
}

// ── Speed Override ──

function setSpeed(pct) {
  el("speed-pct").value = pct;
  applySpeed();
}

function applySpeed() {
  const pct = parseInt(el("speed-pct").value);
  sendDirect(`M220 S${pct}`);
  addLine(`Speed override: ${pct}%`, "info");
}

// ── Move / Circle ──

let moveMode = "circle";

function setMoveMode(mode) {
  moveMode = mode;
  const radiusLabel = el("radius-label");
  const btn = el("move-action-btn");
  const modeMove = el("mode-move");
  const modeCircle = el("mode-circle");

  if (mode === "move") {
    radiusLabel.style.display = "none";
    btn.textContent = "Move To";
    modeMove.classList.add("primary");
    modeCircle.classList.remove("primary");
  } else {
    radiusLabel.style.display = "";
    btn.textContent = "Draw Circle";
    modeCircle.classList.add("primary");
    modeMove.classList.remove("primary");
  }
}

function doMoveAction() {
  const x = parseFloat(el("move-x").value);
  const y = parseFloat(el("move-y").value);
  const f = parseFloat(el("move-f").value);
  const b = getBounds();
  const warn = el("move-warn");
  warn.textContent = "";

  if (moveMode === "move") {
    if (x < b.minX || x > b.maxX || y < b.minY || y > b.maxY) {
      warn.textContent = `Position outside safe area (${b.minX}-${b.maxX}, ${b.minY}-${b.maxY})!`;
      return;
    }
    const cmds = generateMoveCommands(x, y, f, getPenUpZ(), getPenZSpeed());
    cmds.forEach(send);
    addLine(`Move to (${x}, ${y})`, "info");
  } else {
    const r = parseFloat(el("circle-r").value);
    if (x - r < b.minX || x + r > b.maxX || y - r < b.minY || y + r > b.maxY) {
      warn.textContent = `Circle exceeds safe area (${b.minX}-${b.maxX}, ${b.minY}-${b.maxY})!`;
      return;
    }
    const cmds = generateCircleCommands(x, y, r, f, getPenUpZ(), getPenDownZ(), getPenZSpeed());
    cmds.forEach(send);
    addLine(`Circle: center=(${x},${y}) r=${r}`, "info");
  }
}

// ── SVG Import ──

const SVG_SERVER_THRESHOLD = 100000;

function onSvgFileChange(e) {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > SVG_SERVER_THRESHOLD) {
    parseSvgServer(file);
  } else {
    const reader = new FileReader();
    reader.onload = (ev) => parseSvg(ev.target.result, file.name);
    reader.readAsText(file);
  }
}

async function parseSvgServer(file) {
  const svgInfo = el("svg-info");
  const loadProgress = el("svg-load-progress");
  const loadFill = el("svg-load-fill");
  const loadText = el("svg-load-text");

  loadProgress.style.display = "block";
  loadFill.style.width = "0%";
  loadText.textContent = `Uploading ${file.name} (${(file.size / 1024).toFixed(0)} KB) for server processing...`;
  svgInfo.textContent = loadText.textContent;
  addLine(`Large SVG (${(file.size / 1024).toFixed(0)} KB) — using server-side preprocessing...`, "info");

  const formData = new FormData();
  formData.append("file", file);
  formData.append("simplify", el("svg-simplify").value || "0");
  // Send fill parameters so server can pre-generate fill patterns
  const fillMode = el("svg-fill-mode").value;
  if (fillMode !== "none") {
    formData.append("fill_mode", fillMode);
    formData.append("fill_angle", el("svg-fill-angle").value);
    formData.append("fill_min_spacing", el("svg-fill-min-spacing").value);
    formData.append("fill_max_spacing", el("svg-fill-max-spacing").value);
    formData.append("fill_max_brightness", el("svg-fill-max-brightness").value);
    formData.append("fill_min_area", el("svg-fill-min-area").value);
  }

  try {
    loadFill.style.width = "30%";
    loadText.textContent = "Server processing...";
    svgInfo.textContent = loadText.textContent;

    const resp = await fetch("/api/preprocess-svg", { method: "POST", body: formData });
    if (!resp.ok) {
      const err = await resp.json();
      addLine(`SVG preprocess error: ${err.error || resp.statusText}`, "err");
      loadProgress.style.display = "none";
      return;
    }

    loadFill.style.width = "70%";
    loadText.textContent = "Parsing response...";

    const data = await resp.json();

    loadFill.style.width = "90%";
    loadText.textContent = "Building preview...";

    svgPaths = data.paths.map(sp => sp.map(([x, y]) => ({ x, y })));
    svgFilled = data.filled || data.paths.map(() => 0.0);
    svgBBox = data.bbox;

    loadFill.style.width = "100%";
    const stats = data.stats;
    const w = (svgBBox.maxX - svgBBox.minX).toFixed(1);
    const h = (svgBBox.maxY - svgBBox.minY).toFixed(1);
    svgInfo.textContent = `${file.name}: ${stats.simplified_paths} paths, ${stats.total_points} points, ${w} x ${h} (SVG units)`;
    addLine(`Loaded ${file.name}: ${stats.original_paths} subpaths → ${stats.simplified_paths} simplified, ${stats.total_points} points`, "info");

    loadProgress.style.display = "none";
    renderPreview(ctx, getPreviewOpts());
  } catch (e) {
    addLine(`SVG upload error: ${e.message}`, "err");
    loadProgress.style.display = "none";
  }
}

function parseSvg(svgText, filename) {
  svgText = svgText.replace(/<\?xml[^?]*\?>/gi, "");
  svgText = svgText.replace(/<!DOCTYPE[^>]*>/gi, "");

  const parser = new DOMParser();
  const svgDoc = parser.parseFromString(svgText.trim(), "image/svg+xml");
  const parseError = svgDoc.querySelector("parsererror");
  if (parseError) {
    addLine("SVG parse error: " + parseError.textContent.slice(0, 100), "err");
    return;
  }
  const svgEl = svgDoc.querySelector("svg");
  if (!svgEl) {
    addLine("No <svg> element found in file", "err");
    return;
  }

  const container = document.createElement("div");
  container.style.cssText = "position:absolute;visibility:hidden;width:0;height:0;overflow:hidden;";
  container.appendChild(document.adoptNode(svgEl));
  document.body.appendChild(container);

  svgPaths = [];
  svgFilled = [];
  svgBBox = null;

  const elements = svgEl.querySelectorAll("path, line, polyline, polygon, rect, circle, ellipse");
  const total = elements.length;
  if (total === 0) {
    addLine("No drawable paths found in SVG", "err");
    container.remove();
    return;
  }

  const resolution = numVal("svg-res", 0.5);
  const svgInfo = el("svg-info");
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  let totalPts = 0;
  let idx = 0;

  let cachedCTM = null;
  try {
    const firstPath = elements[0];
    const svgRoot = firstPath.ownerSVGElement;
    if (svgRoot) {
      const screenCTM = firstPath.getScreenCTM();
      const svgScreenCTM = svgRoot.getScreenCTM();
      if (screenCTM && svgScreenCTM) {
        cachedCTM = svgScreenCTM.inverse().multiply(screenCTM);
      }
    }
  } catch (e) {}

  addLine(`Parsing ${filename}: ${total} elements...`, "info");

  const loadProgress = el("svg-load-progress");
  const loadFill = el("svg-load-fill");
  const loadText = el("svg-load-text");
  loadProgress.style.display = "block";
  loadFill.style.width = "0%";

  let lastPreviewIdx = 0;

  function processBatch() {
    const deadline = performance.now() + 25;
    while (idx < total && performance.now() < deadline) {
      const domEl = elements[idx];
      const subpaths = sampleElement(domEl, resolution, cachedCTM);
      if (subpaths.length > 0) {
        // Resolve fill brightness: null = no fill, 0.0 = black, 1.0 = white
        let fillBrightness = 0.0;
        try {
          const fill = getComputedStyle(domEl).fill;
          if (!fill || fill === "none" || fill === "transparent") {
            fillBrightness = null;
          } else {
            const m = fill.match(/(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
            if (m) {
              const r = parseInt(m[1]), g = parseInt(m[2]), b = parseInt(m[3]);
              fillBrightness = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0;
            }
          }
        } catch (e) {}
        for (const points of subpaths) {
          svgPaths.push(points);
          svgFilled.push(fillBrightness);
          totalPts += points.length;
          for (const pt of points) {
            if (pt.x < minX) minX = pt.x;
            if (pt.y < minY) minY = pt.y;
            if (pt.x > maxX) maxX = pt.x;
            if (pt.y > maxY) maxY = pt.y;
          }
        }
      }
      idx++;
    }

    const pct = (idx / total) * 100;
    loadFill.style.width = pct + "%";
    loadText.textContent = `${pct.toFixed(1)}% — ${idx}/${total} elements, ${svgPaths.length} paths, ${totalPts} pts`;
    svgInfo.textContent = loadText.textContent;

    if (svgPaths.length > lastPreviewIdx) {
      svgBBox = { minX, minY, maxX, maxY };
      drawPathsOnCanvas(ctx, getPreviewOpts(), lastPreviewIdx);
      lastPreviewIdx = svgPaths.length;
    }

    if (idx < total) {
      requestAnimationFrame(processBatch);
    } else {
      container.remove();
      loadProgress.style.display = "none";
      const w = (maxX - minX).toFixed(1);
      const h = (maxY - minY).toFixed(1);
      svgInfo.textContent = `${filename}: ${svgPaths.length} paths, ${totalPts} points, ${w} x ${h} (SVG units)`;
      addLine(`Loaded ${filename}: ${svgPaths.length} paths, ${totalPts} points`, "info");
      renderPreview(ctx, getPreviewOpts());
    }
  }

  renderPreview(ctx, getPreviewOpts());
  requestAnimationFrame(processBatch);
}

// Sample an SVG element into point arrays. Returns an array of subpath arrays
// (one per contiguous segment). Multi-subpath <path> elements (M...Z M...Z) are
// split at discontinuities so connecting lines between subpaths are eliminated.
function sampleElement(domEl, resolution, precomputedCTM) {
  const tag = domEl.tagName.toLowerCase();
  let pathEl;
  let createdPath = false;

  if (tag === "path") {
    pathEl = domEl;
  } else {
    const d = elementToPathD(tag, n => domEl.getAttribute(n));
    if (!d) return [];
    const ns = "http://www.w3.org/2000/svg";
    pathEl = document.createElementNS(ns, "path");
    pathEl.setAttribute("d", d);
    domEl.parentNode.appendChild(pathEl);
    createdPath = true;
  }

  const totalLen = pathEl.getTotalLength();
  if (totalLen === 0) {
    if (createdPath) pathEl.remove();
    return [];
  }

  let ctm = precomputedCTM || null;
  if (!ctm) {
    try {
      const svgRoot = pathEl.ownerSVGElement;
      if (svgRoot) {
        const screenCTM = pathEl.getScreenCTM();
        const svgScreenCTM = svgRoot.getScreenCTM();
        if (screenCTM && svgScreenCTM) {
          ctm = svgScreenCTM.inverse().multiply(screenCTM);
        }
      }
    } catch (e) {}
  }

  const points = [];
  const steps = Math.min(Math.max(Math.ceil(totalLen / resolution), 1), 5000);

  if (ctm) {
    const a = ctm.a, b = ctm.b, c = ctm.c, d = ctm.d, e = ctm.e, f = ctm.f;
    for (let i = 0; i <= steps; i++) {
      const pt = pathEl.getPointAtLength((i / steps) * totalLen);
      points.push({ x: a * pt.x + c * pt.y + e, y: b * pt.x + d * pt.y + f });
    }
  } else {
    for (let i = 0; i <= steps; i++) {
      const pt = pathEl.getPointAtLength((i / steps) * totalLen);
      points.push({ x: pt.x, y: pt.y });
    }
  }

  if (createdPath) pathEl.remove();
  if (points.length < 2) return [];

  // Detect subpath boundaries: compute inter-point distances and split at
  // gaps much larger than the median (indicating a moveTo between subpaths).
  const distsSq = [];
  for (let i = 1; i < points.length; i++) {
    const dx = points[i].x - points[i - 1].x;
    const dy = points[i].y - points[i - 1].y;
    distsSq.push(dx * dx + dy * dy);
  }
  const sorted = distsSq.slice().sort((a, b) => a - b);
  const medianSq = sorted[Math.floor(sorted.length / 2)];
  // Gap threshold: 5x median linear distance (25x squared). Floor prevents
  // degenerate zero-median from splitting everything.
  const gapSq = Math.max(medianSq * 25, 1e-6);

  const subpaths = [];
  let current = [points[0]];
  for (let i = 1; i < points.length; i++) {
    if (distsSq[i - 1] > gapSq) {
      if (current.length >= 2) subpaths.push(current);
      current = [];
    }
    current.push(points[i]);
  }
  if (current.length >= 2) subpaths.push(current);
  return subpaths;
}

function clearSvg() {
  svgPaths = [];
  svgFilled = [];
  svgBBox = null;
  el("svg-info").textContent = "";
  el("svg-warn").textContent = "";
  renderPreview(ctx, getPreviewOpts());
}

function fitToBed() {
  if (!svgBBox) return;
  const b = getBounds();
  const fit = computeFitToBed(svgBBox, b);
  if (!fit) return;

  el("svg-scale").value = fit.scale.toFixed(3);
  el("svg-ox").value = fit.ox.toFixed(1);
  el("svg-oy").value = fit.oy.toFixed(1);

  renderPreview(ctx, getPreviewOpts());
}

// ── Plot SVG ──

function sendCommandsBatched(cmds, onDone) {
  let i = 0;
  function batch() {
    const deadline = performance.now() + 10; // 10ms per frame
    while (i < cmds.length && performance.now() < deadline) {
      send(cmds[i++]);
    }
    if (i < cmds.length) {
      requestAnimationFrame(batch);
    } else if (onDone) {
      onDone();
    }
  }
  requestAnimationFrame(batch);
}

let plotBusy = false;

function plotSvg() {
  if (plotBusy) return;
  const warn = el("svg-warn");
  warn.textContent = "";

  if (!svgPaths.length || !svgBBox) {
    warn.textContent = "No SVG loaded.";
    return;
  }

  plotBusy = true;

  const scale = numVal("svg-scale", 1);
  const ox = numVal("svg-ox", 0);
  const oy = numVal("svg-oy", 0);
  let paths = transformPaths(svgPaths, svgBBox, scale, ox, oy);

  const b = getBounds();
  const speed = numVal("svg-speed", 4500);
  const simplifyTol = numVal("svg-simplify", 0);
  const mergeGap = numVal("svg-merge", 0);

  // Generate fill patterns BEFORE optimization (svgFilled mapping is still 1:1)
  const fillMode = el("svg-fill-mode").value;
  const fillAngle = numVal("svg-fill-angle", 45);
  const minArea = numVal("svg-fill-min-area", 5);
  const minSpacing = numVal("svg-fill-min-spacing", 0.4);
  const maxSpacing = numVal("svg-fill-max-spacing", 5.0);
  const maxBrightness = numVal("svg-fill-max-brightness", 0.8);
  const fillPaths = generateFillPaths(paths, fillMode, fillAngle, svgFilled, minArea, minSpacing, maxSpacing, maxBrightness);

  if (fillPaths.length > 0) {
    addLine(`Fill: ${fillPaths.length} hatch paths (${fillMode}, ${minSpacing}-${maxSpacing}mm spacing, ${fillAngle}°)`, "info");
  }

  const origPaths = paths.length;
  let origPts = 0;
  for (const p of paths) origPts += p.length;

  addLine(`Optimizing ${origPaths} paths (${origPts} points)...`, "info");
  paths = optimizePaths(paths, simplifyTol, mergeGap);

  let optPts = 0;
  for (const p of paths) optPts += p.length;
  addLine(`Optimized: ${origPaths} → ${paths.length} paths, ${origPts} → ${optPts} points`, "info");

  // Optimize fill paths separately (sort for minimal travel, but don't merge — they're already zigzag)
  let optFills = fillPaths.length > 0 ? optimizePaths(fillPaths, 0, 0) : [];

  // Combine: fill first, then outlines on top
  const allPaths = optFills.concat(paths);

  const boundsErr = checkBounds(allPaths, b);
  if (boundsErr) {
    warn.textContent = `SVG ${boundsErr}`;
    plotBusy = false;
    return;
  }

  const penUpZ = getPenUpZ();
  const penDownZ = getPenDownZ();
  const { cmds, map } = generatePlotCommandsWithMap(allPaths, speed, penUpZ, penDownZ);

  jobCommandMap = map;
  jobPlotPaths = allPaths;
  jobCurrentCmd = 0;
  jobStartTime = Date.now();
  resetDrawStateCache();
  startJob(cmds.length);
  addLine(`Sending ${cmds.length} commands...`, "info");
  sendCommandsBatched(cmds, () => {
    addLine(`Plotting ${allPaths.length} paths (${cmds.length} commands)`, "info");
    plotBusy = false;
  });
  renderPreview(ctx, getPreviewOpts());
}

// ── Tabs ──

function switchTab(tabName) {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });
  document.querySelectorAll('.tab-content').forEach(content => {
    content.classList.toggle('active', content.id === 'tab-' + tabName);
  });
}

// ── Event Binding ──

cmdInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendInput();
  else if (e.key === "ArrowUp") {
    e.preventDefault();
    if (historyIdx < cmdHistory.length - 1) cmdInput.value = cmdHistory[++historyIdx];
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    if (historyIdx > 0) cmdInput.value = cmdHistory[--historyIdx];
    else { historyIdx = -1; cmdInput.value = ""; }
  }
});

el("svg-file-input").addEventListener("change", onSvgFileChange);

["svg-ox", "svg-oy", "svg-scale", "bed-x", "bed-y", "bed-padding", "svg-fill-angle", "svg-fill-min-spacing", "svg-fill-max-spacing", "svg-fill-max-brightness", "svg-fill-min-area", "pen-size"].forEach(id => {
  el(id).addEventListener("input", () => renderPreview(ctx, getPreviewOpts()));
});

el("svg-fill-mode").addEventListener("change", () => renderPreview(ctx, getPreviewOpts()));

// ── Zoom / Pan ──

function zoomIn() {
  camZoom = Math.min(camZoom * 1.25, 10);
  renderPreview(ctx, getPreviewOpts());
}

function zoomOut() {
  camZoom = Math.max(camZoom / 1.25, 0.5);
  renderPreview(ctx, getPreviewOpts());
}

function zoomReset() {
  camZoom = 1;
  camPanX = 0;
  camPanY = 0;
  renderPreview(ctx, getPreviewOpts());
}

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mouseX = e.clientX - rect.left;
  const mouseY = e.clientY - rect.top;

  const oldZoom = camZoom;
  if (e.deltaY < 0) camZoom = Math.min(camZoom * 1.15, 10);
  else camZoom = Math.max(camZoom / 1.15, 0.5);

  // Zoom toward mouse position
  const ratio = camZoom / oldZoom;
  camPanX = mouseX - ratio * (mouseX - camPanX);
  camPanY = mouseY - ratio * (mouseY - camPanY);

  renderPreview(ctx, getPreviewOpts());
}, { passive: false });

let isDragging = false, dragStartX = 0, dragStartY = 0, dragStartPanX = 0, dragStartPanY = 0;

canvas.addEventListener("mousedown", (e) => {
  isDragging = true;
  dragStartX = e.clientX;
  dragStartY = e.clientY;
  dragStartPanX = camPanX;
  dragStartPanY = camPanY;
  canvas.style.cursor = "grabbing";
});

window.addEventListener("mousemove", (e) => {
  if (!isDragging) return;
  camPanX = dragStartPanX + (e.clientX - dragStartX);
  camPanY = dragStartPanY + (e.clientY - dragStartY);
  renderPreview(ctx, getPreviewOpts());
});

window.addEventListener("mouseup", () => {
  if (isDragging) {
    isDragging = false;
    canvas.style.cursor = "grab";
  }
});

canvas.style.cursor = "grab";

// ── Init ──

setMoveMode("circle");
connect();
renderPreview(ctx, getPreviewOpts());
