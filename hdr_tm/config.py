"""Request parsing and numeric constants.

Every knob that can influence the output lives here, so the invariance
guarantees documented in metering.py stay easy to audit.
"""
import math

# --- fixed point grids (integer accumulation => partition independent) ------
LOG_SCALE_BITS = 20          # 2**20 grid on log2 luminance  (~1e-6 EV)
WEIGHT_SCALE_BITS = 16       # 2**16 grid on the reliability weight
LOG_SCALE = 1 << LOG_SCALE_BITS
WEIGHT_SCALE = 1 << WEIGHT_SCALE_BITS
LOG_CLAMP_EV = 4096.0        # log2 clamp: keeps every accumulator inside int64

# --- tone / metering constants ---------------------------------------------
KEY = 0.18                   # middle grey used by exposure compensation
LOG_KEY = math.log2(KEY)
BLACK_FLOOR = 2.0 ** -24.0   # log-domain floor applied before log2()
BLACK_EPS = 1.0e-6           # Y <= BLACK_EPS is a "pure black" pixel
LIN_MIN = 1.0e-12            # linear luma floor so log2() never sees 0

DEFAULTS = {
    "tile": 16,
    "zones": None,               # [rows, cols]; overrides tile when given
    "zone_shuffle": 0,           # int seed != 0 -> permuted zone order
    "radius": 3,                 # spatial radius of the base filter
    "sigma_r": 0.35,             # range sigma of the base filter (EV)
    "rel_cut": 1.0e-6,           # drop window weights below max * rel_cut
    "tone_sigma": 3.0,           # EV sigma of the metering mid-tone weight
    "clip_lo": 1.0,              # linear Y where highlight weight starts
    "clip_hi": 4.0,              # linear Y where highlight weight is zero
    "border": None,              # frame border band in px (None -> auto)
    "border_floor": 0.25,        # border weight at the outermost pixel
    "ev_bias": 0.0,              # user exposure compensation added to the meter
    "ev_min": -12.0,
    "ev_max": 24.0,
    "max_dark_gain": 3.0,        # shadow gain cap, linear, in [1, 3]
    "lift_k": 0.8,               # EV of shadow lift per EV below the pivot
    "pivot": 0.0,                # normalised log luma where lift starts
    "detail_gain": 1.0,
    "white_lo": 2.0,
    "white_hi": 4096.0,
    "percentile": 0.995,
    "gamma": 1.0,
    "saturation": 1.0,
}

ALLOWED = set(DEFAULTS) | {"hdr"}


def _num(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("%s must be a number, got %r" % (name, v))
    v = float(v)
    if not math.isfinite(v):
        raise ValueError("%s must be finite, got %r" % (name, v))
    return v


def parse(req):
    """Validate a request dict -> (config, hdr_reference)."""
    if not isinstance(req, dict):
        raise ValueError("request must be a JSON object")
    unknown = set(req) - ALLOWED
    if unknown:
        raise ValueError("unknown request keys: %s" % sorted(unknown))
    if "hdr" not in req:
        raise ValueError("request must contain the key hdr")
    cfg = dict(DEFAULTS)
    for k, v in req.items():
        if k != "hdr":
            cfg[k] = v

    for k in ("tile", "radius", "zone_shuffle"):
        cfg[k] = int(_num(cfg[k], k))
        if cfg[k] < 0:
            raise ValueError("%s must be >= 0" % k)
    if cfg["radius"] < 1:
        cfg["radius"] = 1
    if cfg["tile"] < 1 and cfg["zones"] is None:
        raise ValueError("tile must be >= 1")
    for k in ("sigma_r", "tone_sigma", "clip_lo", "clip_hi", "border_floor",
              "rel_cut", "ev_bias", "ev_min", "ev_max", "max_dark_gain",
              "lift_k", "pivot", "detail_gain", "gamma", "saturation",
              "percentile", "white_lo", "white_hi"):
        cfg[k] = _num(cfg[k], k)

    if cfg["sigma_r"] <= 0.0:
        raise ValueError("sigma_r must be > 0")
    if not (0.0 < cfg["rel_cut"] < 1.0):
        raise ValueError("rel_cut must be in (0, 1)")
    if cfg["tone_sigma"] <= 0.0:
        raise ValueError("tone_sigma must be > 0")
    if not (1.0 <= cfg["max_dark_gain"] <= 3.0):
        raise ValueError("max_dark_gain must be in [1, 3] (shadow gain cap)")
    if cfg["lift_k"] < 0.0:
        raise ValueError("lift_k must be >= 0")
    if cfg["lift_k"] > lift_cap_ev(cfg):
        raise ValueError(
            "lift_k <= log2(max_dark_gain); otherwise the dark gain cap would "
            "be our own limit rather than a genuine constraint")
    if cfg["ev_min"] >= cfg["ev_max"]:
        raise ValueError("ev_min must be < ev_max")
    if not (0.0 < cfg["percentile"] < 1.0):
        raise ValueError("percentile must be in (0, 1)")
    if cfg["clip_hi"] <= cfg["clip_lo"]:
        raise ValueError("clip_hi must be > clip_lo")
    if not (0.0 < cfg["border_floor"] <= 1.0):
        raise ValueError("border_floor must be in (0, 1]")
    if not (0.1 <= cfg["saturation"] <= 3.0):
        raise ValueError("saturation must be in [0.1, 3]")
    if not (0.05 <= cfg["gamma"] <= 5.0):
        raise ValueError("gamma must be in [0.05, 5]")
    if cfg["detail_gain"] < 0.0:
        raise ValueError("detail_gain must be >= 0")
    if cfg["white_lo"] <= 0.0 or cfg["white_hi"] <= cfg["white_lo"]:
        raise ValueError("white_lo/hi must be [>0, >lo]")
    if cfg["border"] is not None:
        cfg["border"] = int(_num(cfg["border"], "border"))
        if cfg["border"] < 0:
            raise ValueError("border must be >= 0")

    zones = cfg.get("zones")
    if zones is not None:
        if not isinstance(zones, (list, tuple)) or len(zones) != 2:
            raise ValueError("zones must be [rows, cols]")
        zones = [int(_num(z, "zones")) for z in zones]
        if zones[0] < 1 or zones[1] < 1:
            raise ValueError("zones must be >= [1, 1]")
        cfg["zones"] = zones
    return cfg, req["hdr"]


def _lift_cap_ev(gain):
    return math.log2(gain)


def lift_cap_ev(cfg):
    """Shadow lift cap in EV: the linear gain never exceeds max_dark_gain."""
    return _lift_cap_ev(cfg["max_dark_gain"])
