#!/usr/bin/env python3
"""Labeled synthetic checks for shots.py.

Every clip is built from 64-bin colour histograms whose histogram
distance is known exactly, so the expected boundary times are known in
advance.  Frame timestamps are deliberately irregular (variable frame
rate) to make sure nothing assumes a constant frame spacing.
"""

import json
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "shots.py")
sys.path.insert(0, ROOT)

import shots  # noqa: E402

TH = {"hard": 0.30, "grad": 0.10, "flash": 120.0, "merge": 200.0}


def solid(bin_index, bins=64):
    h = [0.0] * bins
    h[bin_index] = 1.0
    return h


def mix(target, base, w):
    """Histogram at distance ``w`` from ``base`` towards ``target``."""
    return [(1.0 - w) * y + w * x for x, y in zip(target, base)]


def vfr_times(n, start=0.0, seed=7, gap=(20.0, 60.0)):
    """Irregular frame times: ``gap`` ms apart (variable frame rate)."""
    rng = random.Random(seed)
    t, out = float(start), []
    for _ in range(n):
        out.append(round(t, 3))
        t += rng.uniform(*gap)
    return out


def clip(hist_seq, times):
    assert len(hist_seq) == len(times)
    return [{"t": t, "hist": h} for t, h in zip(times, hist_seq)]


def run_detect(frames, th=TH):
    return shots.detect(frames, th)


def run_cli(frames, th=TH, chunks=None):
    """Run the real CLI, optionally split into several input files."""
    d = tempfile.mkdtemp(prefix="shots-")
    paths = []
    if chunks is None:
        chunks = [frames]
    for idx, part in enumerate(chunks):
        p = os.path.join(d, "in%d.json" % idx)
        doc = {"frames": part}
        if idx == 0:
            doc["thresholds"] = th
        with open(p, "w") as fh:
            json.dump(doc, fh)
        paths.append(p)
    out = os.path.join(d, "out.json")
    subprocess.check_call([sys.executable, SCRIPT] + paths + [out])
    with open(out) as fh:
        return json.load(fh)


# --------------------------------------------------------------- scenes

def scene_hard_cut():
    a, b = solid(0), solid(40)
    h = [a] * 6 + [b] * 6
    t = vfr_times(12)
    return clip(h, t), [{"t": t[6], "type": "hard"}]


def scene_dissolve(n_dissolve=5, seed=11):
    """Linear n-frame dissolve from bin 0 to bin 40.

    Each cross-fade step moves 1/n along the segment; for n=4 that is
    d = 0.25 per pair -- above grad (0.10), below hard (0.30): exactly the
    regime the original code used to miss.

    The run of changing pairs is (i, i+1) .. (j-1, j), so the span runs
    from the last frame of the old shot (i) to the first frame of the new
    one (j), and the boundary is reported at t[j].
    """
    a, b = solid(0), solid(40)
    h = [a] * 5
    for k in range(1, n_dissolve + 1):
        h.append(mix(b, a, k / float(n_dissolve)))
    h += [b] * 5
    t = vfr_times(len(h), seed=seed)
    i = 4                       # last frame of the old shot
    j = i + n_dissolve          # first frame of the new shot
    return clip(h, t), [{"t": t[j], "type": "gradual",
                         "span": [t[i], t[j]], "frames": n_dissolve + 1}]


def scene_flash(n_flash=1, seed=3):
    """Short white flash inside a scene: A A A [W]*n A A A.

    40 fps spacing, so even a 3-frame flash spans ~100 ms and fits inside
    the 120 ms window (at a slower frame rate it would be a real
    transition; see ``scene_flash_outside_window``).
    """
    a, w = solid(0), solid(40)
    h = [a] * 4 + [w] * n_flash + [a] * 4
    t = vfr_times(len(h), seed=seed, gap=(22.0, 28.0))
    return clip(h, t), []


def scene_multi_frame_flash():
    """Ramp up to a brighter frame then straight back down: a transient.

    Unlike a real dissolve the clip returns to the *starting* picture, so
    no boundary may be emitted.  The excursion spans six pairs at 20 ms
    (a ~120 ms flare at 50 fps), i.e. it fits inside the flash window;
    ``scene_flash_outside_window`` covers the opposite case.
    """
    a = solid(0)
    vals = [0.25, 0.5, 0.75, 0.5, 0.25]      # distance from a per frame
    h = [a] * 4 + [mix(solid(40), a, v) for v in vals] + [a] * 4
    t = [20.0 * k for k in range(len(h))]
    return clip(h, t), []


def scene_flash_outside_window():
    """A one-frame spike that comes back too late to be a flash."""
    a, w = solid(0), solid(40)
    h = [a] * 4 + [w] + [a] * 4
    t = [0.0, 40.0, 80.0, 120.0, 160.0, 500.0, 540.0, 580.0, 620.0]
    return clip(h, t), [{"t": t[4], "type": "hard"}, {"t": t[5], "type": "hard"}]


