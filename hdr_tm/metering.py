"""Zone-weighted metering in the log domain.

INVARIANCE CONTRACT
-------------------
Every quantity that reaches the tone curve is derived from *integer* sums:
per pixel the log luma is quantised on a 2**20 grid and the reliability
weight on a 2**16 grid, and each zone stores exact integer accumulators.
The global statistics are the integer sums over all zones, therefore

    stats(any tiling)  ==  stats(any other tiling)

bit for bit, and the zone visit order is irrelevant by commutativity of
integer addition.  Zone *means* are floats (they are per zone by definition)
but they never feed back into the global statistics.
"""
import math

from .config import (BLACK_EPS, BLACK_FLOOR, KEY, LOG_CLAMP_EV, LOG_KEY,
                     LOG_SCALE, WEIGHT_SCALE)

HIST_BINS_PER_EV = 16          # white-point histogram resolution (bins per EV)
PURE_BLACK = 0.0               # Y <= 0 is a pure black sample
NO_VOTE = 0                    # quantised weight of a pure black pixel

_SCALE_I = HIST_BINS_PER_EV    # bins per EV (NOT a bit shift: 16 bins, not 2**16)


def _smoothstep(t):
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def _border_weight(r, c, h, w, band, floor):
    """1 in the frame interior, decaying to `floor` on the outermost pixel.

    Defined on the *frame*, never on a zone, so it cannot depend on tiling.
    `band is None` means "auto"; the caller (meter) resolves it, but this
    helper also accepts None so it stays usable standalone.
    """
    if band is None:
        band = build_border(h, w, {"border": None})
    if band <= 0:
        return 1.0
    d = min(r, c, h - 1 - r, w - 1 - c)
    if d >= band:
        return 1.0
    # d == 0 (outermost pixel) -> floor, d == band-1 -> just under 1
    t = d / float(band - 1) if band > 1 else 1.0
    return floor + (1.0 - floor) * _smoothstep(t)


def pixel_weight(y, r, c, h, w, cfg):
    """Reliability weight in [0, 1] for one pixel (zones never enter here)."""
    if y <= PURE_BLACK:
        return PURE_BLACK
    log_y = math.log2(max(y, BLACK_FLOOR))

    # (a) mid-tone emphasis: the meter trusts mid greys most
    t = (log_y - LOG_KEY) / cfg["tone_sigma"]
    w_mid = math.exp(-0.5 * t * t)
    if w_mid < 1.0 / 256.0:
        w_mid = 1.0 / 256.0            # floor keeps ultra dark scenes votable

    # (b) highlight guard: blown samples must not drag the exposure down
    lo, hi = cfg["clip_lo"], cfg["clip_hi"]
    if y <= lo:
        w_hi = 1.0
    elif y >= hi:
        w_hi = 0.0
    else:
        w_hi = 1.0 - _smoothstep((math.log2(y) - math.log2(lo)) /
                                 (math.log2(hi) - math.log2(lo)))

    # (c) frame border weight: unreliable edge pixels vote with less power
    w_bd = _border_weight(r, c, h, w, cfg["border"], cfg["border_floor"])
    return w_mid * w_hi * w_bd


def quantise_log(y):
    """Quantised log2 luma (integer), clamped to a safe fixed-point range.

    The clamp is what keeps a 1e300 sample (or a denormal) from overflowing
    the accumulator or indexing past the histogram; the clamp bounds are far
    outside any real sensor's dynamic range (+-4096 EV on the log2 scale).
    """
    if y != y:                       # NaN cannot vote
        y = BLACK_FLOOR
    log_y = math.log2(max(y, BLACK_FLOOR))
    if log_y > LOG_CLAMP_EV:
        log_y = LOG_CLAMP_EV
    elif log_y < -LOG_CLAMP_EV:
        log_y = -LOG_CLAMP_EV
    return int(round(log_y * LOG_SCALE))


def quantise_weight(w):
    if w <= 0.0:
        return NO_VOTE
    q = int(round(w * WEIGHT_SCALE))
    return q if q > 0 else NO_VOTE


