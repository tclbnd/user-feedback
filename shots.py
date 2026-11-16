#!/usr/bin/env python3
"""Shot-boundary detector over pre-computed colour histograms.

Usage: python3 shots.py input.json [chunk2.json ...] output.json

Frames are in capture order and carry REAL timestamps in ms (variable
frame rate).  No code path assumes a constant frame spacing: every time
decision (flash window, merge window, reported boundary time) uses the
frame timestamps, and a boundary is always located *exactly* on a frame
time, never interpolated between frames.

Detection rules (see README):

* d >= hard                        -> hard cut at the second frame's t.
* grad <= d < hard for k >= 2
  consecutive pairs with sum >= hard -> gradual transition, span
  [first frame t, last frame t], ``frames`` = number of frames covered.
* a short excursion that leaves the picture and returns to a histogram
  within ``grad`` of the frame before the excursion, all inside
  ``flash`` ms -> flash: suppressed, both sides merged, no boundary.
* boundaries closer than ``merge`` ms to the previously emitted one are
  dropped (first wins).

A flash is only decided once the picture has recovered, and a gradual
transition is only decided once its run has ended; both decisions need
look-ahead, which is why the whole stream is flattened before scanning.
A stream delivered as one document or as any number of chunks therefore
produces byte-identical output.
"""

import json
import sys

EPS = 1e-9
CONF_CAP = 1.5


# --------------------------------------------------------------- reading

def _flatten(items):
    """Flatten arbitrarily nested frame chunks into one ordered frame list.

    Accepts ``[frame, ...]``, ``[[frame, ...], ...]`` and
    ``[{"frames": [...]}, ...]`` at any depth, so a payload assembled from
    differently sized chunks yields the same flat stream.
    """
    flat = []
    stack = list(reversed(items))
    while stack:
        item = stack.pop()
        if isinstance(item, dict) and "frames" in item:
            stack.extend(reversed(item["frames"]))
        elif isinstance(item, list):
            stack.extend(reversed(item))
        else:
            flat.append(item)
    return flat


def load(paths):
    """Read one or more JSON chunks and return (frames, thresholds).

    ``thresholds`` may accompany the first chunk only; later chunks may be
    bare lists or documents without thresholds.
    """
    frames = []
    thresholds = None
    for path in paths:
        with open(path, "r") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict):
            part = doc.get("frames", [])
            if thresholds is None and "thresholds" in doc:
                thresholds = doc["thresholds"]
        else:
            part = doc
        frames.extend(_flatten(part))
    if thresholds is None:
        raise SystemExit("input has no thresholds")
    return frames, thresholds


def _prepare(frames):
    """Return (raw_times, float_times, dense_histograms).

    Offline feature dumps are not always in the friendliest shape: bins may
    be stored sparsely as an index -> value object (missing bins are zero),
    and raw counts may not have been normalised.  Both are handled here so
    the distance is always computed on 64-bin probability vectors.
    """
    raw_t, ts, hists = [], [], []
    for fr in frames:
        if "t" not in fr or "hist" not in fr:
            raise ValueError("frame is missing 't' or 'hist': %r" % (fr,))
        raw_t.append(fr["t"])
        ts.append(float(fr["t"]))
        hists.append(fr["hist"])

    sparse = any(isinstance(h, dict) for h in hists)
    if sparse:
        width = 0
        for h in hists:
            if isinstance(h, dict):
                for k in h:
                    width = max(width, int(k) + 1)
            else:
                width = max(width, len(h))
        dense = []
        for h in hists:
            vec = [0.0] * width
            if isinstance(h, dict):
                for k, v in h.items():
                    vec[int(k)] = float(v)
            else:
                for idx, v in enumerate(h[:width]):
                    vec[idx] = float(v)
            dense.append(vec)
    else:
        dense = [[float(x) for x in h] for h in hists]
        width = len(dense[0]) if dense else 0
        if any(len(v) != width for v in dense):
            raise ValueError("histograms have different bin counts")

    # Normalise only when the dump holds raw counts; conformant input
    # (already summing to 1) is left untouched.
    for vec in dense:
        total = sum(vec)
        if total > EPS and abs(total - 1.0) > 1e-6:
            for idx in range(len(vec)):
                vec[idx] /= total
    return raw_t, ts, dense


# -------------------------------------------------------------- distance

def dist(a, b):
    """Colour-histogram distance: half the L1 distance (range 0..1)."""
    return sum(abs(x - y) for x, y in zip(a, b)) / 2.0


# -------------------------------------------------------------- detector