def scene_merge_close_cuts():
    """Two hard cuts 100 ms apart: the second is inside the merge window."""
    a, b, c = solid(0), solid(40), solid(20)
    h = [a] * 4 + [b] * 2 + [c] * 4
    t = [float(x) for x in range(0, 10 * 40, 40)]
    return clip(h, t), [{"t": t[4], "type": "hard"}]


def scene_consecutive_cuts():
    """Three shots, two hard cuts, well clear of the merge window."""
    a, b, c = solid(0), solid(40), solid(20)
    h = [a] * 5 + [b] * 5 + [c] * 5
    t = [1000.0 + 40.0 * k for k in range(len(h))]
    return clip(h, t), [{"t": t[5], "type": "hard"},
                        {"t": t[10], "type": "hard"}]


def scene_threshold_exact():
    """A cut whose distance is exactly `hard` must still count as a cut."""
    a = solid(0)
    b = mix(solid(40), a, TH["hard"])     # d == hard exactly
    h = [a] * 4 + [b] * 4
    t = vfr_times(len(h), seed=23)
    return clip(h, t), [{"t": t[4], "type": "hard"}]


def scene_dissolve_then_cut():
    """A dissolve straight into a hard cut.

    The run of moderate pairs ends at the first frame of shot C; the cut
    to C is a separate boundary but falls inside the merge window, so only
    the gradual survives.  The old code kept extending the run through the
    cut and reported a span ending on C's frame.
    """
    a, b, c = solid(0), solid(40), solid(20)
    h = [a] * 5 + [mix(b, a, k / 4.0) for k in (1, 2, 3, 4)] + [c] * 5
    t = vfr_times(len(h), seed=41)
    i, j = 4, 8
    return clip(h, t), [{"t": t[j], "type": "gradual",
                         "span": [t[i], t[j]], "frames": 5}]


def scene_low_fps_flash_is_a_cut():
    """Two bright frames 250 ms apart at 4 fps exceed the flash window.

    The same shapes at 40 fps are a flash (``scene_flash``); at 4 fps the
    excursion is longer than ``flash`` ms, so it is reported as boundaries
    rather than suppressed.  This is the check that makes the timing
    frame-rate aware rather than frame-count aware.
    """
    a, w = solid(0), solid(40)
    h = [a] * 4 + [w] + [a] * 4
    t = [250.0 * k for k in range(len(h))]
    return clip(h, t), [{"t": t[4], "type": "hard"},
                        {"t": t[5], "type": "hard"}]


def scene_dissolve_then_later_cut():
    """Dissolve, a two-frame hold, then a clearly separated hard cut.

    At 300 ms per frame the cut is outside the merge window, so both
    boundaries must be reported and the dissolve span must not be
    stretched over the hold.
    """
    a, b, c = solid(0), solid(40), solid(20)
    h = [a] * 4 + [mix(b, a, k / 4.0) for k in (1, 2, 3, 4)] + [b] * 2 + [c] * 4
    t = [300.0 * k for k in range(len(h))]
    # h: [a]*4 (0-3), four fade frames (4-7), two held frames (8-9),
    # then shot C from frame 10.  Moderate pairs are 3..6, so the run
    # covers frames 3..7 and the hard cut is at frame 10.
    return clip(h, t), [{"t": t[7], "type": "gradual",
                         "span": [t[3], t[7]], "frames": 5},
                        {"t": t[10], "type": "hard"}]


def scene_low_texture_long():
    """Long, nearly static shot: tiny jitter only, no boundary at all."""
    rng = random.Random(13)
    h = []
    for _ in range(40):
        vec = [0.0] * 64
        vec[0] = 0.97
        vec[1 + rng.randrange(3)] = 0.03
        h.append(vec)
    t = vfr_times(40, seed=17)
    return clip(h, t), []


def random_stream(rng, length=None):
    """A random frame stream mixing cuts, ramps, flares and static runs."""
    if length is None:
        length = rng.randrange(6, 30)
    base = [0.0] * 64
    base[rng.randrange(64)] = 1.0
    hists = [base]
    t = 0.0
    target = len(hists)
    while target < length and target < 30:
        kind = rng.choice(["static", "cut", "ramp", "flare"])
        if kind == "static":
            n = rng.randrange(1, 4)
            hists.extend([hists[-1]] * n)
        elif kind == "cut":
            other = [0.0] * 64
            other[rng.randrange(64)] = 1.0
            hists.append(other)
        elif kind == "ramp":
            other = [0.0] * 64
            other[rng.randrange(64)] = 1.0
            n = rng.randrange(2, 5)
            hists.extend([mix(other, hists[-1], k / float(n))
                          for k in range(1, n + 1)])
        else:
            other = [0.0] * 64
            other[rng.randrange(64)] = 1.0
            hists.append(other)
            hists.append(hists[-2] if len(hists) > 1 else base)
        target = len(hists)
    hists = hists[:length]
    times = []
    t = 0.0
    for _ in hists:
        times.append(round(t, 3))
        t += rng.choice([20.0, 25.0, 40.0, 60.0])
    return clip(hists, times)


# --------------------------------------------------------------- checks

