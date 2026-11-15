#!/usr/bin/env python3
"""Acceptance harness for the deterministic particle pipeline.

Checks
------
A. single-thread reference vs 1/4/8 threads * several chunkings and two
   adversarially scrambled dispatch orders, 300 frames each: per-frame,
   per-component equality of positions and velocities, plus per-frame state
   and render digests.
B. analytic premultiplied-alpha compositing of an overlapping particle
   string: max RGBA channel error <= 2e-6 (closed form, ``math.fsum``), blend
   invariance under draw batches, and an end-to-end check that the emitted
   ``blend`` equals the closed form over the emitted ``alpha``/``color``/``order``.
C. injected equal-depth ties (cycled depth layers, quantised sort keys, a
   fully tied stack and a fully coincident stack): the sort stays a total
   order (depth descending, id ascending) and is reproduced across scheduling.
D. negative controls: 1-ulp state perturbation, a changed draw order, a
   different seed, a dropped frame must all be detected; a shuffled or
   straight-alpha composite must exceed the tolerance.

Exit status 0 only when every check passes.
"""

import json
import math
import random
import sys

import sim


TOL = 2e-6


def _fail(msg):
    print("FAIL: " + msg)
    return 1


# --------------------------------------------------------------------------- #
# A: single-thread reference vs parallel / re-chunked runs
# --------------------------------------------------------------------------- #
def diff_frames(ref_out, ref_states, out, frames, steps):
    """List every difference of ``out``/``frames`` against the reference."""
    bad = []
    if len(frames) != steps or len(ref_states) != steps:
        return ["frame count mismatch (%d vs %d)" % (len(frames),
                                                     len(ref_states))]
    for fi, (rf, cf) in enumerate(zip(ref_states, frames)):
        for lane in range(6):
            a, b = rf[lane], cf[lane]
            if a != b:
                bad.append("frame %d lane %d differs (max |delta| = %r)"
                           % (fi + 1, lane,
                              max((abs(x - y) for x, y in zip(a, b)),
                                  default=0)))
                break
        if bad:
            break
    for key in ("digest", "render_digest", "final_digest", "traj", "vel",
                "order", "depth", "blend"):
        if out[key] != ref_out[key]:
            bad.append("'%s' differs from the reference" % key)
    for fa, fb in zip(ref_out["frames"], out["frames"]):
        for key in ("state_digest", "render_digest", "sort_keys", "order",
                    "blend", "counts"):
            if fa[key] != fb[key]:
                bad.append("frame %d '%s' differs" % (fa["step"], key))
                break
        if bad:
            break
    return bad


def compare_frames(tag, ref_out, ref_states, out, frames, steps):
    """Bit-exact comparison of one run against the single-thread reference.

    Returns ``(rc, max_abs_component_delta)``.
    """
    bad = diff_frames(ref_out, ref_states, out, frames, steps)
    if bad:
        return _fail("%s: %s" % (tag, bad[0])), None
    maxd = 0
    for rf, cf in zip(ref_states, frames):
        for lane in range(6):
            for x, y in zip(rf[lane], cf[lane]):
                d = abs(x - y)
                if d > maxd:
                    maxd = d
    return 0, maxd


