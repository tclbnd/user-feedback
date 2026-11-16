#!/usr/bin/env python3
"""Pitch-shift resynthesis from tracked sine partials.

Everything here is expressed in terms of the *absolute* sample index.
Without an automation envelope a partial's oscillator is a pure function of
time, ``cos(2*pi*(ratio*freq)*(i/sr) + phase)``, so rendering one long block
and rendering several chunks give bit-identical samples.  With an envelope,
phase is integrated on the sample clock from absolute time zero, and the
integration can be resumed from a checkpointed phase without replaying
earlier samples; the result is still identical to continuous playback.
"""
import bisect
import hashlib
import json
import math
import sys


def fade_gain(i, frames, fade):
    """Half-hann edge gain at absolute sample index ``i``.

    ``i < fade`` rises as ``0.5 - 0.5*cos(pi*(i+1)/fade)``; ``i >= frames -
    fade`` falls as ``0.5 - 0.5*cos(pi*(frames-i)/fade)``; elsewhere unity.
    """
    if fade <= 0:
        return 1.0
    if i < fade:
        return 0.5 - 0.5 * math.cos(math.pi * (i + 1) / fade)
    if i >= frames - fade:
        return 0.5 - 0.5 * math.cos(math.pi * (frames - i) / fade)
    return 1.0


def partial_sample(partial, ratio, sr, i):
    """Value of one partial at absolute sample index ``i``.

    Phase is a pure function of absolute time, so there is no per-partial
    accumulator to reset and no drift when a render is resumed mid-way.
    """
    t = i / sr
    return partial["amp"] * math.cos(
        2 * math.pi * (ratio * partial["freq"]) * t + partial["phase"]
    )


def voice_sample(partials, ratio, sr, i):
    """Sum of all partials at absolute sample index ``i`` (pre-fade)."""
    return sum(partial_sample(p, ratio, sr, i) for p in partials)


def normalize_controls(controls, frames):
    """Return ``[(sample_index, input_order, point)]`` sorted deterministically.

    Controls name *absolute* sample instants.  Fractional times are snapped to
    the nearest sample and clamped to ``[0, frames]`` (a point exactly at
    ``frames`` is an event after the last rendered sample).  Ties at the same
    sample keep input order, so the meaning of overlapping events never
    depends on sort stability.
    """
    out = []
    for order, point in enumerate(controls or []):
        at = float(point.get("at", 0))
        i = int(math.floor(at + 0.5))
        i = max(0, min(int(frames), i))
        out.append((i, order, point))
    out.sort(key=lambda item: (item[0], item[1]))
    return out


def _breakpoints(points, key, initial):
    """Piecewise control values for ``key``, anchored at sample 0.

    Sample 0 is a virtual control carrying the parameter's initial value, so
    linear interpolation ramps smoothly into the first explicit control
    point (and a parameter with no control points stays constant).  When
    several events land on the same sample the later one wins.
    """
    bps = [(0, float(initial))]
    for i, _order, point in points:
        if key not in point:
            continue
        value = float(point[key])
        if bps[-1][0] == i:
            bps[-1] = (i, value)  # later event at the same sample wins
        else:
            bps.append((i, value))
    return bps


def _curve_at(tables, i, mode):
    """Evaluate a breakpoint curve at absolute sample ``i``.

    ``mode="linear"`` interpolates between bracketing breakpoints;
    ``mode="hold"`` keeps the earlier value until the next breakpoint is
    reached.  Evaluation is a pure function of ``i``, so any chunk of the
    render sees exactly the same control value.
    """
    idx, vals = tables
    if i <= idx[0]:
        return vals[0]
    k = bisect.bisect_right(idx, i) - 1
    if k + 1 >= len(idx):
        return vals[k]
    i0, i1 = idx[k], idx[k + 1]
    v0, v1 = vals[k], vals[k + 1]
    if mode == "hold":
        return v0
    return v0 + (v1 - v0) * (i - i0) / (i1 - i0)


