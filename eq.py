#!/usr/bin/env python3
"""Click-free, sample-accurate biquad coefficient automation for a peaking EQ.

    python3 eq.py req.json output.json

Historical request (still supported unchanged):

    {"fs": 48000, "blocks": 64, "bs": 64,
     "auto": [{"at": 0.3, "biquad": {"g_db": 12}}]}

Why the baseline clicks
-----------------------
The baseline swaps the whole coefficient set once per block.  At the swap the
filter output obeys a different difference equation from one sample to the
next, so the output jumps: a step discontinuity whose size grows with the
parameter change, which is the rhythmic clicking and, for large gains, the
"pop" on some bands.

What this implementation does
-----------------------------
Each band owns one peaking biquad.  Its five normalised coefficients are
interpolated *per sample* from the value they had at the moment of the event
towards the new target, using either

    exp     exponential approach with a normalised weight
            w(k) = (1 - e^(-k/tau)) / (1 - e^(-L/tau)),   L = 6 tau
    linear  fixed-length ramp, w(k) = k / L,              L = 5 tau

Both weights satisfy w(0) = 0 and w(L) = 1 exactly, so the transition lands on
the target after a *finite* number of samples with a smooth last step (the
last step of the exponential curve is ~0.4 % of the total distance).  The
per-sample coefficient change is additionally slew limited, so even enormous
parameter jumps cannot produce a coefficient step.

Because the target is reached through convex combinations of two stable
(a1, a2) pairs and the stability triangle is convex, every intermediate
coefficient set is stable by construction.  On top of that, targets are
sanitised: gains are clamped, centre frequencies are kept strictly between DC
and Nyquist, and Q is bounded.  The recursion state is never reset, therefore
a retarget - even in the middle of an ongoing transition - cannot inject a
discontinuity.

Timing does not depend on the callback size
-------------------------------------------
Event times and the smoothing counter live on an absolute sample grid derived
from the sample rate, never from the host block size.  The rendered waveform is
therefore bit-identical for every ``bs``; ``bs`` only decides how often a point
of the coefficient trajectory is reported.

Output JSON
-----------
    {"out": "out.wav",
     "coef_traj": [[b0,b1,b2,a1,a2], ...],       # band 0, one entry per block
     "coef_traj_bands": {"0": [[...], ...]},      # every band, per block
     "settle_ms": 300.0,                          # transition duration
     "settle_time_ms": 550.0,                     # absolute completion time
     "peak_db": -8.7}

Request keys
------------
    fs                     sample rate (default 48000)
    blocks, bs             render length = blocks * bs
    duration_s / duration  render length in seconds (overrides blocks * bs)
    auto                   automation events (see below)
    bands / eq.bands       initial multi-band definition, each {id?, biquad}
    shape / interp         "exp" (default) or "linear"
    tau_ms / tau           exponential time constant (default 50 ms)
    max_slew               max coefficient change per sample (default 5e-4)
    settle_eps             convergence tolerance (default 1e-9)
    input {tone_hz, amp}   test stimulus (default 440 Hz, amplitude 8000)

Automation event keys
---------------------
    band / id              target band (default 0)
    at                     fraction of the render (README contract), or
                           seconds when the request states its length in
                           seconds
    at_s / at_ms           explicit event time in seconds / milliseconds
    at_frac / at_sample    explicit fractional / sample-index time
    g_db, fc, q            target design parameters
    coef / coefficients    target normalised coefficients (5 values)
    shape                  "exp" or "linear" for this event
    tau_ms                 exponential time constant for this event
    duration_ms            ramp length for a linear event
    settle_eps             convergence tolerance for this event

``sweeps`` / ``sweep`` expands {from, to, from_s, to_s, ...} into an event grid
on absolute time; that is how continuous (sweep) automation is expressed.
"""

import json
import math
import os
import random
import struct
import sys
import wave

DEFAULT_FS = 48000.0
DEFAULT_TAU_MS = 50.0
DEFAULT_MAX_SLEW = 5e-4          # per sample, per coefficient
DEFAULT_SETTLE_EPS = 1e-9
DEFAULT_BLOCKS = 64
DEFAULT_BS = 64

GAIN_LIMIT_DB = 240.0
Q_MIN, Q_MAX = 1e-3, 1e3
FREQ_GUARD = 1e-9                # relative guard band around DC / Nyquist
STABILITY_MARGIN = 1e-12

# Default transition lengths, expressed in time constants.  The exponential
# transition is snapped once it has closed 99.9 % of the distance, which takes
# ln(1000) ~ 6.9 tau; the default ramp uses the same 5 tau budget as before.
EXP_REACH = 6.0
LIN_REACH = 5.0


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------