class ZoneStat(object):
    """Exact integer accumulators for one zone."""

    __slots__ = ("index", "row", "col", "r0", "r1", "c0", "c1",
                 "n", "n_black", "sum_w", "sum_wq", "sum_wq2", "sum_q")

    def __init__(self, index, row, col, r0, r1, c0, c1):
        self.index = index
        self.row = row
        self.col = col
        self.r0, self.r1, self.c0, self.c1 = r0, r1, c0, c1
        self.n = 0
        self.n_black = 0
        self.sum_w = 0
        self.sum_wq = 0
        self.sum_wq2 = 0
        self.sum_q = 0

    def add(self, q, w_q, is_black):
        self.n += 1
        self.sum_q += q
        if is_black:
            self.n_black += 1
        else:
            self.sum_w += w_q
            self.sum_wq += w_q * q
            self.sum_wq2 += w_q * q * q

    @property
    def black_fraction(self):
        return self.n_black / self.n if self.n else 0.0

    @property
    def mean_log_ev(self):
        """Weighted mean log2 luma; falls back to the plain mean when the zone
        carries no vote (a fully pure black zone)."""
        if self.sum_w > 0:
            return (self.sum_wq / self.sum_w) / LOG_SCALE
        if self.n > 0:
            return (self.sum_q / self.n) / LOG_SCALE
        return math.log2(BLACK_FLOOR)

    def as_dict(self):
        return {
            "index": self.index, "row": self.row, "col": self.col,
            "bounds": [self.r0, self.r1, self.c0, self.c1],
            "pixels": self.n,
            "black_pixels": self.n_black,
            "black_fraction": round(self.black_fraction, 12),
            "weight": self.sum_w,
            "weight_sum_q": self.sum_wq,
            "mean_ev": round(self.mean_log_ev, 12),
        }