def control_tables(ratio, frames, controls):
    """Return ``(ratio_tables, amp_tables, resets)`` for the control envelope.

    ``resets`` maps an absolute sample index to the phase value assigned by
    the last reset event landing on that sample.
    """
    points = normalize_controls(controls, frames)
    ratio_bps = _breakpoints(points, "ratio", ratio)
    amp_bps = _breakpoints(points, "amp", 1.0)
    ratio_tables = ([p[0] for p in ratio_bps], [p[1] for p in ratio_bps])
    amp_tables = ([p[0] for p in amp_bps], [p[1] for p in amp_bps])
    resets = {}
    for i, _order, point in points:
        if point.get("reset"):
            resets[i] = float(point.get("phase", 0.0))  # later reset wins
    return ratio_tables, amp_tables, resets


def integrate_range(partials, sr, ratio_tables, amp_tables, resets, interp,
                    start, stop, phases):
    """Advance the oscillators over absolute samples ``start``..``stop-1``.

    ``phases`` holds the phase each partial will use for sample ``start``
    (``None`` means start from the partial's initial phase).  Returns
    ``(amps, thetas, end_phases)`` where ``end_phases`` is the phase that
    sample ``stop`` would use.  Phase is advanced on the sample clock after
    each emitted sample, so a control change between samples never produces
    a phase jump; a reset event overrides the running phase at that sample.
    """
    amps, thetas = [], []
    steps = [2.0 * math.pi * float(p["freq"]) / float(sr) for p in partials]
    cur = [float(p["phase"]) if phases is None else float(phases[k])
           for k, p in enumerate(partials)]
    for i in range(start, stop):
        r = _curve_at(ratio_tables, i, interp)
        g = _curve_at(amp_tables, i, interp)
        reset = resets.get(i)
        if reset is not None:
            for k, p in enumerate(partials):
                cur[k] = reset + float(p["phase"])
        amps.append([float(p["amp"]) * g for p in partials])
        thetas.append(list(cur))
        for k in range(len(partials)):
            cur[k] += steps[k] * r
    return amps, thetas, cur


def build_curves(partials, ratio, sr, frames, controls=None, interp="linear"):
    """Per-partial amplitude and phase for every absolute sample.

    ``ratio`` (frequency multiplier) and the global ``amp`` gain are
    interpolated per the requested ``mode`` at absolute sample instants.
    Phase is integrated on the sample clock, ``theta[i+1] = theta[i] +
    2*pi*ratio(i)*freq/sr``, so a frequency change never introduces a phase
    discontinuity.  A point carrying ``"reset": true`` restarts the phase at
    that exact sample to ``point["phase"] + partial["phase"]``; points without
    ``reset`` leave the running phase untouched.
    """
    ratio_tables, amp_tables, resets = control_tables(ratio, frames, controls)
    amps, thetas, _ = integrate_range(partials, sr, ratio_tables, amp_tables,
                                      resets, interp, 0, frames, None)
    return [{"amp": [row[k] for row in amps],
             "theta": [row[k] for row in thetas]}
            for k in range(len(partials))]


def render_span(partials, ratio, sr, frames, start, stop, controls=None,
                interp="linear", phases=None):
    """Render a slice and return ``(samples, phases_for_sample_stop)``.

    ``phases_for_sample_stop`` is ``None`` when the render has no control
    envelope (there the oscillator is a pure function of absolute time).
    """
    fade = frames // 4
    if not controls:
        return ([voice_sample(partials, ratio, sr, i)
                 * fade_gain(i, frames, fade) for i in range(start, stop)], None)
    ratio_tables, amp_tables, resets = control_tables(ratio, frames, controls)
    amps, thetas, end_phases = integrate_range(partials, sr, ratio_tables,
                                               amp_tables, resets, interp,
                                               start, stop, phases)
    samples = [
        sum(row[k] * math.cos(theta[k]) for k in range(len(partials)))
        * fade_gain(start + n, frames, fade)
        for n, (row, theta) in enumerate(zip(amps, thetas))
    ]
    return samples, end_phases


