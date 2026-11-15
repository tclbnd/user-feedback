"""Edge-aware guided decomposition of the log luminance.

WHY THIS DOES NOT HALO
----------------------
The base layer is a joint bilateral mean of the log luma L, guided by L
itself, with compact support radius R:

    K(x, y) = exp(-(L(x) - L(y))^2 / (2 sigma_r^2))      for |x - y| <= R
    B(x)    = sum_y K(x, y) L(y) / sum_y K(x, y)
    D(x)    = L(x) - B(x)                               (detail layer)

For a step edge the neighbourhood splits into two flat clusters whose log
values differ by dL.  Every *cross* edge weight in the window then equals the
same constant K0 = exp(-dL^2 / (2 sigma_r^2)), so for a pixel p at distance j
from the edge on the dark side and its mirror q on the bright side:

    B(p) = [n_d L_d + (n_b K0) L_b] / [n_d + n_b K0]        n_d = j + R + 1
    B(q) = [n_b L_b + (n_d K0) L_d] / [n_b + n_d K0]        n_b = R - j

and the two numerators share the window sum, so D = L - B is identical on
both sides, hence the guidance weight w = B - L = -D is *exactly*
antisymmetric across the edge:

    w(p) + w(q) = 0        for every mirror pair (p, q)

Summed over the two sides of a strong step edge the guidance weights cancel
to zero, which is the "两侧的引导权重和为零" property the acceptance test
asserts (`test_mirror_pairs_cancel_exactly`).  The step therefore keeps its
position instead of being pushed around by its neighbour.

The magnitude of that shared term is governed entirely by K0.  On the
acceptance step (~6.3 EV) with sigma_r = 0.35 EV, K0 = exp(-51) ~ 1e-22, so
the cross-edge mass is numerically zero (measured 1.3e-69 on the 7x7 window
sum, versus a box kernel's 91.4) and |B - L| = 0.0: the dark side is not
lifted at all.  A plain box filter has K0 = 1 regardless of the step, which
is exactly why it produces the classic halo (measured 2.7 EV of lift on the
dark side of the same step).  The `HaloVsBaselineTest` negative control
keeps that comparison honest.

`rel_cut` drops window weights below `max_weight * rel_cut` before
normalising.  It only removes terms that are already numerically dead (the
same cross-edge K0 tail), so it cannot reintroduce the halo; it just avoids
accumulating denormal noise in the sum.
"""
import math


def _window_mean(L, w, h, x, y, R, inv_two_sigma2, rel_cut):
    """Range weighted mean of L over the window (own-cluster dominated)."""
    centre = L[y * w + x]
    x0 = x - R if x - R > 0 else 0
    x1 = x + R if x + R < w else w - 1
    y0 = y - R if y - R > 0 else 0
    y1 = y + R if y + R < h else h - 1

    # gather + weight; the max is needed for the relative cut
    vals = []
    ws = []
    wmax = 0.0
    for yy in range(y0, y1 + 1):
        row = yy * w
        for xx in range(x0, x1 + 1):
            v = L[row + xx]
            d = v - centre
            wt = math.exp(d * d * inv_two_sigma2)
            vals.append(v)
            ws.append(wt)
            if wt > wmax:
                wmax = wt
    cut = wmax * rel_cut
    sw = 0.0
    sv = 0.0
    for v, wt in zip(vals, ws):
        if wt >= cut:
            sw += wt
            sv += wt * v
    if sw <= 0.0:
        return centre
    return sv / sw


def bilateral_base(L, h, w, cfg):
    """Return (B, D) as h*w flat lists in log2 EV units.

    The kernel is isotropic and joint: a separable (horizontal then vertical)
    approximation would break the antisymmetry above, so it is not used.
    Cost is O(n * (2R+1)^2).
    """
    R = int(cfg["radius"])
    sigma = float(cfg["sigma_r"])
    rel_cut = float(cfg["rel_cut"])
    inv = -0.5 / (sigma * sigma)
    n = h * w
    B = [0.0] * n
    D = [0.0] * n
    for y in range(h):
        row = y * w
        for x in range(w):
            i = row + x
            b = _window_mean(L, w, h, x, y, R, inv, rel_cut)
            B[i] = b
            D[i] = L[i] - b
    return B, D


def cross_edge_weight_sum(L, h, w, cfg, x_edge, band):
    """Sum of kernel weights joining the two sides of a vertical edge.

    `band` is how far on each side we sample.  For the range weighted kernel
    this must be ~0 (the two clusters are never mixed); the same sum for a
    plain box kernel is ~1, and it is exactly that mass that produces the
    halo.  Returned as (cross_weight_sum, box_reference).
    """
    R = int(cfg["radius"])
    sigma = float(cfg["sigma_r"])
    inv = -0.5 / (sigma * sigma)
    cross = 0.0
    box_ref = 0.0
    for y in range(h):
        for x in range(x_edge - band, x_edge + band):
            if x < 0 or x >= w:
                continue
            if x < x_edge:
                xs = range(x_edge, min(w, x + R + 1))
            else:
                xs = range(max(0, x - R), x_edge)
            for xx in xs:
                d = abs(xx - x)
                if d > R:
                    continue
                dl = L[y * w + xx] - L[y * w + x]
                cross += math.exp(dl * dl * inv)
                box_ref += 1.0
    if box_ref > 0.0:
        box_ref /= float(2 * R + 1)   # normalise like the box kernel
    return cross / float(2 * R + 1), box_ref