def check_schedule_invariance(base_req, verbose=True):
    n = int(base_req.get("n", 64))
    steps = int(base_req.get("steps", 300))
    ref_req = dict(base_req, threads=1, chunk=max(1, n))
    ref_out, ref_states = sim.simulate(ref_req)

    configs = [(1, max(1, n // 2), 1, False),
               (4, 16, 16, False),
               (4, 1, 1, False),
               (8, 4, 3, False),
               (8, 1, 64, False),
               (2, 7, 5, False),
               # adversarial schedules: scrambled worker dispatch order
               (1, 1, 2, True),
               (4, 16, 16, True),
               (8, 3, 7, True),
               (2, 5, 2, True)]
    for threads, chunk, draw_batch, scramble in configs:
        req = dict(base_req, threads=threads, chunk=chunk, draw_batch=draw_batch,
                   scramble=scramble)
        out, frames = sim.simulate(req)
        tag = "threads=%d chunk=%-2d draw=%-2d%s" % (
            threads, chunk, draw_batch, " scramble" if scramble else "        ")
        rc, maxd = compare_frames(tag, ref_out, ref_states, out, frames, steps)
        if rc:
            return rc
        if verbose:
            print("  ok  %-40s frames=%-4d digest=%s  max|dstate|=%d"
                  % (tag, steps, out["digest"][:16], maxd))
    return 0


# --------------------------------------------------------------------------- #
# B: analytic premultiplied-alpha compositing
# --------------------------------------------------------------------------- #
def analytic(colors):
    """Closed-form 'over' chain for a far -> near premultiplied stack.

        C = sum_k s_k * prod_{m>k} (1 - a_m)
        A = 1 - prod_k (1 - a_k)

    The suffix products are formed first, then the leading sum is evaluated
    with ``math.fsum`` (exactly rounded), so the reference is computed to
    double precision and is itself independent of accumulation order.
    """
    n = len(colors)
    keep = [1.0] * (n + 1)
    for k in range(n - 1, -1, -1):
        keep[k] = keep[k + 1] * (1.0 - colors[k][0])
    terms_r, terms_g, terms_b, terms_a = [], [], [], []
    for k, (a, (cr, cg, cb)) in enumerate(colors):
        w = keep[k + 1]
        terms_r.append(cr * a * w)
        terms_g.append(cg * a * w)
        terms_b.append(cb * a * w)
        terms_a.append(a * w)
    return [math.fsum(terms_r), math.fsum(terms_g), math.fsum(terms_b),
            math.fsum(terms_a)]


def check_blend_analytic(seed=11):
    rng = random.Random(seed)
    # an overlapping "particle string": many layers with partial alpha
    layers = [(rng.uniform(0.05, 0.9),
               (rng.uniform(0, 1), rng.uniform(0, 1), rng.uniform(0, 1)))
              for _ in range(40)]
    alpha = [a for a, _ in layers]
    colors = [c for _, c in layers]
    premul = [(c[0] * a, c[1] * a, c[2] * a) for a, c in layers]
    order = list(range(len(layers)))

    expect = analytic(layers)
    got = sim.composite(order, alpha, premul, batch_size=len(layers))
    err = max(abs(g - e) for g, e in zip(got[:4], expect))
    if err > TOL:
        return _fail("analytic blend error %.3e > %.1e" % (err, TOL))
    print("  ok  analytic blend max channel error = %.3e (<= %.1e)"
          % (err, TOL))

    # batch size must not matter
    for batch in (1, 2, 3, 7, 16, 40, 64):
        g = sim.composite(order, alpha, premul, batch_size=batch)
        e = max(abs(x - y) for x, y in zip(g[:4], expect))
        if e > TOL:
            return _fail("batch=%d changes blend (err %.3e)" % (batch, e))
        if g[:4] != got[:4]:
            return _fail("batch=%d produces a different result" % batch)
    print("  ok  blend invariant under draw batches 1,2,3,7,16,40,64"
          " (bit-identical)")
    return 0


# --------------------------------------------------------------------------- #
# C: equal-depth ties
# --------------------------------------------------------------------------- #
def check_ties(base_req, extra):
    """Quantised depth keys must still yield a total, reproducible order."""
    req = dict(base_req)
    req.update(extra)
    runs = []
    for threads, chunk in ((1, 64), (4, 16), (8, 1), (4, 3)):
        out, _ = sim.simulate(dict(req, threads=threads, chunk=chunk))
        runs.append(out)

    ref = runs[0]
    for out in runs[1:]:
        if out["digest"] != ref["digest"]:
            return _fail("tied-depth run diverges across scheduling")
        if out["order"] != ref["order"]:
            return _fail("tied-depth draw order differs across scheduling")
        if [f["sort_keys"] for f in out["frames"]] != \
                [f["sort_keys"] for f in ref["frames"]]:
            return _fail("tied-depth sort keys differ across scheduling")

    tied_frames = 0
    tied_pairs = 0
    for fr in ref["frames"]:
        keys = fr["sort_keys"]
        for (k0, i0), (k1, i1) in zip(keys, keys[1:]):
            if k1 > k0:
                return _fail("sort keys not descending in depth (step %d)"
                             % fr["step"])
            if k0 == k1 and i1 <= i0:
                return _fail("tied depth not broken by ascending id (step %d)"
                             % fr["step"])
        if fr["blend_stats"]["tie_groups"] > 0:
            tied_frames += 1
            tied_pairs += fr["blend_stats"]["tie_pairs"]
    if tied_pairs == 0:
        return _fail("expected depth ties, found none for %s"
                     % json.dumps(extra, sort_keys=True))
    print("  ok  quantised keys %-22s frames with ties=%-3d tied pairs=%-5d "
          "order reproducible" % (json.dumps(extra, sort_keys=True),
                                  tied_frames, tied_pairs))
    return 0


def _stack_particles(count, same_point=False, alpha_step=0.0):
    """An overlapping particle string: identical depth, hence identical keys."""
    parts = []
    for i in range(count):
        if same_point:
            x = y = 32.0
        else:
            x = 16.0 + (i % 8) * 4.0
            y = 16.0 + (i // 8) * 4.0
        a = 0.9 if alpha_step == 0.0 else 0.05 + (i % 9) * alpha_step
        parts.append({"x": x, "y": y, "z": 8.0, "vx": 0.0, "vy": 0.0, "vz": 0.0,
                      "a": a,
                      "color": [((i * 37) % 100) / 100.0,
                                ((i * 61) % 100) / 100.0,
                                ((i * 83) % 100) / 100.0]})
    return parts


def check_tie_stack():
    """Every particle shares one exact depth key -> the whole frame is a tie."""
    parts = _stack_particles(48, alpha_step=0.1)
    req = {"steps": 4, "particles": parts, "radius": 0.0, "force": [0, 0],
           "threads": 1, "chunk": 48, "draw_batch": 4, "n": len(parts)}
    runs = []
    for threads, chunk in ((1, 48), (4, 16), (8, 1), (4, 5)):
        out, _ = sim.simulate(dict(req, threads=threads, chunk=chunk))
        runs.append(out)
    ref = runs[0]
    for out in runs[1:]:
        if out["digest"] != ref["digest"] or out["order"] != ref["order"]:
            return _fail("fully tied stack not reproducible across scheduling")
        if out["blend"] != ref["blend"]:
            return _fail("fully tied stack blend differs across scheduling")
    # 48 equal keys -> order must be strictly ascending particle id
    if ref["order"] != list(range(len(parts))):
        return _fail("fully tied stack order is not ascending id: %r"
                     % (ref["order"][:8],))
    for fr in ref["frames"]:
        if fr["blend_stats"]["tie_groups"] != 1:
            return _fail("fully tied stack should form one tie group, got %d"
                         % fr["blend_stats"]["tie_groups"])
        if fr["blend_stats"]["tie_pairs"] != len(parts) * (len(parts) - 1) // 2:
            return _fail("tied pair count wrong")
        if fr["blend_stats"]["draw_batches"] != 12:
            return _fail("draw batch grouping wrong")

    # analytic reference for the composite of the whole tied stack
    colors = [(p["a"], tuple(p["color"])) for p in parts]
    expect = analytic(colors)
    err = max(abs(g - e) for g, e in zip(ref["blend"], expect))
    if err > TOL:
        return _fail("tied stack analytic blend err %.3e > %.1e" % (err, TOL))
    print("  ok  fully tied stack: 48 equal keys, order=ascending id, "
          "analytic err=%.3e" % err)
    return 0


def check_tie_coincident():
    """Coincident centres: collision pair direction is derived deterministically
    from ids, so the scatter and the ordering stay reproducible."""
    parts = _stack_particles(16, same_point=True, alpha_step=0.05)
    req = {"steps": 40, "particles": parts, "radius": 0.6,
           "collision_restitution": 0.2, "force": [0, 0], "n": len(parts),
           "threads": 1, "chunk": 16}
    runs = []
    for threads, chunk in ((1, 16), (4, 4), (8, 1), (4, 3)):
        out, _ = sim.simulate(dict(req, threads=threads, chunk=chunk))
        runs.append(out)
    ref = runs[0]
    for out in runs[1:]:
        if out["digest"] != ref["digest"]:
            return _fail("coincident particles diverge across scheduling")
        if out["order"] != ref["order"]:
            return _fail("coincident particles order differs across scheduling")
        if out["traj"] != ref["traj"] or out["vel"] != ref["vel"]:
            return _fail("coincident particles final state differs")
    # particles must have separated deterministically (no NaN, still distinct)
    pts = {tuple(p) for p in ref["traj"]}
    if len(pts) < len(parts) - 2:
        return _fail("coincident particles did not separate (%d distinct)"
                     % len(pts))
    print("  ok  coincident stack: %d distinct positions after %d steps, "
          "reproducible" % (len(pts), req["steps"]))
    return 0


def check_blend_end_to_end(base_req):
    """The frame's blend must be reproducible from the emitted alpha/colour/order
    and must match the closed-form analytic composite."""
    out, _ = sim.simulate(dict(base_req, steps=40, threads=4, chunk=16))
    alpha = out["alpha"]
    color = out["color"]
    premul = [(c[0] * a, c[1] * a, c[2] * a) for a, c in zip(alpha, color)]
    order = out["order"]
    layers = [(alpha[i], tuple(color[i])) for i in order]

    expect = analytic(layers)
    got = out["blend"]
    err = max(abs(g - e) for g, e in zip(got, expect))
    if err > TOL:
        return _fail("end-to-end frame blend err %.3e > %.1e" % (err, TOL))

    # the order emitted is exactly the far->near order of the depth keys
    keys = out["frames"][-1]["sort_keys"]
    if [i for _, i in keys] != order:
        return _fail("emitted order does not match the emitted sort keys")
    for c in out["frames"][-1]["blend"]:
        if not (-1e-12 <= c <= 1.0 + 1e-12):
            return _fail("blend channel out of range: %r" % c)
    print("  ok  end-to-end frame blend matches analytic (err=%.3e) over %d "
          "layers" % (err, len(order)))
    return 0


# --------------------------------------------------------------------------- #
# D: negative controls -- the checks must be able to fail
# --------------------------------------------------------------------------- #
def _naive_straight_composite(order, alpha, color):
    """The bug we are guarding against: straight (non-premultiplied) alpha.

    Each layer is composited as ``C = C_src * a + C_dst * (1 - a)`` with
    ``C_src`` the *unpremultiplied* colour, which is the classic wrong
    formulation.  It diverges from the correct premultiplied result, so it
    serves as the control for the analytic check.
    """
    r = g = b = a = 0.0
    for i in order:
        cr, cg, cb = color[i]
        sa = alpha[i]
        ia = 1.0 - sa
        r = cr + r * ia          # missing * sa on the source term
        g = cg + g * ia
        b = cb + b * ia
        a = sa + a * ia
    return [r, g, b, a]


def _shuffled_composite(order, alpha, premul, seed=3):
    """Control: composite the same layers in a shuffled (wrong) order."""
    shuffled = list(order)
    random.Random(seed).shuffle(shuffled)
    return sim.composite(shuffled, alpha, premul, batch_size=4)


def check_negative_controls(base_req):
    """Prove the harness has teeth: perturbed inputs must be rejected.

    Every control is evaluated with ``diff_frames`` directly (no expected
    "FAIL" lines in the report); the run only passes when *all* of these
    perturbations are detected AND the unperturbed runs still compare equal.
    """
    req30 = dict(base_req, steps=30, threads=1, chunk=64)
    ref_out, ref_states = sim.simulate(req30)
    out, states = sim.simulate(dict(base_req, steps=30, threads=8, chunk=1))

    if diff_frames(ref_out, ref_states, out, states, 30):
        return _fail("unperturbed 1-thread and 8-thread runs disagree")

    detected = []

    # (a) one Q30 ulp in a single velocity component of a single frame
    bad = [tuple(list(lane) for lane in fr) for fr in states]
    bad[7][3][5] += 1
    detected.append(bool(diff_frames(ref_out, ref_states, out, bad, 30)))

    # (b) draw order reversed (the committed order must be the depth order)
    bad_out = dict(out, order=list(reversed(out["order"])))
    detected.append(bool(diff_frames(ref_out, ref_states, bad_out, states, 30)))

    # (c) a different seed must not compare equal
    other_out, other_states = sim.simulate(dict(base_req, steps=30, seed=99,
                                                threads=4, chunk=8))
    detected.append(bool(diff_frames(ref_out, ref_states, other_out,
                                     other_states, 30)))

    # (d) dropped draw batch: a frame is missing
    detected.append(bool(diff_frames(ref_out, ref_states, out, states[:-1], 30)))

    if not all(detected):
        return _fail("negative controls not detected: %r" % (detected,))

    # (e) the blend must be order sensitive: a shuffled composite has to
    #     differ from the depth-ordered one, which is why the order is pinned.
    rng = random.Random(5)
    layers = [(rng.uniform(0.1, 0.8),
               (rng.uniform(0, 1), rng.uniform(0, 1), rng.uniform(0, 1)))
              for _ in range(30)]
    alpha = [a for a, _ in layers]
    color = [c for _, c in layers]
    premul = [(c[0] * a, c[1] * a, c[2] * a) for a, c in zip(alpha, color)]
    order = list(range(30))
    expect = analytic(layers)
    proper = sim.composite(order, alpha, premul, batch_size=8)
    if max(abs(x - y) for x, y in zip(proper[:4], expect)) > TOL:
        return _fail("premultiplied blend deviates from the closed form")
    shuffled = _shuffled_composite(order, alpha, premul)
    order_err = max(abs(x - y) for x, y in zip(shuffled[:4], expect))
    if order_err <= TOL:
        return _fail("blend is order insensitive -- depth order not pinned "
                     "(err %.3e)" % order_err)

    # (f) a straight (non-premultiplied) source term must be rejected
    naive = _naive_straight_composite(order, alpha, color)
    naive_err = max(abs(x - y) for x, y in zip(naive, expect))
    if naive_err <= TOL:
        return _fail("straight-alpha blend not distinguished (err %.3e)"
                     % naive_err)

    print("  ok  negative controls: %d/4 perturbations rejected; "
          "shuffled order err=%.3e, straight-alpha err=%.3e"
          % (sum(detected), order_err, naive_err))
    return 0


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    rc = 0
    base = {"n": 64, "steps": 300, "seed": 7, "force": [0, -9.8],
            "bounds": [0, 64], "threads": 4, "chunk": 16}

    print("[A] single-thread reference vs 1/4/8 threads, chunkings, scrambled "
          "dispatch, 300 frames")
    rc |= check_schedule_invariance(base)

    print("[A2] same, with force field + collisions + smaller radius")
    heavy = dict(base, field={"type": "swirl", "strength": 1.5, "scale": 32.0,
                              "center": [32.0, 32.0]},
                 radius=1.2, collision_restitution=0.25, n=96)
    rc |= check_schedule_invariance(heavy)

    print("[B] analytic premultiplied-alpha compositing")
    rc |= check_blend_analytic()
    rc |= check_blend_end_to_end(base)

    print("[C] injected equal-depth ties")
    rc |= check_ties(base, {"dup_layers": 4})
    rc |= check_ties(base, {"depth_quant_bits": 22})
    rc |= check_tie_stack()
    rc |= check_tie_coincident()

    print("[D] negative controls")
    rc |= check_negative_controls(base)

    if rc == 0:
        print("ALL CHECKS PASSED")
    else:
        print("SOME CHECKS FAILED")
    return rc


if __name__ == "__main__":
    sys.exit(main())
