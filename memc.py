#!/usr/bin/env python3
"""Occlusion-aware motion-compensated frame interpolation (MEMC).

Pure stdlib.  Usage:  python3 memc.py request.json outdir

Pipeline (per adjacent frame pair I0,I1 and each t in (0,1)):

1. Bidirectional block matching (SAD).  Forward flow f01 is estimated on
   I0's block grid (block of I0 -> best match in I1), backward flow f10 on
   I1's grid.  Matching is coarse-to-fine (pyramid, per-level search radius
   +/-r) with half- then quarter-pixel refinement; border blocks and
   out-of-frame motion are handled by partial (overlap-normalized) SAD.
   Every block is matched independently and ties are broken by a fixed
   rule (smallest SAD, then smallest |dx|+|dy|, then smallest (dy,dx)),
   so the block traversal / tiling order cannot influence the result.

2. Mismatch suppression: a matched displacement is snapped back to (0,0)
   when the zero-motion SAD is almost as good (protects low-texture and
   repetitive-texture blocks from false matches).  A per-block confidence
   flag is derived from best SAD vs. block texture energy.

3. Per-pixel refinement: every pixel re-scores the motions of its 3x3
   block neighbourhood plus zero motion on a small local window, so
   mixed-motion boundary blocks cannot drag static pixels along.

4. Per-pixel forward/backward consistency:
       |f01(p) + f10(p + f01(p))| <= 1   (component-wise)
   and symmetrically for f10.  Inconsistent / low-confidence pixels are
   marked occluded.

5. Synthesis (flows map coordinates I0->I1, so a point seen at p at time
   t sits at p - t*f01 in I0 and at p - (1-t)*f10 in I1):
       both consistent : I_t(p) = (1-t)*I0(p - t*f01) + t*I1(p - (1-t)*f10)
       only backward   : I_t(p) = I1(p - (1-t)*f10)     (visible in I1)
       only forward    : I_t(p) = I0(p - t*f01)         (visible in I0)
       neither         : cross-validated uni-directional compensation,
                         else frame copy I0(p).
   Occluded pixels are never blended, so foreground/background are not
   mixed and block-edge morphology stays stable.

Outputs per pair k and time t: interp[_k]_{t}.pgm, occl[_k]_{t}.pgm,
mv[_k]_{t}.json, plus stats.json with occlusion ratio, fallback count,
motion-vector count and per-output decision statistics.
"""
import json
import math
import os
import sys

CONS_TOL = 1.0        # forward/backward consistency tolerance (pixels)
VALID_TAU = 16.0      # cross-validation error tolerance for occluded pixels
CONF_FLAT = 2.0       # per-pixel SAD slack for flat blocks
CONF_REL = 0.125      # SAD slack relative to block texture energy
CONF_ABS = 10.0       # absolute per-pixel SAD slack (mixed-motion blocks)
SNAP_MIN = 2          # zero-motion snap slack: max(SNAP_MIN, npix // 8)


# ---------------------------------------------------------------- PGM I/O
def _pgm_tokens(path):
    with open(path, "rb") as f:
        for line in f:
            h = line.find(b"#")
            if h >= 0:
                line = line[:h]
            for tok in line.split():
                yield tok


def read_pgm(path):
    tk = _pgm_tokens(path)
    magic = next(tk)
    if magic != b"P2":
        raise ValueError("only P2 PGM supported: %s" % path)
    w, h = int(next(tk)), int(next(tk))
    _maxv = int(next(tk))
    px = [int(next(tk)) for _ in range(w * h)]
    return w, h, [px[i * w:(i + 1) * w] for i in range(h)]


def write_pgm(path, w, h, img):
    with open(path, "w") as f:
        f.write("P2\n%d %d\n255\n" % (w, h))
        for row in img:
            f.write(" ".join(str(int(v)) for v in row) + "\n")


# ---------------------------------------------------------------- sampling
def sample_bilinear(img, x, y):
    """Bilinear sample of img at float coords (x, y), border-replicated."""
    h = len(img)
    w = len(img[0])
    x = min(max(x, 0.0), w - 1.0)
    y = min(max(y, 0.0), h - 1.0)
    x0 = int(math.floor(x))
    y0 = int(math.floor(y))
    x1 = min(x0 + 1, w - 1)
    y1 = min(y0 + 1, h - 1)
    fx = x - x0
    fy = y - y0
    a = img[y0][x0]
    b = img[y0][x1]
    c = img[y1][x0]
    d = img[y1][x1]
    return (a * (1.0 - fx) + b * fx) * (1.0 - fy) + \
           (c * (1.0 - fx) + d * fx) * fy