def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def match(bounds, expected):
    if len(bounds) != len(expected):
        return False, "count %d != %d" % (len(bounds), len(expected))
    for got, want in zip(bounds, expected):
        if got["type"] != want["type"]:
            return False, "type %s != %s" % (got["type"], want["type"])
        if not approx(float(got["t"]), float(want["t"])):
            return False, "t %s != %s" % (got["t"], want["t"])
        if "span" in want:
            if "span" not in got:
                return False, "missing span"
            if [float(x) for x in got["span"]] != [float(x) for x in want["span"]]:
                return False, "span %s != %s" % (got["span"], want["span"])
        if "frames" in want:
            if got.get("frames") != want["frames"]:
                return False, "frames %s != %s" % (got.get("frames"),
                                                   want["frames"])
    return True, ""


def report(name, ok, why=""):
    print("%-28s %s%s" % (name, "PASS" if ok else "FAIL",
                          "" if ok else "  (" + why + ")"))
    return ok


def check(name, frames, expected, threshold=TH):
    got = run_detect(frames, threshold)["boundaries"]
    ok, why = match(got, expected)
    return report(name, ok, why)


def main():
    scenes = [
        ("hard cut", scene_hard_cut()),
        ("consecutive cuts", scene_consecutive_cuts()),
        ("cut at exact hard", scene_threshold_exact()),
        ("dissolve 4-step", scene_dissolve(4)),
        ("dissolve 8-step", scene_dissolve(8)),
        ("dissolve then cut", scene_dissolve_then_cut()),
        ("dissolve + later cut", scene_dissolve_then_later_cut()),
        ("flash 1 frame", scene_flash(1)),
        ("flash 3 frames", scene_flash(3)),
        ("low-fps spike is a cut", scene_low_fps_flash_is_a_cut()),
        ("multiframe exposure", scene_multi_frame_flash()),
        ("flash outside window", scene_flash_outside_window()),
        ("merge window", scene_merge_close_cuts()),
        ("low-texture long shot", scene_low_texture_long()),
    ]
    ok = True
    for name, (frames, expected) in scenes:
        ok = check(name, frames, expected) and ok

    # VFR: the cut is reported at the real second-frame timestamp, which
    # is not any multiple of a nominal frame period.
    frames, _ = scene_hard_cut()
    got = run_detect(frames)
    ok = report("vfr real frame time",
                got["boundaries"][0]["t"] == frames[6]["t"]) and ok

    # Chunk invariance: same frames, several ways of splitting the input,
    # across every scene (a flash straddling a chunk boundary in
    # particular must not be re-decided differently).
    chunk_ok = True
    for name, (frames, _expected) in scenes:
        if len(frames) < 2:
            continue
        reference = run_cli(frames)
        rng = random.Random(len(frames))
        splits = [[3, 4, 100], [1] * len(frames), [7, 7, 7]]
        for _ in range(3):
            splits.append([rng.randrange(1, 5) for _ in range(len(frames))])
        for sizes in splits:
            parts, pos = [], 0
            for size in sizes:
                if pos >= len(frames):
                    break
                parts.append(frames[pos:pos + size])
                pos += size
            if pos < len(frames):
                parts.append(frames[pos:])
            if run_cli(frames, chunks=parts) != reference:
                print("   chunk mismatch: %s %s" % (name, sizes))
                chunk_ok = False
    ok = report("chunk invariance", chunk_ok) and ok

    # Offline feature shapes: sparse bin dumps and raw (unnormalised)
    # counts must read identically to the canonical dense input.
    sparse = [{"t": fr["t"],
               "hist": {str(i): v for i, v in enumerate(fr["hist"]) if v}}
              for fr in frames]
    ok = report("sparse histogram dump",
                run_detect(sparse) == run_detect(frames)) and ok
    counts = [{"t": fr["t"], "hist": [v * 1000.0 for v in fr["hist"]]}
              for fr in frames]
    ok = report("unnormalised counts",
                run_detect(counts) == run_detect(frames)) and ok

    # A flash reports its peak and emits no boundary.
    frames, _ = scene_flash(3)
    got = run_detect(frames)
    ok = report("flash reporting",
                not got["boundaries"] and len(got["flashes"]) == 1) and ok

    # Random fuzz: arbitrary frame streams, split at arbitrary points,
    # must give byte-identical output to the unsplit run.
    fuzz_ok = True
    for seed in range(25):
        rng = random.Random(900 + seed)
        frames = random_stream(rng)
        reference = run_cli(frames)
        if run_detect(frames) != reference:
            print("   fuzz seed %d: in-process != CLI" % seed)
            fuzz_ok = False
        parts, pos = [], 0
        while pos < len(frames):
            size = rng.randrange(1, 6)
            parts.append(frames[pos:pos + size])
            pos += size
        if run_cli(frames, chunks=parts) != reference:
            print("   fuzz seed %d: chunked != whole" % seed)
            fuzz_ok = False
    ok = report("randomized chunk fuzz", fuzz_ok) and ok

    print("ALL PASS" if ok else "SOME FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