def render_range(partials, ratio, sr, frames, start, stop, controls=None,
                 interp="linear", phases=None):
    """Render absolute samples ``start``..``stop-1`` of the full ``frames``.

    Both the fade and the oscillator depend only on absolute sample index and
    the total frame count, so this can be called for abutting chunks (or for
    a single chunk resumed from a checkpoint) and the union is identical to
    ``render`` over the whole range.  DC removal is intentionally *not* done
    here: it needs the mean of the whole faded signal, so it belongs to the
    caller that owns the full buffer.
    """
    return render_span(partials, ratio, sr, frames, start, stop, controls,
                       interp, phases)[0]


def render(partials, ratio, sr, frames, controls=None, interp="linear"):
    """Render ``frames`` samples from absolute sample 0.

    The half-hann edge fade is applied first and the DC component is then
    removed exactly once from the faded signal.
    """
    if frames <= 0:
        return []
    y = render_range(partials, ratio, sr, frames, 0, frames, controls, interp)
    mean = sum(y) / frames
    return [v - mean for v in y]


def note_signature(partials, ratio, sr, frames, controls, interp):
    """Stable fingerprint of everything that shapes the waveform."""
    payload = json.dumps({"partials": partials, "ratio": ratio, "sr": sr,
                          "frames": frames, "controls": controls,
                          "interp": interp}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def checkpoint_state(partials, ratio, sr, frames, next_index, y,
                     controls=None, interp="linear", phases=None):
    """State needed to resume rendering after ``next_index`` samples.

    Everything that determines the waveform is carried here: the immutable
    note description, the absolute control points and interpolation mode, the
    absolute index to resume from, the oscillator phase each partial will use
    for sample ``next_index`` (``phases``), and the already-faded samples
    accumulated so far, which are needed for the single global DC removal.
    All fields are plain JSON values, so the state can be written to disk and
    restored later; ``phases=None`` means the render starts from sample 0 and
    the phases are re-derived from the initial partial phases.
    """
    return {
        "partials": partials,
        "ratio": ratio,
        "sr": sr,
        "frames": frames,
        "controls": controls,
        "interp": interp,
        "signature": note_signature(partials, ratio, sr, frames, controls,
                                    interp),
        "next_index": next_index,
        "phases": phases,
        "faded": y,
    }


def resume(state, stop=None):
    """Continue a checkpointed render up to absolute sample ``stop``.

    Returns ``(samples, new_state)``.  ``samples`` holds the fade-applied
    values for the newly rendered range; the final DC removal happens in
    ``finish`` once ``next_index == frames``.  Oscillator phases continue
    from the checkpointed value, so no control event before ``next_index``
    has to be replayed and no phase is recomputed from scratch.
    """
    frames = state["frames"]
    wanted = note_signature(state["partials"], state["ratio"], state["sr"],
                            frames, state.get("controls"),
                            state.get("interp", "linear"))
    if state.get("signature", wanted) != wanted:
        raise ValueError("checkpoint does not match the note description")
    start = min(state["next_index"], frames)
    stop = frames if stop is None else min(stop, frames)
    stop = max(stop, start)
    controls = state.get("controls")
    interp = state.get("interp", "linear")
    phases = state.get("phases")
    if controls and phases is None and start > 0:
        # A checkpoint without stored phases (e.g. written by an older
        # version) still resumes correctly: re-integrate the prefix on the
        # absolute sample clock, discarding its samples.
        ratio_tables, amp_tables, resets = control_tables(
            state["ratio"], frames, controls)
        _, _, phases = integrate_range(state["partials"], state["sr"],
                                       ratio_tables, amp_tables, resets,
                                       interp, 0, start, None)
    chunk, end_phases = render_span(state["partials"], state["ratio"],
                                    state["sr"], frames, start, stop,
                                    controls, interp, phases)
    new_state = checkpoint_state(state["partials"], state["ratio"],
                                 state["sr"], frames, stop,
                                 state["faded"] + chunk,
                                 controls, interp, end_phases)
    return chunk, new_state


def finish(state):
    """Apply the single global DC removal and return the final samples.

    Refuses to finalise an incomplete render: the mean must be taken over all
    ``frames`` faded samples, otherwise the DC removal would differ from a
    continuous render.
    """
    if state["next_index"] != state["frames"]:
        raise ValueError("cannot finalise: rendered %d of %d samples"
                         % (state["next_index"], state["frames"]))
    y = state["faded"]
    if len(y) != state["frames"]:
        raise ValueError("cannot finalise: checkpoint holds %d faded samples,"
                         " expected %d" % (len(y), state["frames"]))
    if not y:
        return []
    mean = sum(y) / len(y)
    return [v - mean for v in y]


def save_checkpoint(path, state):
    """Persist a resume state as JSON (all fields are plain JSON values)."""
    with open(path, "w") as fh:
        json.dump(state, fh)


def load_checkpoint(path):
    with open(path) as fh:
        return json.load(fh)


def write_output(path, y, partials, ratio):
    peak = max((abs(v) for v in y), default=0.0)
    out = {
        "samples": [round(v, 6) for v in y],
        "peak": round(peak, 6),
        "freqs": [round(ratio * p["freq"], 3) for p in partials],
    }
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1)