def detect(frames, th):
    if not frames:
        return {"boundaries": [], "flashes": []}

    raw_t, ts, hs = _prepare(frames)
    n = len(ts)
    # ds[i] is the change across the pair (frame i, frame i+1).
    ds = [dist(hs[i], hs[i + 1]) for i in range(n - 1)]

    hard = float(th["hard"])
    grad = float(th["grad"])
    flash_ms = float(th.get("flash", 0.0) or 0.0)
    merge_ms = float(th.get("merge", 0.0) or 0.0)

    boundaries, flashes = [], []
    last_emitted = None          # time of the last boundary kept

    def confidence(value):
        if hard <= 0:
            return 0.0
        return round(min(value / hard, CONF_CAP), 3)

    def emit(idx, kind, conf, span=None, frames_covered=None):
        """Emit a candidate boundary unless it is inside the merge window.

        ``span`` is a pair of frame indices and is reported with their real
        timestamps; ``frames_covered`` is the number of frames the run of
        pairs spans (k pairs cover k+1 frames), reported as ``frames``.
        """
        nonlocal last_emitted
        t = ts[idx]
        if last_emitted is not None and (t - last_emitted) < merge_ms:
            return False
        entry = {"t": raw_t[idx], "type": kind, "confidence": confidence(conf)}
        if span is not None:
            entry["span"] = [raw_t[span[0]], raw_t[span[1]]]
        if frames_covered is not None:
            entry["frames"] = frames_covered
        boundaries.append(entry)
        last_emitted = t
        return True

    def flash(a):
        """Check whether the excursion starting after frame ``a`` is a flash.

        Frame ``a`` is the last frame of the steady picture before the
        excursion; the excursion covers frames a+1 .. b-1 and ``b`` is the
        first frame back within ``grad`` of frame ``a``'s histogram.  It is
        a flash when

          * the picture is steady before it: the pair (a-1, a) changes by
            less than ``grad`` (a return to the steady picture) and the
            gap between those two frames is at most ``flash`` ms, and
          * the deviation is short: the recovery frame ``b`` arrives no
            more than ``flash`` ms after the first deviating frame a+1.

        Both bounds are REAL milliseconds taken from the frame timestamps,
        so a multi-frame flare at a high frame rate is still a flash while
        the same number of frames at 10 fps is a real transition.  The
        decision is deferred: nothing is emitted for the excursion until
        the recovery frame ``b`` has been seen.

        Returns ``(b, peak)`` -- recovery frame and the excursion frame
        farthest from the steady picture -- or ``None``.
        """
        if a < 1 or ds[a - 1] >= grad:
            return None                     # not entered from a steady run
        if (ts[a] - ts[a - 1]) > flash_ms + EPS:
            return None                     # the return into it is too slow
        pre = hs[a]
        deadline = ts[a + 1] + flash_ms + EPS   # real time, never a count
        b = a + 2                           # a+1 already differs by >= grad
        while b < n and ts[b] <= deadline:
            if dist(pre, hs[b]) < grad:
                peak, best = a + 1, -1.0
                for k in range(a + 1, b):
                    dk = dist(pre, hs[k])
                    if dk > best:
                        best, peak = dk, k
                return b, peak
            b += 1
        return None

    def record_flash(peak):
        flashes.append({"t": raw_t[peak]})

    i = 0
    while i < n - 1:
        d = ds[i]

        if d >= hard:
            hit = flash(i)
            if hit is not None:
                record_flash(hit[1])
                i = hit[0]            # both sides merged, nothing emitted
                continue
            emit(i + 1, "hard", d)
            i += 1
            continue

        if d >= grad:
            # Maximal run of consecutive moderate pairs.  A pair that is
            # >= hard is a cut, not part of the dissolve, and a pair below
            # grad ends the transition.
            j = i
            acc = 0.0
            while j < n - 1 and grad <= ds[j] < hard:
                acc += ds[j]
                j += 1
            if j - i >= 2 and acc >= hard:
                hit = flash(i)
                # Only trust the flash reading when the picture came back
                # *inside* the run (recovery <= j).  A genuine dissolve
                # that is then followed by a hard cut also "recovers", but
                # its recovery frame lies past the run's end; suppressing
                # it there would swallow the following cut.
                if hit is not None and hit[0] <= j:
                    # Ramped transient exposure that came back to the
                    # starting picture: a flash, not a dissolve.
                    record_flash(hit[1])
                    i = hit[0]
                    continue
                emit(j, "gradual", acc, span=(i, j), frames_covered=j - i + 1)
            i = j
            continue

        i += 1

    return {"boundaries": boundaries, "flashes": flashes}


def main(argv):
    if len(argv) < 3:
        sys.stderr.write(
            "usage: shots.py input.json [chunk2.json ...] output.json\n")
        return 2
    frames, thresholds = load(argv[1:-1])
    result = detect(frames, thresholds)
    with open(argv[-1], "w") as fh:
        json.dump(result, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