# ---------------------------------------------------------------- pyramid
def downsample2(img):
    h = len(img)
    w = len(img[0])
    out = []
    for y in range(0, h, 2):
        y1 = min(y + 1, h - 1)
        row = []
        for x in range(0, w, 2):
            x1 = min(x + 1, w - 1)
            row.append((img[y][x] + img[y][x1] + img[y1][x] + img[y1][x1] + 2) // 4)
        out.append(row)
    return out


def build_pyramid(img, levels):
    pyr = [img]
    while len(pyr) < levels and min(len(pyr[-1]), len(pyr[-1][0])) >= 16:
        pyr.append(downsample2(pyr[-1]))
    return pyr


# ---------------------------------------------------------------- matching
def block_sad_partial(ref, ry, rx, src, sy, sx, bh, bw):
    """SAD of the ref block at (rx, ry) against src shifted by (sx, sy),
    restricted to the overlapping region (border blocks / out-of-frame
    motion).  Returns (sad, overlap_pixels); (None, 0) if no overlap."""
    H = len(src)
    W = len(src[0])
    x0 = max(0, sx)
    x1 = min(W, sx + bw)
    y0 = max(0, sy)
    y1 = min(H, sy + bh)
    if x0 >= x1 or y0 >= y1:
        return None, 0
    s = 0
    for j in range(y0, y1):
        rrow = ref[ry + j - sy]
        srow = src[j]
        for i in range(x0, x1):
            d = rrow[rx + i - sx] - srow[i]
            s += d if d >= 0 else -d
    return s, (y1 - y0) * (x1 - x0)


def block_sad_subpix(ref, ry, rx, src, sy, sx, bh, bw):
    """SAD with bilinear sampling of src at fractional (sx, sy)."""
    H = len(src)
    W = len(src[0])
    total = 0.0
    for j in range(bh):
        yy = sy + j
        y0 = int(math.floor(yy))
        fy = yy - y0
        if y0 < 0:
            y0, fy = 0, 0.0
        elif y0 >= H - 1:
            y0, fy = H - 1, 0.0
        r0 = src[y0]
        r1 = src[min(y0 + 1, H - 1)]
        rrow = ref[ry + j]
        for i in range(bw):
            xx = sx + i
            x0 = int(math.floor(xx))
            fx = xx - x0
            if x0 < 0:
                x0, fx = 0, 0.0
            elif x0 >= W - 1:
                x0, fx = W - 1, 0.0
            x1 = min(x0 + 1, W - 1)
            v = (r0[x0] * (1.0 - fx) + r0[x1] * fx) * (1.0 - fy) + \
                (r1[x0] * (1.0 - fx) + r1[x1] * fx) * fy
            total += abs(rrow[rx + i] - v)
    return total


def window_sad_subpix(ref, ry, rx, src, sy, sx, bh, bw):
    """Partial SAD with bilinear src sampling at fractional (sx, sy):
    only ref pixels whose sample position falls inside src are counted.
    Returns (sad, n)."""
    H = len(src)
    W = len(src[0])
    total = 0.0
    n = 0
    for j in range(bh):
        yy = sy + j
        if yy < 0.0 or yy > H - 1:
            continue
        y0 = int(math.floor(yy))
        fy = yy - y0
        y1 = min(y0 + 1, H - 1)
        r0 = src[y0]
        r1 = src[y1]
        rrow = ref[ry + j]
        for i in range(bw):
            xx = sx + i
            if xx < 0.0 or xx > W - 1:
                continue
            x0 = int(math.floor(xx))
            fx = xx - x0
            x1 = min(x0 + 1, W - 1)
            v = (r0[x0] * (1.0 - fx) + r0[x1] * fx) * (1.0 - fy) + \
                (r1[x0] * (1.0 - fx) + r1[x1] * fx) * fy
            total += abs(rrow[rx + i] - v)
            n += 1
    return total, n


def block_energy(img, y0, x0, bh, bw):
    """Sum of absolute deviation from the block mean (texture energy)."""
    npix = bh * bw
    acc = 0
    for j in range(bh):
        acc += sum(img[y0 + j][x0:x0 + bw])
    mean = acc / float(npix)
    e = 0.0
    for j in range(bh):
        for v in img[y0 + j][x0:x0 + bw]:
            e += abs(v - mean)
    return e


def _candidate_order(radius, cdx, cdy):
    """All candidate displacements in cdx/cdy +/- radius, sorted by a fixed
    rule: smallest |Dx|+|Dy| first, then smallest (Dy, Dx).  Combined with
    strict-< comparison this resolves ties toward the smallest motion,
    deterministically and independently of any block traversal order."""
    cands = [(cdx + ox, cdy + oy)
             for oy in range(-radius, radius + 1)
             for ox in range(-radius, radius + 1)]
    cands.sort(key=lambda d: (abs(d[0]) + abs(d[1]), d[1], d[0]))
    return cands


def match_block(ref, src, x0, y0, bw, bh, radius, cdx, cdy, slack=0.0,
                cands=None):
    """Best displacement for the ref block at (x0, y0) searched in src.

    Partial-block matching: candidates whose block leaves the src frame are
    scored on the overlapping region only (normalized SAD = SAD/overlap),
    provided at least half of the block overlaps.  Returns
    (Dx, Dy, score, overlap, ties) where `ties` lists all candidates
    within `slack` of the best score (for ambiguity resolution).
    """
    npix = bw * bh
    min_overlap = max(1, npix // 2)
    if cands is None:
        cands = _candidate_order(radius, cdx, cdy)
    best = None
    best_score = None
    best_n = 0
    scored = []
    for Dx, Dy in cands:
        sad, n = block_sad_partial(ref, y0, x0, src, y0 + Dy, x0 + Dx, bh, bw)
        if n < min_overlap:
            continue
        score = sad / float(n)
        scored.append((score, Dx, Dy, n))
        if best_score is None or score < best_score:
            best_score = score
            best = (Dx, Dy)
            best_n = n
    if best is None:
        return 0, 0, float("inf"), 0, []
    ties = [(Dx, Dy) for score, Dx, Dy, n in scored
            if score <= best_score + slack]
    return best[0], best[1], best_score, best_n, ties


def disambiguate(ref, src, x0, y0, bw, bh, ties, margin, best_score):
    """Resolve ambiguous (near-optimal) integer matches by re-scoring each
    candidate on an expanded window (block + margin), which contains
    surrounding edges and therefore pins down the true motion where the
    bare block cannot (uniform interiors).  Deterministic: candidates are
    evaluated in the fixed _candidate_order sequence with strict-<."""
    if len(ties) <= 1:
        return ties[0] if ties else (0, 0)
    # exact match including zero motion -> static, no need to look further
    if best_score == 0.0 and (0, 0) in ties:
        return (0, 0)
    H = len(ref)
    W = len(ref[0])
    wx0 = max(0, x0 - margin)
    wy0 = max(0, y0 - margin)
    wx1 = min(W, x0 + bw + margin)
    wy1 = min(H, y0 + bh + margin)
    ww = wx1 - wx0
    wh = wy1 - wy0
    min_overlap = max(1, (ww * wh) // 2)
    best = None
    best_score = None
    for Dx, Dy in ties:
        sad, n = block_sad_partial(ref, wy0, wx0, src,
                                   wy0 + Dy, wx0 + Dx, wh, ww)
        if n < min_overlap:
            continue
        score = sad / float(n)
        if best_score is None or score < best_score:
            best_score = score
            best = (Dx, Dy)
    return best if best is not None else ties[0]


def compute_flow_field(pyr_ref, pyr_src, block, radius, order=None):
    """Block flow field ref->src.  Returns (fx, fy, conf) block grids.

    Each block is processed independently; `order` (a permutation of block
    indices) may reorder the traversal without changing the output.
    """
    img = pyr_ref[0]
    h = len(img)
    w = len(img[0])
    nbx = (w + block - 1) // block
    nby = (h + block - 1) // block
    fx = [[0.0] * nbx for _ in range(nby)]
    fy = [[0.0] * nbx for _ in range(nby)]
    conf = [[False] * nbx for _ in range(nby)]
    nlevels = len(pyr_ref)
    idxs = list(range(nbx * nby))
    if order is not None:
        assert sorted(order) == idxs, "order must be a permutation of blocks"
        idxs = list(order)
    ref0 = pyr_ref[0]
    src0 = pyr_src[0]
    for lin in idxs:
        by, bx = divmod(lin, nbx)
        x0 = bx * block
        y0 = by * block
        bw = min(block, w - x0)
        bh = min(block, h - y0)
        npix = bw * bh
        snap_slack = max(SNAP_MIN, npix // 8) / float(npix)
        energy_pp = block_energy(ref0, y0, x0, bh, bw) / float(npix)
        amb_slack = CONF_FLAT + CONF_REL * energy_pp + CONF_ABS
        # coarse-to-fine integer search (per-level radius `radius`)
        Dx = Dy = 0
        for lev in range(nlevels - 1, -1, -1):
            s = 1 << lev
            ref = pyr_ref[lev]
            src = pyr_src[lev]
            lx0 = x0 // s
            ly0 = y0 // s
            lbw = min(max(1, bw // s), len(ref[0]) - lx0)
            lbh = min(max(1, bh // s), len(ref) - ly0)
            cdx = int(round(Dx / float(s)))
            cdy = int(round(Dy / float(s)))
            if lev == 0:
                # finest level: search around the coarse estimate AND around
                # zero motion, so a wrong coarse hypothesis (e.g. periodic
                # texture) cannot hide the true displacement
                cands = sorted(
                    set(_candidate_order(radius, cdx, cdy))
                    | set(_candidate_order(radius, 0, 0)),
                    key=lambda d: (abs(d[0]) + abs(d[1]), d[1], d[0]))
                dx, dy, score, novl, ties = match_block(
                    ref, src, lx0, ly0, lbw, lbh, radius, cdx, cdy,
                    slack=amb_slack, cands=cands)
                # ambiguity resolution on an expanded window
                if len(ties) > 1:
                    dx, dy = disambiguate(ref, src, lx0, ly0, lbw, lbh,
                                          ties, max(2, lbw // 2), score)
                    sad, novl = block_sad_partial(ref, ly0, lx0, src,
                                                  ly0 + dy, lx0 + dx,
                                                  lbh, lbw)
                    score = sad / float(novl) if novl else float("inf")
            else:
                dx, dy, score, novl, _ = match_block(
                    ref, src, lx0, ly0, lbw, lbh, radius, cdx, cdy)
            Dx, Dy = dx * s, dy * s
        best_score = score
        full_overlap = novl >= npix
        # sub-pixel refinement at finest level (half-, then quarter-pixel,
        # fixed candidate order); skipped for partial (out-of-frame) matches
        bxf, byf = float(Dx), float(Dy)
        if full_overlap:
            for step in (0.5, 0.25):
                best_sad = block_sad_subpix(ref0, y0, x0, src0,
                                            y0 + byf, x0 + bxf, bh, bw)
                improved = False
                for oy in (-step, 0.0, step):
                    for ox in (-step, 0.0, step):
                        if ox == 0.0 and oy == 0.0:
                            continue
                        s = block_sad_subpix(ref0, y0, x0, src0,
                                             y0 + byf + oy, x0 + bxf + ox,
                                             bh, bw)
                        if s < best_sad - 1e-9:
                            best_sad = s
                            bxf += ox
                            byf += oy
                            improved = True
                if not improved:
                    break
            best_score = best_sad / float(npix)
        # zero-motion snap: suppress spurious matches in low / repeated
        # texture when standing still is essentially as good
        sad0, n0 = block_sad_partial(ref0, y0, x0, src0, y0, x0, bh, bw)
        score0 = sad0 / float(n0) if n0 else float("inf")
        if n0 == npix and score0 <= best_score + snap_slack:
            bxf = byf = 0.0
            best_score = score0
        # confidence: match error small relative to block texture energy,
        # and clearly better than standing still
        conf[by][bx] = (best_score <= CONF_FLAT + CONF_REL * energy_pp
                        + CONF_ABS
                        and best_score <= 0.5 * score0 + 1.0)
        fx[by][bx] = bxf
        fy[by][bx] = byf
    return fx, fy, conf


def refine_flow_per_pixel(img_ref, img_src, fx, fy, conf_blk, block, win=2):
    """Per-pixel flow refinement.

    Each pixel re-scores the candidate motions of its 3x3 block
    neighbourhood plus zero motion on a small local window and keeps the
    best one.  This fixes mixed-motion boundary blocks (a block dominated
    by a moving object must not drag its static-background pixels along)
    while remaining a pure function of the block flow field, hence
    deterministic and block-order independent.  Returns per-pixel
    (vx, vy, conf) maps.
    """
    h = len(img_ref)
    w = len(img_ref[0])
    nby = len(fx)
    nbx = len(fx[0])
    ofx = [[0.0] * w for _ in range(h)]
    ofy = [[0.0] * w for _ in range(h)]
    ocf = [[False] * w for _ in range(h)]
    for y in range(h):
        by = min(y // block, nby - 1)
        yblo = max(0, by - 1)
        ybhi = min(nby, by + 2)
        for x in range(w):
            bx = min(x // block, nbx - 1)
            cands = {(0.0, 0.0)}
            for yy in range(yblo, ybhi):
                for xx in range(max(0, bx - 1), min(nbx, bx + 2)):
                    cands.add((fx[yy][xx], fy[yy][xx]))
            cands = sorted(cands,
                           key=lambda d: (abs(d[0]) + abs(d[1]), d[1], d[0]))
            wx0 = max(0, x - win)
            wy0 = max(0, y - win)
            wx1 = min(w, x + win + 1)
            wy1 = min(h, y + win + 1)
            bw = wx1 - wx0
            bh = wy1 - wy0
            npix = bw * bh
            minov = max(1, npix // 2)
            sad0, n0 = block_sad_partial(img_ref, wy0, wx0, img_src,
                                         wy0, wx0, bh, bw)
            s0 = sad0 / float(n0)
            energy = 0.0
            for j in range(wy0, wy1):
                row = img_ref[j]
                for i in range(wx0, wx1):
                    energy += row[i]
            mean = energy / float(npix)
            energy = 0.0
            for j in range(wy0, wy1):
                row = img_ref[j]
                for i in range(wx0, wx1):
                    energy += abs(row[i] - mean)
            energy /= float(npix)
            static_evidence = s0 <= CONF_FLAT + CONF_REL * energy
            own = (fx[by][bx], fy[by][bx])
            sad_own, n_own = window_sad_subpix(
                img_ref, wy0, wx0, img_src,
                wy0 + own[1], wx0 + own[0], bh, bw)
            if n_own < minov:
                # own block flow not verifiable at this pixel (window would
                # leave the frame): keep it unless the pixel is clearly
                # static, in which case zero motion is the safer estimate
                if static_evidence:
                    best = (0.0, 0.0)
                    bests = s0
                else:
                    best = own
                    bests = None
            else:
                best = (0.0, 0.0)
                bests = s0
                for Dx, Dy in cands:
                    if Dx == 0.0 and Dy == 0.0:
                        continue
                    sad, n = window_sad_subpix(img_ref, wy0, wx0, img_src,
                                               wy0 + Dy, wx0 + Dx, bh, bw)
                    if n < minov:
                        continue
                    s = sad / float(n)
                    if s < bests - 1e-9:
                        bests = s
                        best = (Dx, Dy)
            if bests is None:
                # unverifiable kept block flow: inherit block confidence
                ocf[y][x] = conf_blk[by][bx]
            else:
                ocf[y][x] = (bests <= CONF_FLAT + CONF_REL * energy
                             + CONF_ABS
                             and bests <= 0.5 * s0 + 1.0)
            ofx[y][x] = best[0]
            ofy[y][x] = best[1]
    return ofx, ofy, ocf


# ---------------------------------------------------------------- synthesis
def synthesize(i0, i1, px01, px10, t):
    """Build the interpolated frame, occlusion map and decision counts.

    px01 / px10 are per-pixel (vx, vy, conf) maps.  Decision per pixel
    (f01/f10 map coordinates I0->I1 / I1->I0):
      both directions consistent -> bidirectional blend
      exactly one consistent     -> uni-directional compensation
      one side confident only    -> trust the confident side
      neither                    -> cross-validated uni compensation,
                                    else frame copy.
    A direction whose sample position falls outside its source frame is
    unusable and demotes the decision to the other direction / copy.
    """
    h = len(i0)
    w = len(i0[0])
    v01x, v01y, c01 = px01
    v10x, v10y, c10 = px10
    out = [[0] * w for _ in range(h)]
    occl = [[0] * w for _ in range(h)]
    counts = {"bidir": 0, "uni_i0": 0, "uni_i1": 0, "copy": 0}
    for y in range(h):
        for x in range(w):
            fx = v01x[y][x]
            fy = v01y[y][x]
            qx = min(max(int(round(x + fx)), 0), w - 1)
            qy = min(max(int(round(y + fy)), 0), h - 1)
            gx = v10x[qy][qx]
            gy = v10y[qy][qx]
            c01p = c01[y][x]
            c10p = c10[y][x]
            fwd_geo = (c01p and c10[qy][qx]
                       and abs(fx + gx) <= CONS_TOL
                       and abs(fy + gy) <= CONS_TOL)
            hx = v10x[y][x]
            hy = v10y[y][x]
            rx = min(max(int(round(x + hx)), 0), w - 1)
            ry = min(max(int(round(y + hy)), 0), h - 1)
            ex = v01x[ry][rx]
            ey = v01y[ry][rx]
            bwd_geo = (c10p and c01[ry][rx]
                       and abs(hx + ex) <= CONS_TOL
                       and abs(hy + ey) <= CONS_TOL)
            # sample positions (content at p at time t sits at p - t*f01
            # in I0 and at p - (1-t)*f10 in I1)
            s0x = x - t * fx
            s0y = y - t * fy
            s1x = x - (1.0 - t) * hx
            s1y = y - (1.0 - t) * hy
            in0 = 0.0 <= s0x <= w - 1 and 0.0 <= s0y <= h - 1
            in1 = 0.0 <= s1x <= w - 1 and 0.0 <= s1y <= h - 1
            # primary decision
            if fwd_geo and bwd_geo:
                mode = "blend"
            elif fwd_geo:
                mode = "i0"
            elif bwd_geo:
                mode = "i1"
            elif c10p and not c01p:
                mode = "i1"
            elif c01p and not c10p:
                mode = "i0"
            else:
                mode = "val"
            # out-of-frame demotion
            if mode == "blend":
                if not in0 and not in1:
                    mode = "copy"
                elif not in0:
                    mode = "i1"
                elif not in1:
                    mode = "i0"
            elif mode == "i0" and not in0:
                mode = "i1" if (in1 and (bwd_geo or c10p)) else "copy"
            elif mode == "i1" and not in1:
                mode = "i0" if (in0 and (fwd_geo or c01p)) else "copy"
            if mode == "val":
                # cross-validate each single-direction compensation:
                # the compensated value must reappear in the other frame
                cands = []
                if in0:
                    v0 = sample_bilinear(i0, s0x, s0y)
                    e0 = abs(v0 - sample_bilinear(i1, x + (1.0 - t) * fx,
                                                  y + (1.0 - t) * fy))
                    cands.append((e0, 0, v0))
                if in1:
                    v1 = sample_bilinear(i1, s1x, s1y)
                    e1 = abs(v1 - sample_bilinear(i0, x + t * hx,
                                                  y + t * hy))
                    cands.append((e1, 1, v1))
                best = None
                for e, side, v in cands:        # fixed order: i0 first
                    if best is None or e < best[0]:
                        best = (e, side, v)
                if best is not None and best[0] <= VALID_TAU:
                    mode = "i0" if best[1] == 0 else "i1"
                    val = best[2]
                else:
                    mode = "copy"
            if mode == "blend":
                val = ((1.0 - t) * sample_bilinear(i0, s0x, s0y)
                       + t * sample_bilinear(i1, s1x, s1y))
                counts["bidir"] += 1
            elif mode == "i0":
                val = sample_bilinear(i0, s0x, s0y)
                counts["uni_i0"] += 1
            elif mode == "i1":
                val = sample_bilinear(i1, s1x, s1y)
                counts["uni_i1"] += 1
            else:
                val = i0[y][x]                  # frame copy fallback
                counts["copy"] += 1
            occl[y][x] = 0 if (fwd_geo and bwd_geo) else 255
            iv = int(round(val))
            out[y][x] = 0 if iv < 0 else (255 if iv > 255 else iv)
    return out, occl, counts


def interpolate_pair(i0, i1, t, block=8, radius=8, levels=2, order=None):
    """Full pipeline for one frame pair.  Returns a result dict."""
    h = len(i0)
    w = len(i0[0])
    pyr0 = build_pyramid(i0, levels)
    pyr1 = build_pyramid(i1, levels)
    f01 = compute_flow_field(pyr0, pyr1, block, radius, order)
    f10 = compute_flow_field(pyr1, pyr0, block, radius, order)
    px01 = refine_flow_per_pixel(i0, i1, f01[0], f01[1], f01[2], block)
    px10 = refine_flow_per_pixel(i1, i0, f10[0], f10[1], f10[2], block)
    img, occl, counts = synthesize(i0, i1, px01, px10, t)
    occl_px = sum(1 for row in occl for v in row if v)
    stats = {
        "occl_ratio": occl_px / float(w * h),
        "fallback_px": counts["copy"],
        "mv_count": 2 * len(f01[0]) * len(f01[0][0]),
        "decisions": dict(counts),
    }
    mv = {
        "block": block,
        "grid": [len(f01[0][0]), len(f01[0])],
        "f01": [[[f01[0][y][x], f01[1][y][x]] for x in range(len(f01[0][0]))]
                for y in range(len(f01[0]))],
        "f10": [[[f10[0][y][x], f10[1][y][x]] for x in range(len(f10[0][0]))]
                for y in range(len(f10[0]))],
        "conf01": f01[2],
        "conf10": f10[2],
    }
    return {"img": img, "occl": occl, "stats": stats, "mv": mv,
            "f01": f01, "f10": f10}


# ---------------------------------------------------------------- driver
def main():
    req = json.load(open(sys.argv[1]))
    outdir = sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    base = os.path.dirname(os.path.abspath(sys.argv[1]))
    frames = [read_pgm(os.path.join(base, fr["file"])) for fr in req["frames"]]
    block = int(req.get("block", 8))
    radius = int(req.get("search", 8))
    levels = int(req.get("levels", 2))
    ts = req["t"]
    total_px = 0
    total_occl = 0
    total_fallback = 0
    mv_count = 0
    per_output = {}
    for k in range(len(frames) - 1):
        w0, h0, i0 = frames[k]
        w1, h1, i1 = frames[k + 1]
        if (w0, h0) != (w1, h1):
            raise ValueError("frame size mismatch")
        # flows are t-independent: compute once per pair
        pyr0 = build_pyramid(i0, levels)
        pyr1 = build_pyramid(i1, levels)
        f01 = compute_flow_field(pyr0, pyr1, block, radius)
        f10 = compute_flow_field(pyr1, pyr0, block, radius)
        px01 = refine_flow_per_pixel(i0, i1, f01[0], f01[1], f01[2], block)
        px10 = refine_flow_per_pixel(i1, i0, f10[0], f10[1], f10[2], block)
        mv_count += 2 * len(f01[0]) * len(f01[0][0])
        for t in ts:
            img, occl, counts = synthesize(i0, i1, px01, px10, float(t))
            tag = str(t) if len(frames) == 2 else "%d_%s" % (k, t)
            write_pgm(os.path.join(outdir, "interp_%s.pgm" % tag), w0, h0, img)
            write_pgm(os.path.join(outdir, "occl_%s.pgm" % tag), w0, h0, occl)
            mv = {
                "block": block,
                "grid": [len(f01[0][0]), len(f01[0])],
                "f01": [[[f01[0][y][x], f01[1][y][x]]
                         for x in range(len(f01[0][0]))]
                        for y in range(len(f01[0]))],
                "f10": [[[f10[0][y][x], f10[1][y][x]]
                         for x in range(len(f10[0][0]))]
                        for y in range(len(f10[0]))],
                "conf01": f01[2],
                "conf10": f10[2],
            }
            with open(os.path.join(outdir, "mv_%s.json" % tag), "w") as f:
                json.dump(mv, f)
            occl_px = sum(1 for row in occl for v in row if v)
            total_px += w0 * h0
            total_occl += occl_px
            total_fallback += counts["copy"]
            per_output["interp_%s" % tag] = {
                "occl_ratio": occl_px / float(w0 * h0),
                "fallback_px": counts["copy"],
                "decisions": dict(counts),
            }
    stats = {
        "occl_ratio": (total_occl / float(total_px)) if total_px else 0.0,
        "fallback_px": total_fallback,
        "mv_count": mv_count,
        "outputs": per_output,
    }
    with open(os.path.join(outdir, "stats.json"), "w") as f:
        json.dump(stats, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