def build_border(h, w, cfg):
    band = cfg["border"]
    if band is None:
        band = max(1, min(h, w) // 16)
    return int(band)


def meter(y, h, w, bounds_r, bounds_c, order, cfg):
    """Run the zone metering.

    Returns (stats, zones) where stats holds the integer global sums and the
    derived exposure / white point, and zones is the per zone breakdown.
    """
    band = build_border(h, w, cfg)
    cfg = dict(cfg)
    cfg["border"] = band

    # per pixel log luma and weight are tiling independent by construction;
    # the histogram spans the full clamped range on a 1/16 EV grid, so no
    # sample can ever fall outside it
    qs = [[quantise_log(v) for v in row] for row in y]
    bins_per_ev = _SCALE_I
    q_per_bin = LOG_SCALE // bins_per_ev
    hist_lo = int(-LOG_CLAMP_EV * bins_per_ev)
    hist_hi = int(LOG_CLAMP_EV * bins_per_ev)

    zones = []
    nr = len(bounds_r) - 1
    nc = len(bounds_c) - 1
    for zi in range(nr):
        for zj in range(nc):
            zones.append(ZoneStat(len(zones), zi, zj,
                                  bounds_r[zi], bounds_r[zi + 1],
                                  bounds_c[zj], bounds_c[zj + 1]))
    hist = [0] * (hist_hi - hist_lo + 1)    # inclusive at both ends

    visit = order if order is not None else range(len(zones))
    for zindex in visit:
        z = zones[zindex]
        r0, r1, c0, c1 = z.r0, z.r1, z.c0, z.c1
        for r in range(r0, r1):
            yrow = y[r]
            qrow = qs[r]
            for c in range(c0, c1):
                val = yrow[c]
                q = qrow[c]
                is_black = val <= BLACK_EPS
                w_q = NO_VOTE if is_black else quantise_weight(
                    pixel_weight(val, r, c, h, w, cfg))
                z.add(q, w_q, is_black)
                hist[q // q_per_bin - hist_lo] += 1

    # ---- global statistics: integer sums over zones => tiling invariant ----
    n_total = sum(z.n for z in zones)
    n_black = sum(z.n_black for z in zones)
    sum_w = sum(z.sum_w for z in zones)
    sum_wq = sum(z.sum_wq for z in zones)
    sum_wq2 = sum(z.sum_wq2 for z in zones)
    sum_q = sum(z.sum_q for z in zones)

    total_hist = sum(hist)
    assert total_hist == n_total, "histogram lost samples"

    if sum_w > 0:
        mean_log = (sum_wq / sum_w) / LOG_SCALE
        mean_source = "zone_weighted"
    elif n_total > n_black:
        # only possible if every non-black sample was clipped out of the vote
        mean_log = (sum_q / n_total) / LOG_SCALE
        mean_source = "unweighted_fallback"
    else:
        # fully pure black frame: park the mean on the log floor
        mean_log = math.log2(BLACK_FLOOR)
        mean_source = "black_floor"

    # weighted log variance about the mean, from the exact integer moments
    if sum_w > 0:
        e_q = sum_wq / sum_w
        var_q = sum_wq2 / sum_w - e_q * e_q
        if var_q < 0.0:
            var_q = 0.0
        sigma_log = math.sqrt(var_q) / LOG_SCALE
    else:
        sigma_log = 0.0

    white = _white_point(hist, hist_lo, cfg, n_total)
    ev_pre = LOG_KEY - mean_log
    if mean_source == "black_floor":
        # Nothing measurable in the frame.  Applying the customary
        # log2(KEY) - log2(BLACK_FLOOR) boost would be meaningless (there is
        # no signal to expose for) and would amplify pure noise, so park the
        # exposure at neutral.  The pixel path still goes through the curve,
        # which maps BLACK_FLOOR to a small positive display value.
        ev_pre = 0.0
    ev = _clamp(ev_pre + cfg["ev_bias"], cfg["ev_min"], cfg["ev_max"])

    # ---- per zone contributions (weight shares sum to exactly 1) ----------
    zone_dicts = []
    contrib_sum = 0.0
    share_total = 0.0
    for z in zones:
        d = z.as_dict()
        share = (z.sum_w / sum_w) if sum_w > 0 else 0.0
        vote = LOG_KEY - z.mean_log_ev
        d["weight_share"] = share
        d["vote_ev"] = vote
        d["contribution_ev"] = share * vote
        d["black_zone"] = z.n_black == z.n
        contrib_sum += d["contribution_ev"]
        share_total += d["weight_share"]
        zone_dicts.append(d)

    stats = {
        "pixels": n_total,
        "black_pixels": n_black,
        "black_fraction": round(n_black / n_total, 12) if n_total else 0.0,
        "weight_sum": sum_w,
        "weight_sum_q": sum_wq,
        "weight_scale": WEIGHT_SCALE,
        "log_scale": LOG_SCALE,
        "mean_log_ev": round(mean_log, 12),
        "mean_source": mean_source,
        "sigma_log_ev": round(sigma_log, 12),
        "contrast_stops": round(2.0 * sigma_log, 12),
        "key": KEY,
        "log_key": round(LOG_KEY, 12),
        "white_point": round(white, 12),
        "percentile": cfg["percentile"],
        "metering_ev": round(ev, 12),
        "ev_bias": cfg["ev_bias"],
        "ev_clamped": ev != (ev_pre + cfg["ev_bias"]),
        "zone_count": len(zones),
        "zone_weight_share_sum": share_total,
        "zone_contribution_sum": contrib_sum,
        "border_band": band,
    }
    return stats, zone_dicts, hist


def _white_point(hist, hist_lo, cfg, n_total):
    """Weighted percentile of the log luma histogram (integer counts).

    Scans from the top bin downwards: the percentile of interest is near the
    white end, so this touches only the head of the tail.
    """
    if n_total <= 0:
        return cfg["white_lo"]
    target = int(math.ceil(cfg["percentile"] * n_total))
    target = min(max(target, 1), n_total)
    acc = 0
    bin_index = 0
    for i in range(len(hist) - 1, -1, -1):
        acc += hist[i]
        if acc >= n_total - target + 1:
            bin_index = i
            break
    # bin centre on the quantised log axis (bins are 1/16 EV wide)
    q_mid = (hist_lo + bin_index) * (LOG_SCALE // _SCALE_I) + (LOG_SCALE // 32)
    white = 2.0 ** (q_mid / LOG_SCALE)
    return _clamp(white, cfg["white_lo"], cfg["white_hi"])


def _clamp(v, lo, hi):
    if not math.isfinite(v):
        return lo
    return lo if v < lo else (hi if v > hi else v)
