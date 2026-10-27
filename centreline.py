"""
Centreline tracing — turns a 1-px skeleton into single-stroke polylines.

find_contours() traces the *outline* of a mask, so a thin line comes back as a
closed loop around it and the plotter draws every stroke twice. This module
walks the skeleton pixels instead, producing one path per stroke:

  1. Build an 8-connected pixel graph (diagonal shortcuts across an L-corner
     are dropped so corners don't look like junctions).
  2. Walk chains between endpoints / junctions; leftover edges are closed loops.
  3. Prune short spurs (skeleton "hairs"; width-aware when the stroke mask is
     given) and re-trace until stable, so chains that were split only by a
     spur become one continuous stroke.
  4. At each junction, join the pair of chains that continues most straight.
  5. Light smoothing to remove pixel staircase.

All paths are (N, 2) float arrays in (row, col) order, matching the
measure.find_contours convention used by the rest of the app.
"""

import math

import numpy as np
from scipy import ndimage

_NBRS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def _graph(on):
    adj = {}
    for r, c in on:
        nb = []
        for dr, dc in _NBRS:
            q = (r + dr, c + dc)
            if q not in on:
                continue
            # Diagonal step across an L-corner: use the 4-connected route instead
            if dr and dc and ((r + dr, c) in on or (r, c + dc) in on):
                continue
            nb.append(q)
        adj[(r, c)] = nb
    return adj


def _ekey(a, b):
    return (a, b) if a < b else (b, a)


def _trace(adj):
    """Split the graph into chains that end at endpoints/junctions (or loop)."""
    seen = set()

    def walk(start, nxt):
        path = [start, nxt]
        seen.add(_ekey(start, nxt))
        prev, cur = start, nxt
        while len(adj[cur]) == 2:
            a, b = adj[cur]
            n = b if a == prev else a
            k = _ekey(cur, n)
            if k in seen:
                break
            seen.add(k)
            path.append(n)
            prev, cur = cur, n
        return path

    chains = []
    for p, nb in adj.items():           # chains starting at endpoints/junctions
        if len(nb) != 2:
            for q in nb:
                if _ekey(p, q) not in seen:
                    chains.append(walk(p, q))
    for p, nb in adj.items():           # whatever is left is a closed loop
        for q in nb:
            if _ekey(p, q) not in seen:
                chains.append(walk(p, q))
    return chains


def _prune_spurs(chains, adj, spur_len, dist=None, width_factor=2.5):
    """Drop endpoint->junction chains shorter than spur_len pixels, or shorter
    than width_factor x the stroke half-width at the junction (a bump on a
    thick stroke skeletonises into a hair about as long as the stroke is wide)."""
    keep = []
    for ch in chains:
        da, db = len(adj[ch[0]]), len(adj[ch[-1]])
        if da == 1 and db >= 3:
            junction = ch[-1]
        elif db == 1 and da >= 3:
            junction = ch[0]
        else:
            keep.append(ch)
            continue
        limit = spur_len
        if dist is not None:
            limit = max(limit, width_factor * dist[junction])
        if len(ch) >= limit:
            keep.append(ch)
    return keep


def _direction(ch, at_start, n=6):
    """Unit vector pointing *out of* the junction along the chain."""
    seg = ch[:n] if at_start else ch[::-1][:n]
    (r0, c0), (r1, c1) = seg[0], seg[-1]
    d = math.hypot(r1 - r0, c1 - c0)
    return ((r1 - r0) / d, (c1 - c0) / d) if d else (0.0, 0.0)


def _join_at_junctions(chains):
    """Greedily merge the straightest pair of chains meeting at each junction."""
    chains = [list(c) for c in chains]
    alive = [True] * len(chains)
    ends = {}                                   # pixel -> [(chain_idx, at_start)]
    for i, ch in enumerate(chains):
        if ch[0] == ch[-1]:
            continue                            # closed loop
        ends.setdefault(ch[0], []).append((i, True))
        ends.setdefault(ch[-1], []).append((i, False))

    for px in list(ends):
        lst = ends[px]
        while True:
            lst[:] = [(i, s) for i, s in lst
                      if alive[i] and (chains[i][0] if s else chains[i][-1]) == px]
            if len(lst) < 2:
                break
            best = None
            for a in range(len(lst)):
                for b in range(a + 1, len(lst)):
                    (i, si), (j, sj) = lst[a], lst[b]
                    if i == j:
                        continue
                    ui, uj = _direction(chains[i], si), _direction(chains[j], sj)
                    straight = -(ui[0] * uj[0] + ui[1] * uj[1])   # 1 = straight on
                    if best is None or straight > best[0]:
                        best = (straight, a, b)
            if best is None or best[0] < 0.0:   # sharper than 90° — leave a pen lift
                break
            _, a, b = best
            (i, si), (j, sj) = lst[a], lst[b]
            ci = chains[i] if not si else chains[i][::-1]          # ends at px
            cj = chains[j] if sj else chains[j][::-1]              # starts at px
            merged = ci + cj[1:]
            chains[i] = merged
            alive[j] = False
            # Re-register the merged chain's far ends
            for end, at_start in ((merged[0], True), (merged[-1], False)):
                ends.setdefault(end, []).append((i, at_start))
            lst[:] = [(k, s) for k, s in lst if k not in (i, j)]
    return [c for c, ok in zip(chains, alive) if ok]


def _smooth(p, passes):
    """3-tap moving average; endpoints of open paths stay fixed."""
    if passes <= 0 or len(p) < 3:
        return p
    closed = np.array_equal(p[0], p[-1])
    for _ in range(passes):
        if closed:
            q = p[:-1]
            q = (np.roll(q, 1, 0) + 2 * q + np.roll(q, -1, 0)) / 4
            p = np.vstack([q, q[:1]])
        else:
            p = p.copy()
            p[1:-1] = (p[:-2] + 2 * p[1:-1] + p[2:]) / 4
    return p


def trace_centrelines(skel, mask=None, spur_len=8, smooth_passes=2, join=True,
                      max_prune_rounds=10):
    """
    skel: boolean array, 1-px wide strokes (e.g. skimage.morphology.skeletonize).
    mask: optional boolean stroke mask the skeleton came from; enables
          width-aware spur pruning.
    Returns a list of (N, 2) float arrays in (row, col) order.
    """
    ys, xs = np.nonzero(skel)
    on = set(zip(ys.tolist(), xs.tolist()))
    if not on:
        return []

    dist = ndimage.distance_transform_edt(mask) if mask is not None else None
    adj = _graph(on)
    chains = _trace(adj)
    if spur_len > 0 or dist is not None:
        # Removing a spur can expose a new one (a hair on a hair): repeat until
        # stable, re-tracing so former junctions become plain chain pixels.
        for _ in range(max_prune_rounds):
            kept = _prune_spurs(chains, adj, spur_len, dist)
            if len(kept) == len(chains):
                break
            on = {px for ch in kept for px in ch}
            adj = _graph(on)
            chains = _trace(adj)
    if join:
        chains = _join_at_junctions(chains)

    return [_smooth(np.asarray(ch, dtype=float), smooth_passes)
            for ch in chains if len(ch) >= 2]