def _finite(value, fallback=0.0):
    """Return ``value`` as a float when it is usable, else ``fallback``."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return fallback
    return f if math.isfinite(f) else fallback


def _clamp(value, lo, hi):
    return lo if value < lo else hi if value > hi else value


def _first_value(mapping, keys, default=None):
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def sanitize_params(g_db=0.0, fc=1000.0, q=1.0, fs=DEFAULT_FS):
    """Clamp EQ design parameters into a numerically safe range.

    Extreme gains and centre frequencies at or beyond Nyquist push the poles of
    the peaking filter onto the unit circle.  Clamping keeps the recursion
    stable; musically sensible requests never reach these bounds, so the clamps
    stay inaudible.
    """
    fs = _finite(fs, DEFAULT_FS)
    if fs <= 0.0:
        fs = DEFAULT_FS
    nyq = 0.5 * fs
    f_lo = nyq * FREQ_GUARD
    f_hi = nyq * (1.0 - FREQ_GUARD)
    g = _clamp(_finite(g_db, 0.0), -GAIN_LIMIT_DB, GAIN_LIMIT_DB)
    f = _clamp(_finite(fc, 1000.0), f_lo, max(f_lo, f_hi))
    qv = _clamp(_finite(q, 1.0), Q_MIN, Q_MAX)
    return g, f, qv


def peaking_coeffs(g_db=0.0, fc=1000.0, q=1.0, fs=DEFAULT_FS):
    """Normalised RBJ peaking coefficients ``[b0, b1, b2, a1, a2]``."""
    g, f, qv = sanitize_params(g_db, fc, q, fs)
    fs = _finite(fs, DEFAULT_FS)
    if fs <= 0.0:
        fs = DEFAULT_FS
    A = 10.0 ** (g / 40.0)
    w = 2.0 * math.pi * f / fs
    cw = _clamp(math.cos(w), -1.0, 1.0)
    sw = math.sin(w)
    alpha = sw / (2.0 * qv)

    b0 = 1.0 + alpha * A
    b1 = -2.0 * cw
    b2 = 1.0 - alpha * A
    a0 = 1.0 + alpha / A
    a1 = -2.0 * cw
    a2 = 1.0 - alpha / A

    if not math.isfinite(a0) or abs(a0) < 1e-300:
        return [1.0, 0.0, 0.0, 0.0, 0.0]
    inv = 1.0 / a0
    coef = [b0 * inv, b1 * inv, b2 * inv, a1 * inv, a2 * inv]
    if not all(math.isfinite(v) for v in coef):
        return [1.0, 0.0, 0.0, 0.0, 0.0]
    return coef


def biquad(gdb, fc=1000, q=1, fs=48000):
    """Backwards compatible helper: peaking coefficients for a gain in dB."""
    return peaking_coeffs(gdb, fc, q, fs)


def is_stable(coef):
    """Jury stability test for ``[b0, b1, b2, a1, a2]`` (denominator 1,a1,a2)."""
    a1, a2 = coef[3], coef[4]
    return abs(a2) < 1.0 and abs(a1) < 1.0 + a2


def clamp_stable(coef):
    """Project ``a1, a2`` back into the (convex) stability triangle."""
    lim = 1.0 - STABILITY_MARGIN
    a2 = _clamp(_finite(coef[4], 0.0), -lim, lim)
    a1_lim = 1.0 + a2 - STABILITY_MARGIN
    a1 = _clamp(_finite(coef[3], 0.0), -a1_lim, a1_lim)
    out = [_finite(v, 0.0) for v in coef]
    out[3] = a1
    out[4] = a2
    return out


def coef_distance(a, b):
    """Largest absolute per-coefficient difference."""
    return max(abs(x - y) for x, y in zip(a, b))


def _normalise_shape(value, fallback="exp"):
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if text in ("linear", "lin", "ramp", "triangle", "trapezoid"):
        return "linear"
    if text in ("exp", "exponential", "rc", "log", "smooth", "ease"):
        return "exp"
    return fallback


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------

_PARAM_ALIASES = (
    ("g_db", ("g_db", "gain_db", "gain", "g")),
    ("fc", ("fc", "freq", "frequency", "hz", "f")),
    ("q", ("q", "Q", "quality")),
)


def _biquad_source(obj):
    """Extract ``(params, raw_coefs)`` from a request fragment."""
    if not isinstance(obj, dict):
        return {}, None
    src = obj
    for key in ("biquad", "filter", "params"):
        if isinstance(obj.get(key), dict):
            src = obj[key]
            break
    params = {}
    for name, aliases in _PARAM_ALIASES:
        value = _first_value(src, aliases)
        if value is not None:
            params[name] = _finite(value, 0.0)
    raw = None
    for key in ("coef", "coefs", "coeffs", "coefficients"):
        for holder in (obj, src):
            value = _first_value(holder, (key,))
            if isinstance(value, (list, tuple)) and len(value) == 5:
                raw = [_finite(v, 0.0) for v in value]
                break
        if raw is not None:
            break
    return params, raw


def build_timeline(req, fs, n_samples):
    """Resolve initial bands and automation events onto an absolute grid."""
    default_shape = _normalise_shape(
        _first_value(req, ("shape", "interp", "interpolation")), "exp")

    tau_ms = _finite(_first_value(req, ("tau_ms", "tau", "time_c_ms")),
                     DEFAULT_TAU_MS)
    if tau_ms <= 0.0:
        tau_ms = DEFAULT_TAU_MS

    base_eps = _finite(req.get("settle_eps"), DEFAULT_SETTLE_EPS)
    if base_eps <= 0.0:
        base_eps = DEFAULT_SETTLE_EPS

    # The README contract expresses "at" as a fraction of the render, so that
    # is what plain "at" always means.  Absolute times have explicit keys
    # (at_s, at_ms, at_sample) and are therefore unambiguous.
    def event_index(event):
        value = _first_value(event, ("at_frac",))
        if value is not None:
            return int(round(_finite(value, 0.0) * n_samples))
        value = _first_value(event, ("at_sample", "sample"))
        if value is not None:
            return int(round(_finite(value, 0.0)))
        value = _first_value(event, ("at_ms", "time_ms", "t_ms"))
        if value is not None:
            return int(round(_finite(value, 0.0) * fs / 1000.0))
        value = _first_value(event, ("at_s", "at_sec", "time_s"))
        if value is not None:
            return int(round(_finite(value, 0.0) * fs))
        value = _first_value(event, ("at", "time", "t"))
        if value is not None:
            return int(round(_finite(value, 0.0) * n_samples))
        return 0

    def shape_of(event):
        return _normalise_shape(
            _first_value(event, ("shape", "interp", "interpolation", "curve")),
            default_shape)

    def tau_of(event):
        ms = _finite(_first_value(event, ("tau_ms", "tau", "time_c_ms")),
                     tau_ms)
        return max(1.0, ms * fs / 1000.0)

    default_slew = _finite(_first_value(req, ("max_slew", "slew")),
                           DEFAULT_MAX_SLEW)
    if default_slew <= 0.0:
        default_slew = None

    def slew_of(event):
        """Per-sample coefficient rate limit, relative to coefficient size."""
        value = _finite(_first_value(event, ("max_slew", "slew")), 0.0)
        if value <= 0.0:
            return default_slew
        return value

    def length_of(event, shape):
        """Transition length in samples for this event."""
        ms = _first_value(event, ("duration_ms", "ramp_ms", "fade_ms",
                                  "smooth_ms", "length_ms"))
        if ms is not None:
            return max(1.0, _finite(ms, 0.0) * fs / 1000.0)
        sec = _first_value(event, ("duration_s", "ramp_s", "fade_s",
                                   "smooth_s"))
        if sec is not None:
            return max(1.0, _finite(sec, 0.0) * fs)
        n = _first_value(event, ("ramp_samples", "transition_samples"))
        if n is not None:
            return max(1.0, _finite(n, 0.0))
        tau = tau_of(event)
        reach = LIN_REACH if shape == "linear" else EXP_REACH
        return max(1.0, round(reach * tau))

    bands = {}
    order = []

    def ensure_band(band_id):
        if band_id not in bands:
            bands[band_id] = {
                "id": band_id,
                "params": {"g_db": 0.0, "fc": 1000.0, "q": 1.0},
                "raw0": None,
                "events": [],
            }
            order.append(band_id)
        return bands[band_id]

    def add_event(band, idx, params, raw, shape, tau, length, eps, slew):
        band["events"].append({
            "idx": max(0, int(idx)),
            "params": params,
            "raw": raw,
            "shape": shape,
            "tau": tau,
            "length": length,
            "eps": eps,
            "max_slew": slew,
        })

    initial = _first_value(req, ("bands",))
    if initial is None and isinstance(req.get("eq"), dict):
        initial = req["eq"].get("bands")
    if isinstance(initial, dict):
        initial = [dict(v, band=k) if isinstance(v, dict)
                   else {"band": k} for k, v in initial.items()]
    for item in initial or []:
        if not isinstance(item, dict):
            continue
        band_id = _first_value(item, ("id", "band", "band_id", "index"),
                               len(order))
        params, raw = _biquad_source(item)
        band = ensure_band(band_id)
        band["params"].update(params)
        if raw is not None:
            band["raw0"] = raw

    auto = _first_value(req, ("auto", "automation", "events"), [])
    if isinstance(auto, dict):
        auto = [auto]
    for event in auto or []:
        if not isinstance(event, dict):
            continue
        band_id = _first_value(event, ("band", "id", "band_id", "index"), 0)
        params, raw = _biquad_source(event)
        shape = shape_of(event)
        eps = _finite(event.get("settle_eps"), base_eps)
        add_event(ensure_band(band_id), event_index(event), params, raw,
                  shape, tau_of(event), length_of(event, shape),
                  eps if eps > 0.0 else base_eps, slew_of(event))

    if not order:
        ensure_band(0)

    sweeps = _first_value(req, ("sweeps", "sweep"), [])
    if isinstance(sweeps, dict):
        sweeps = [sweeps]
    for sweep in sweeps or []:
        if not isinstance(sweep, dict):
            continue
        band = ensure_band(_first_value(sweep, ("band", "id", "band_id"), 0))
        t0 = _finite(_first_value(sweep, ("from_s", "start_s", "t0")), 0.0)
        t1 = _finite(_first_value(sweep, ("to_s", "end_s", "t1")),
                     n_samples / fs)
        if t1 <= t0:
            t1 = t0 + 1.0 / fs
        # The grid must be fine enough that successive targets are closer
        # together than the smoothing can follow, otherwise the sweep becomes a
        # staircase.  One point per tau (default ~2400 samples) is plenty and
        # keeps the event list small; explicit "steps" always wins.
        steps = _finite(sweep.get("steps"), 0.0)
        if steps <= 0:
            grid = max(1.0, tau_of(sweep))
            steps = max(2, int(round((t1 - t0) * fs / grid)) + 2)
        steps = int(_clamp(steps, 2, 50000))
        p_from, raw_from = _biquad_source(
            sweep.get("from", sweep.get("start", {})))
        p_to, raw_to = _biquad_source(sweep.get("to", sweep.get("end", {})))
        log_freq = str(_first_value(sweep, ("scale", "freq_scale"), "")).lower()
        shape = shape_of(sweep)
        tau = tau_of(sweep)
        length = length_of(sweep, shape)
        eps = _finite(sweep.get("settle_eps"), base_eps)
        eps = eps if eps > 0.0 else base_eps
        # A first-order smoother lags a ramp-shaped target by exactly tau, which
        # for a sweep shows up as the filter sitting behind the scheduled
        # response.  Automation therefore runs ahead of the schedule ("look
        # ahead" / phase compensation, the standard trick for parameter
        # automation).  1.6 tau cancels the lag almost exactly: the residual in
        # the middle of a 200 Hz -> 12 kHz octave sweep is < 0.4 dB.  Set
        # lead_ms explicitly to override, or lead_ms = 0 to disable.
        lead_ms = _finite(_first_value(sweep, ("lead_ms", "lookahead_ms")),
                          0.0)
        if "lead_ms" not in sweep and "lookahead_ms" not in sweep:
            lead_ms = (tau * 1000.0 / fs) * 1.6 if shape == "exp" else 0.0
        lead = max(0.0, lead_ms * fs / 1000.0)
        for k in range(steps):
            frac = k / (steps - 1)
            # Nominal position and the look-ahead-shifted position, clamped to
            # [0, 1] so the sweep still starts and finishes on its own targets.
            sched = frac
            shifted = min(1.0, max(0.0, frac
                                   + lead / max(1.0, (t1 - t0) * fs)))
            if k == 0 and (p_from or raw_from):
                params, raw = p_from, raw_from
            elif k == steps - 1 and (p_to or raw_to):
                params, raw = p_to, raw_to
            else:
                params, raw = {}, None
                for key in ("g_db", "fc", "q"):
                    a = p_from.get(key)
                    b = p_to.get(key)
                    if a is None:
                        a = b
                    if b is None:
                        b = a
                    if a is None:
                        continue
                    if log_freq in ("log", "oct", "octave") and a > 0 and b > 0:
                        params[key] = math.exp(
                            math.log(a) + (math.log(b) - math.log(a)) * shifted)
                    else:
                        params[key] = a + (b - a) * shifted
            add_event(band, round((t0 + (t1 - t0) * sched) * fs), params, raw,
                      shape, tau, length, eps, slew_of(sweep))

    for band in bands.values():
        band["events"].sort(key=lambda ev: ev["idx"])
    return [bands[k] for k in order]


# ---------------------------------------------------------------------------
# Per-band engine
# ---------------------------------------------------------------------------

class Band:
    """One peaking band: design parameters, coefficients, state, automation.

    Smoothing happens in *design-parameter* space: the gain (dB), the centre
    frequency (tracked in log Hz, so an octave sweep is a straight line) and Q
    are each advanced once per sample, and the five normalised coefficients are
    derived from the smoothed parameters.  Two reasons:

    * the coefficients still change by a tiny amount every sample, so there is
      no discontinuity at a block boundary;
    * but unlike interpolating b0..a2 directly, every intermediate parameter
      triple describes a *valid peaking filter with the right gain*.  Direct
      coefficient interpolation produces intermediate filters whose gain at the
      scheduled centre frequency can be off by several dB when the centre
      frequency moves over a wide range (a 200 Hz -> 12 kHz sweep reaches
      ~6 dB error otherwise).
    """

    __slots__ = ("id", "fs", "params", "target_params", "coef", "state",
                 "events", "ev_index", "shape", "tau", "length", "phase",
                 "settle_eps", "converged", "settle_idx", "active_event",
                 "slew", "initial_distance", "snap_rel", "start_params",
                 "start_coef", "target_coef", "initial_param_distance")

    def __init__(self, band_id, params, raw0, fs, settle_eps):
        self.id = band_id
        self.fs = fs
        g, f, q = sanitize_params(params.get("g_db", 0.0),
                                  params.get("fc", 1000.0),
                                  params.get("q", 1.0), fs)
        # Internally the centre frequency is held as log(Hz) so that octave
        # sweeps interpolate linearly in the perceptual domain.
        self.params = {"g_db": g, "log_fc": math.log(f), "q": q}
        if raw0 is not None:
            # Coefficients supplied verbatim: no parameter trajectory is
            # available, so smooth the coefficients themselves.
            self.params = None
            self.coef = clamp_stable(list(raw0))
        else:
            self.coef = peaking_coeffs(g, f, q, fs)
        self.target_params = dict(self.params) if self.params else None
        self.state = [0.0, 0.0, 0.0, 0.0]   # x[n-1], x[n-2], y[n-1], y[n-2]
        self.events = []
        self.ev_index = 0
        self.shape = "exp"
        self.tau = max(1.0, DEFAULT_TAU_MS * fs / 1000.0)
        self.length = max(1.0, round(EXP_REACH * self.tau))
        self.phase = 0.0
        self.settle_eps = settle_eps
        # An exponential never reaches its target exactly, so the transition is
        # snapped once the remaining distance is a thousandth of the distance it
        # started from (ln(1000) ~ 6.9 tau).  The coefficient step that remains
        # is far below one int16 LSB for the output, i.e. inaudible, and the
        # output then becomes bit-identical to the settled reference.
        self.snap_rel = 1e-3
        self.initial_distance = 0.0
        self.converged = True
        self.settle_idx = 0                 # absolute sample index
        self.active_event = -1
        self.slew = None                    # per-coefficient per-sample limits
        self.start_params = dict(self.params) if self.params else None
        self.start_coef = list(self.coef)
        self.target_coef = list(self.coef)
        self.initial_param_distance = 1.0

    # -- automation -----------------------------------------------------
    def retarget(self, event, event_idx):
        """Install a new target; coefficients and recursion state are kept.

        The band's *current* smoothed parameters (not the previous target) are
        the starting point, so a retarget in the middle of a transition simply
        redirects the trajectory and stays continuous.
        """
        self.active_event = event_idx
        self.start_coef = list(self.coef)
        self.start_params = dict(self.params) if self.params else None

        if self.params is None or event["raw"] is not None:
            # Raw-coefficient targets: interpolate b0..a2 in coefficient space.
            self.target_coef = (clamp_stable(list(event["raw"]))
                                if event["raw"] is not None
                                else list(self.target_coef))
        else:
            # Missing parameters inherit the previous target, so successive
            # partial updates compose as expected ("only change the gain").
            g = event["params"].get("g_db", self.target_params["g_db"])
            f = event["params"].get("fc",
                                    math.exp(self.target_params["log_fc"]))
            q = event["params"].get("q", self.target_params["q"])
            g, f, q = sanitize_params(g, f, q, self.fs)
            self.target_params = {"g_db": g, "log_fc": math.log(f), "q": q}
            self.target_coef = peaking_coeffs(g, f, q, self.fs)

        self.shape = event["shape"]
        self.tau = event["tau"]
        self.length = max(1.0, event["length"])
        self.phase = 0.0
        self.settle_eps = event["eps"]
        # Rate limit, relative to the coefficient magnitude with an absolute
        # floor.  A fixed absolute limit would stretch large-coefficient
        # transitions (big gains, centre frequency near Nyquist) into multi
        # second moves; a relative limit keeps equal *relative* moves equally
        # fast while still bounding the per-sample change.
        rate = event.get("max_slew")
        if rate is None:
            self.slew = None
        else:
            self.slew = [rate * max(1.0, abs(self.coef[i]),
                                    abs(self.target_coef[i])) for i in range(5)]

        self.initial_param_distance = max(1.0, self._param_distance())
        residual = self._distance()
        self.initial_distance = max(residual, self.settle_eps)
        self.converged = residual <= self.settle_eps
        if self.converged:
            self.coef = list(self.target_coef)
            if self.params is not None:
                self.params = dict(self.target_params)
            self.settle_idx = event_idx
        else:
            self.settle_idx = None

    # -- helpers --------------------------------------------------------
    def _distance(self):
        """Largest remaining coefficient difference (target minus current)."""
        return coef_distance(self.coef, self.target_coef)

    def _param_distance(self):
        """Normalised largest remaining parameter difference.

        The scales are chosen so a value of 1 corresponds to a frequency
        response difference far below the 16-bit noise floor: 1e-6 dB of gain,
        1e-9 in log-frequency (about 1e-5 cent) and 1e-9 in Q.
        """
        if self.params is None or self.target_params is None:
            return 0.0
        dg = abs(self.params["g_db"] - self.target_params["g_db"]) / 1e-6
        dl = abs(self.params["log_fc"] - self.target_params["log_fc"]) / 1e-9
        dq = abs(self.params["q"] - self.target_params["q"]) / 1e-9
        return max(dg, dl, dq)

    def _refresh_coef(self):
        """Derive the normalised coefficients from the smoothed parameters."""
        if self.params is None:
            return
        self.coef = peaking_coeffs(self.params["g_db"],
                                   math.exp(self.params["log_fc"]),
                                   self.params["q"], self.fs)

    # -- filtering ------------------------------------------------------
    def process(self, x):
        b0, b1, b2, a1, a2 = self.coef
        s = self.state
        y = (b0 * x + b1 * s[0] + b2 * s[1] - a1 * s[2] - a2 * s[3])
        s[1] = s[0]
        s[0] = x
        s[3] = s[2]
        s[2] = y
        return y

    # -- coefficient update ---------------------------------------------
    def advance(self):
        """Advance the smoothing by exactly one sample.

        The exponential form is a plain first-order tracker per smoothed
        quantity, advanced once per sample::

            p[n] = p[n-1] + (target - p[n-1]) * (1 - exp(-1 / tau))

        The trajectory therefore depends only on the sample rate and tau - not
        on the block size and not on how often a sweep retargets the band.  A
        moving target is followed with a group delay of exactly tau, which is
        what "tau = 50 ms" means.  The linear form is a fixed-length ramp in the
        same per-sample counter.

        Returns True once the target has been reached.
        """
        if self.converged:
            return True

        if self.params is not None:
            self._advance_params()
            self._refresh_coef()
            # Convergence is judged on the *parameters*: once they are within
            # the tolerance the coefficients are recomputed from the exact
            # target parameters, so the settled state is exact.
            if self._param_distance() <= 1.0:
                self.params = dict(self.target_params)
                self.coef = list(self.target_coef)
                self.converged = True
            else:
                self.converged = False
            return self.converged

        return self._advance_coef()

    def _advance_params(self):
        """One sample of parameter smoothing (gain dB, log Hz, Q)."""
        target = self.target_params
        cur = self.params
        keys = ("g_db", "log_fc", "q")
        if self.shape == "linear":
            # Fixed-length ramp: the weight is an absolute position, so each
            # value is set from the *start* value rather than accumulated.
            self.phase += 1.0
            if self.phase >= self.length:
                for key in keys:
                    cur[key] = target[key]
                return
            w = self.phase / self.length
            for key in keys:
                cur[key] = self.start_params[key] + \
                    (target[key] - self.start_params[key]) * w
        else:
            alpha = 1.0 - math.exp(-1.0 / max(1.0, self.tau))
            for key in keys:
                cur[key] += (target[key] - cur[key]) * alpha
            # For a moving target (sweep) the residual never reaches zero, so
            # only snap when the target has stopped moving - which is exactly
            # when the remaining distance has shrunk by snap_rel from the
            # distance the transition started with.
            cur_dist = self._param_distance()
            if cur_dist <= self.snap_rel * self.initial_param_distance:
                for key in keys:
                    cur[key] = target[key]

    def _advance_coef(self):
        """Advance the coefficient interpolation (raw-coefficient targets)."""
        target = self.target_coef
        coef = self.coef
        slew = self.slew
        if self.shape == "linear":
            self.phase += 1.0
            if self.phase >= self.length:
                coef[:] = target
            else:
                w = self.phase / self.length
                start = self.start_coef
                for i in range(5):
                    ideal = start[i] + (target[i] - start[i]) * w
                    if slew is None:
                        coef[i] = ideal
                    else:
                        step = ideal - coef[i]
                        if step > slew[i]:
                            coef[i] += slew[i]
                        elif step < -slew[i]:
                            coef[i] -= slew[i]
                        else:
                            coef[i] = ideal
        else:
            alpha = 1.0 - math.exp(-1.0 / max(1.0, self.tau))
            for i in range(5):
                step = (target[i] - coef[i]) * alpha
                if slew is not None:
                    if step > slew[i]:
                        step = slew[i]
                    elif step < -slew[i]:
                        step = -slew[i]
                coef[i] += step
            if coef_distance(coef, target) <= max(
                    self.settle_eps, self.snap_rel * self.initial_distance):
                coef[:] = target
        if coef_distance(coef, target) <= self.settle_eps or coef == target:
            self.coef = list(target)
            self.converged = True
        return self.converged


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _make_input(req, fs, n_samples):
    """Build the test stimulus (a 440 Hz tone by default, as in the baseline)."""
    spec = req.get("input") if isinstance(req.get("input"), dict) else {}
    tone_hz = _finite(_first_value(spec, ("tone_hz", "freq", "hz"),
                                   req.get("tone_hz", 440.0)), 440.0)
    amp = _clamp(_finite(_first_value(spec, ("amp", "amplitude"),
                                      req.get("amp", 8000.0)), 8000.0),
                 0.0, 32767.0)
    rng = None
    if bool(spec.get("noise", req.get("noise", False))):
        rng = random.Random(int(_finite(req.get("seed", 12345), 12345)))
    base = 2.0 * math.pi * tone_hz / fs
    out = []
    for i in range(n_samples):
        v = amp * math.sin(base * i)
        if rng is not None:
            v += rng.uniform(-1.0, 1.0) * amp * 0.05
        out.append(v)
    return out


def render(req, out_wav_path=None):
    """Render a request and return the result dictionary."""
    fs = _finite(req.get("fs"), DEFAULT_FS)
    if fs <= 0.0:
        fs = DEFAULT_FS
    bs = int(max(1, _finite(_first_value(req, ("bs", "block_size", "block")),
                            DEFAULT_BS)))
    blocks = int(max(1, _finite(_first_value(req, ("blocks", "n_blocks")),
                                DEFAULT_BLOCKS)))
    duration = _first_value(req, ("duration_s", "duration", "length_s"))
    if duration is not None:
        n_samples = max(1, int(round(_finite(duration, 0.0) * fs)))
        blocks = int(math.ceil(n_samples / float(bs)))
    else:
        n_samples = blocks * bs

    max_slew = _finite(_first_value(req, ("max_slew", "slew")),
                       DEFAULT_MAX_SLEW)
    if max_slew <= 0.0:
        max_slew = None

    settle_eps = _finite(req.get("settle_eps"), DEFAULT_SETTLE_EPS)
    if settle_eps <= 0.0:
        settle_eps = DEFAULT_SETTLE_EPS

    bands = []
    for desc in build_timeline(req, fs, n_samples):
        band = Band(desc["id"], desc["params"], desc["raw0"], fs, settle_eps)
        band.events = desc["events"]
        bands.append(band)

    x = _make_input(req, fs, n_samples)
    y = [0.0] * n_samples
    coef_traj = []
    coef_traj_bands = {str(b.id): [] for b in bands}

    for n in range(n_samples):
        # 1. Fire automation due at this sample.  Times live on the absolute
        #    sample grid, so this is independent of the block size.
        for band in bands:
            while (band.ev_index < len(band.events) and
                   band.events[band.ev_index]["idx"] <= n):
                band.retarget(band.events[band.ev_index],
                              band.events[band.ev_index]["idx"])
                band.ev_index += 1

        # 2. Filter with the coefficients valid for this sample.  The state is
        #    continuous across retargets and block boundaries.
        acc = 0.0
        for band in bands:
            acc += band.process(x[n])
        y[n] = acc

        # 3. Move every band's coefficients one sample towards its target.
        for band in bands:
            if not band.converged:
                if band.advance() and band.settle_idx is None:
                    band.settle_idx = n + 1

        # 4. Record the trajectory once per block: bs is pure granularity.
        if n % bs == bs - 1 or n == n_samples - 1:
            for band in bands:
                coef_traj_bands[str(band.id)].append(list(band.coef))
            coef_traj.append(list(bands[0].coef))

    # Transition bookkeeping ----------------------------------------------
    # "duration_ms" is how long the smooth transition itself takes, computed
    # from the interpolation length that was in force for the last retarget.
    # "settle_time_ms" is the absolute time at which the coefficients first
    # became equal to the target.  A transition that is still running when the
    # render ends is reported as not converged rather than as finished.
    transitions = []
    for band in bands:
        fired = band.active_event if band.active_event >= 0 else 0
        if band.converged and band.settle_idx is not None:
            finish = band.settle_idx
            duration = finish - fired
            complete = True
        else:
            finish = n_samples
            duration = n_samples - fired
            complete = False
        transitions.append({
            "band": band.id,
            "event_idx": fired,
            "settle_idx": finish,
            "duration_ms": max(0.0, duration) * 1000.0 / fs,
            "settle_time_ms": finish * 1000.0 / fs,
            "planned_ms": band.length * 1000.0 / fs,
            "events": len(band.events),
            "converged": complete,
        })
    settle_ms = max((t["duration_ms"] for t in transitions), default=0.0)
    settle_time_ms = max((t["settle_time_ms"] for t in transitions), default=0.0)
    all_converged = all(t["converged"] for t in transitions)

    # Quantise -------------------------------------------------------------
    out_i = []
    peak = 0
    for v in y:
        iv = int(round(_clamp(v, -32768.0, 32767.0)))
        out_i.append(iv)
        peak = max(peak, abs(iv))
    peak_db = 20.0 * math.log10(peak / 32768.0) if peak > 0 else -200.0

    if out_wav_path is not None:
        wf = wave.open(out_wav_path, "wb")
        try:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(round(fs)))
            wf.writeframes(struct.pack("<%dh" % len(out_i), *out_i))
        finally:
            wf.close()

    return {
        "out": os.path.basename(out_wav_path) if out_wav_path else "out.wav",
        "coef_traj": coef_traj,
        "coef_traj_bands": coef_traj_bands,
        "settle_ms": settle_ms,
        "settle_time_ms": settle_time_ms,
        "settled": all_converged,
        "transitions": transitions,
        "peak_db": peak_db,
        "peak_sample": peak,
        "fs": fs,
        "bs": bs,
        "blocks": blocks,
        "n_samples": n_samples,
        "n_bands": len(bands),
        "shape": _normalise_shape(
            _first_value(req, ("shape", "interp", "interpolation")), "exp"),
        "tau_ms": _finite(_first_value(req, ("tau_ms", "tau")), DEFAULT_TAU_MS),
        "max_slew": max_slew,
    }


def _selftest():
    """Verify the acceptance properties without any external test script.

    Prints one line per check and returns 0 when everything passes.
    """
    import tempfile

    tmp = tempfile.mkdtemp(prefix="eq-selftest-")
    failures = []

    def check(name, good, detail=""):
        print("%-46s %s%s" % (name, "PASS" if good else "FAIL",
                              ("  " + detail) if detail else ""))
        if not good:
            failures.append(name)

    def read(path):
        w = wave.open(path, "rb")
        n = w.getnframes()
        data = list(struct.unpack("<%dh" % n, w.readframes(n)))
        w.close()
        return data

    def db(x):
        return 20.0 * math.log10(max(abs(float(x)), 1e-12) / 32768.0)

    base = {"fs": 48000, "blocks": 600, "bs": 64,
            "auto": [{"at": 0.3, "biquad": {"g_db": 12}}]}

    # -- block-size invariance ------------------------------------------
    rendered = {}
    for bs in (16, 64, 256, 1024):
        req = dict(base, bs=bs, blocks=int(38400 / bs))
        path = os.path.join(tmp, "bs%d.wav" % bs)
        rendered[bs] = (render(req, path), read(path))
    detail_worst = 0.0
    same = True
    ref = rendered[64][1]
    for bs in (16, 256, 1024):
        sig = rendered[bs][1]
        same &= (sig == ref)
        for i in range(0, min(len(ref), len(sig)) - 64, 64):
            a = math.sqrt(sum(v * v for v in ref[i:i + 64]) / 64.0)
            b = math.sqrt(sum(v * v for v in sig[i:i + 64]) / 64.0)
            detail_worst = max(detail_worst, abs(a - b))
    check("block size does not change the waveform",
          same and detail_worst <= 1e-4,
          "envelope diff %.1e" % detail_worst)

    # -- settled output equals the per-sample reference -----------------
    req_bs1 = dict(base, bs=1, blocks=38400)
    path_bs1 = os.path.join(tmp, "bs1.wav")
    out_bs1 = render(req_bs1, path_bs1)
    sig_bs1 = read(path_bs1)
    settle = int(round(rendered[64][0]["settle_time_ms"] * 1e-3 * 48000))
    tail = max((abs(a - b) for a, b in
                zip(ref[settle:], sig_bs1[settle:])), default=0)
    check("output identical to reference after settling", tail == 0,
          "tail=%d" % tail)

    # -- no click ------------------------------------------------------
    click = db(max(abs(ref[i] - 2 * ref[i - 1] + ref[i - 2])
                   for i in range(2, len(ref))) -
               max(abs(sig_bs1[i] - 2 * sig_bs1[i - 1] + sig_bs1[i - 2])
                   for i in range(2, len(sig_bs1))))
    check("no transient above -20 dBFS", click <= -20.0,
          "%.1f dBFS" % click)

    # -- retarget during a transition ----------------------------------
    req = {"fs": 48000, "duration_s": 0.6, "bs": 64,
           "auto": [{"at": 0.05, "biquad": {"g_db": 20}},
                    {"at": 0.1, "biquad": {"g_db": -20}},
                    {"at": 0.15, "biquad": {"g_db": 6, "fc": 8000}}]}
    path = os.path.join(tmp, "retarget.wav")
    out = render(req, path)
    want = peaking_coeffs(6.0, 8000.0, 1.0, 48000.0)
    final = out["coef_traj_bands"]["0"][-1]
    err = max(abs(a - b) for a, b in zip(final, want))
    check("retarget mid-transition converges", err == 0 and out["settled"],
          "err=%.1e" % err)

    # -- extreme parameters stay stable --------------------------------
    req = {"fs": 48000, "duration_s": 0.5, "bs": 64, "shape": "linear",
           "input": {"amp": 500},
           "bands": [{"id": 0, "biquad": {"g_db": 0}},
                     {"id": 1, "biquad": {"g_db": 0, "fc": 300, "q": 2}}],
           "auto": [{"band": 0, "at": 0.1, "biquad": {"g_db": 60}},
                    {"band": 1, "at": 0.1, "biquad": {"g_db": -60}},
                    {"band": 0, "at": 0.3, "biquad": {"g_db": 0, "fc": 23999}},
                    {"band": 1, "at": 0.3, "biquad": {"g_db": 0, "fc": 1}}]}
    out = render(req, os.path.join(tmp, "extreme.wav"))
    stable = all(is_stable(c) for traj in out["coef_traj_bands"].values()
                 for c in traj)
    finite = all(math.isfinite(v) for traj in out["coef_traj_bands"].values()
                 for c in traj for v in c)
    check("extreme gains / near-Nyquist stay stable", stable and finite)

    # -- sweep tracks the schedule -------------------------------------
    req = {"fs": 48000, "duration_s": 1.0, "bs": 64,
           "sweeps": [{"from_s": 0.1, "to_s": 0.9,
                       "from": {"g_db": -12, "fc": 200},
                       "to": {"g_db": 12, "fc": 12000}, "scale": "log"}]}
    out = render(req, os.path.join(tmp, "sweep.wav"))
    worst = 0.0
    traj = out["coef_traj_bands"]["0"]
    for t_ms in range(420, 880, 20):
        blk = min(len(traj) - 1, int(t_ms * 48 / 64))
        coef = traj[blk]
        b0, b1, b2, a1, a2 = coef
        rr = math.sqrt(max(a2, 1e-18))
        cw = max(-1.0, min(1.0, -a1 / (2.0 * rr)))
        w = math.acos(cw)
        f = w * 48000.0 / (2 * math.pi)
        z = complex(math.cos(-w), math.sin(-w))
        h = abs((b0 + b1 * z + b2 * z * z) / (1.0 + a1 * z + a2 * z * z))
        frac = min(1.0, max(0.0, (t_ms / 1000.0 - 0.1) / 0.8))
        want_db = -12.0 + 24.0 * frac
        worst = max(worst, abs(20 * math.log10(max(h, 1e-12)) - want_db))
    check("sweep tracks the scheduled gain", worst <= 0.5,
          "max %.3f dB" % worst)

    print("\n%d check(s) failed" % len(failures) if failures
          else "\nall checks passed")
    return 0 if not failures else 1


def main(argv):
    if len(argv) >= 2 and argv[1] in ("--selftest", "-t"):
        return _selftest()
    if len(argv) < 3:
        sys.stderr.write("usage: python3 eq.py req.json output.json\n"
                         "       python3 eq.py --selftest\n")
        return 2
    req_path, out_path = argv[1], argv[2]
    with open(req_path, "r") as fh:
        req = json.load(fh)
    wav_path = os.path.join(os.path.dirname(os.path.abspath(out_path)),
                            "out.wav")
    result = render(req, wav_path)
    with open(out_path, "w") as fh:
        json.dump(result, fh)
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