def render_blocks(partials, ratio, sr, frames, controls=None, interp="linear",
                  block=0, state_path=None):
    """Render via checkpoints, optionally in fixed-size blocks.

    This is the continuous render expressed as a sequence of resumable
    chunks: with ``block=0`` a single chunk covers the whole note.  The result
    is asserted to be identical to ``render`` so a bad checkpoint can never
    silently change the waveform.  When ``state_path`` is given the final
    checkpoint is written there (``next_index == frames``).
    """
    state = checkpoint_state(partials, ratio, sr, frames, 0, [], controls,
                             interp)
    while state["next_index"] < frames:
        stop = frames if block <= 0 else min(state["next_index"] + block,
                                             frames)
        _, state = resume(state, stop)
        if state_path:
            save_checkpoint(state_path, state)
    y = finish(state)
    reference = render(partials, ratio, sr, frames, controls, interp)
    if y != reference:
        raise ValueError("blocked render diverged from continuous render")
    return y


def main(argv):
    """CLI: ``pitch.py input.json output.json``.

    Optional ``--block N`` renders N samples per checkpointed chunk
    (identical output to a single continuous render) and ``--checkpoint PATH``
    writes the resume state after each chunk.
    """
    args = argv[1:]
    block = 0
    state_path = None
    rest = []
    k = 0
    while k < len(args):
        if args[k] == "--block":
            block = int(args[k + 1])
            k += 2
        elif args[k] == "--checkpoint":
            state_path = args[k + 1]
            k += 2
        else:
            rest.append(args[k])
            k += 1
    if len(rest) != 2:
        raise SystemExit("usage: pitch.py input.json output.json"
                         " [--block N] [--checkpoint PATH]")
    with open(rest[0]) as fh:
        d = json.load(fh)
    partials = d["partials"]
    ratio = d["ratio"]
    sr = d["sr"]
    frames = d["frames"]
    controls = d.get("controls")
    interp = d.get("interp", "linear")
    if interp not in ("linear", "hold"):
        raise SystemExit("interp must be 'linear' or 'hold'")
    if block > 0 or state_path:
        y = render_blocks(partials, ratio, sr, frames, controls, interp,
                          block, state_path)
    else:
        y = render(partials, ratio, sr, frames, controls, interp)
    write_output(rest[1], y, partials, ratio)


if __name__ == "__main__":
    main(sys.argv)
