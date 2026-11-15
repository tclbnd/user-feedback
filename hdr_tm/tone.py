"""Target tone curve and the capped shadow lift applied to the base layer.

COORDINATE SYSTEM
-----------------
Exposure compensation puts the metered mean log luma on middle grey, so the
exposure normalised coordinate of a pixel is

    e(x) = log2(Y(x)) + ev - log2(KEY)          [EV, 0 == middle grey]

`e` is relative to middle grey: e = 0 at the metered mean, e = -4 is four
stops below the scene key.  Mixing this up with an absolute log scale is the
classic "everything is a shadow" bug, so the whole stage speaks `e`.

DISPLAY CURVE
-------------
    T(u) = u (u + A) / (u (u + B) + C),      u = 2**e >= 0

T(0) = 0, strictly increasing on [0, inf), T(u) -> 1 as u -> inf while
T(u) < 1 for every finite u.  Highlights roll off asymptotically along their
whole numeric range instead of piling up at 1.0 (the baseline defect), and no
sample can reach a hard clip.

SHADOW LIFT (the "local gain" of requirement 2)
----------------------------------------------
    lift(e) = clamp(lift_k * (pivot - e), 0, log2(max_dark_gain))

max_dark_gain <= 3 therefore caps the linear gain 2**lift at 3x.  The lift is
computed from the *base layer only* and added in the log domain, so the detail
layer is untouched (L_out = B + lift(B) + D).  lift is monotone in the smooth
base B, so it introduces no new edge overshoot and cannot halo; guided.py
carries the proof for the base layer itself.

`predict_display` is the single source of truth for the target value of a
linear luma; the tests check the pipeline against it.
"""
import math

A = 0.5
B = 1.0
C = 0.25

U_MIN = 2.0 ** -60.0        # curve input floor, keeps T strictly positive
U_MAX = 2.0 ** 60.0


def target(u):
    """Display curve on the exposure normalised coordinate u >= 0."""
    if u != u:                      # NaN guard
        return 0.0
    if u <= 0.0:
        return 0.0
    if u >= U_MAX:
        return 1.0
    return (u * (u + A)) / (u * (u + B) + C)


def shadow_lift(e, cfg, cap_ev):
    """Capped shadow lift in EV for the base coordinate e (always >= 0)."""
    pivot = cfg["pivot"]
    if e >= pivot:
        return 0.0
    lift = cfg["lift_k"] * (pivot - e)
    if lift > cap_ev:
        lift = cap_ev
    return lift if lift > 0.0 else 0.0


def render(base_e, detail_e, h, w, cfg, stats):
    """Display values in [0, 1] for the whole frame.

    Never returns NaN, inf or a negative value: the curve input is floored at
    U_MIN, `target` is bounded in [0, 1], and the result is clamped.
    """
    ev = stats["metering_ev"]
    log_key = stats["log_key"]
    cap_ev = stats["lift_cap_ev"]
    dgain = cfg["detail_gain"]
    gamma = cfg["gamma"]
    pivot = cfg["pivot"]
    lift_k = cfg["lift_k"]
    n = h * w
    out = [0.0] * n
    for i in range(n):
        e = base_e[i] + ev - log_key
        if e < pivot:
            lift = lift_k * (pivot - e)
            e_tone = e + (lift if lift < cap_ev else cap_ev)
        else:
            e_tone = e
        v = target(pow2(e_tone + detail_e[i] * dgain))
        if gamma != 1.0 and v > 0.0:
            v = v ** (1.0 / gamma)
        if v != v or v <= 0.0:
            out[i] = 0.0
        elif v >= 1.0:
            out[i] = 1.0
        else:
            out[i] = v
    return out


def predict_display(y, cfg, stats):
    """Target display value of a linear luma `y` (detail == 0).

    Reference used by the acceptance tests:

        e    = log2(y) + ev - log2(KEY)
        lift = clamp(lift_k * (pivot - e), 0, log2(max_dark_gain))
        out  = T(2**(e + lift)) ** (1 / gamma)
    """
    if y is None or y != y:
        return 0.0
    if y <= 0.0:
        return 0.0
    e = math.log2(y) + stats["metering_ev"] - stats["log_key"]
    e_tone = e + shadow_lift(e, cfg, stats["lift_cap_ev"])
    v = target(pow2(e_tone))
    if cfg["gamma"] != 1.0 and v > 0.0:
        v = v ** (1.0 / cfg["gamma"])
    return 0.0 if v <= 0.0 else (1.0 if v >= 1.0 else v)


def pow2(v):
    """2**v with hard saturation so no stage can make inf or 0 by accident."""
    if v != v:
        return 1.0
    if v >= 60.0:
        return U_MAX
    if v <= -60.0:
        return U_MIN
    return 2.0 ** v


def curve_solve(y):
    """Inverse of T: the u mapping to a display value y in (0, 1)."""
    if not (0.0 < y < 1.0):
        return 0.0 if y <= 0.0 else U_MAX
    # y (u^2 + B u + C) = u^2 + A u
    # (1-y) u^2 + (A - yB) u - yC = 0
    a = 1.0 - y
    b = A - y * B
    c = -y * C
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        disc = 0.0
    return (-b + math.sqrt(disc)) / (2.0 * a)
