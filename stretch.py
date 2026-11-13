"""Sample-accurate variable-speed time stretching for stereo audio.

Algorithm: streaming WSOLA (waveform-similarity OLA) with a shared,
content-driven transient policy.

* The time map x(t) = integral of speed v(t) over the *output* clock t.
  v is piecewise linear between knots and held after the last knot.
  Integration uses the exact trapezoid rule per output sample, so the map
  and the output length are reproducible to the sample and independent of
  how the input is split into blocks.
* Sustained regions: Hann-windowed analysis frames are placed on a regular
  synthesis hop.  A bounded integer correction delta aligns each frame with
  the natural waveform continuation (normalised cross correlation on a mono
  control signal).  Pitch is preserved because only read positions move.
* Transient regions: a streaming energy-ratio detector confirms onsets with
  a fixed look-ahead.  Near an onset the engine switches to short windows,
  locks delta to 0 and resets the continuation state ("phase reset"), so a
  drum attack is rendered exactly once at its mapped output position.
* Both channels use the identical analysis positions, deltas and windows;
  only the per-channel samples differ, so every stereo relation (including
  exact anti-phase) is preserved through the OLA/normalisation.

The engine is a causal state machine: feed arbitrary block sizes through
process(); block boundaries never change the floating-point result.
"""

import argparse
import json
import math
from collections import deque
from pathlib import Path


# --------------------------------------------------------------------------- #
# Time map
# --------------------------------------------------------------------------- #
class TimeMap:
    """x(t) = integral_0^t v(u) du on the output sample clock."""

    def __init__(self, knots):
        self.knots = sorted((float(t), float(v)) for t, v in knots)
        self.x = [0.0]          # x[t] for t = 0 .. nout
        self.nout = None

    def speed(self, t):
        k = self.knots
        if t <= k[0][0]:
            return k[0][1]
        if t >= k[-1][0]:
            return k[-1][1]
        for i in range(1, len(k)):
            t1, v1 = k[i]
            if t <= t1:
                t0, v0 = k[i - 1]
                f = (t - t0) / (t1 - t0) if t1 != t0 else 0.0
                return v0 + (v1 - v0) * f
        return k[-1][1]

    def _grow_to(self, t):
        """Ensure x[0..t] exist (trapezoid rule, exact for linear v)."""
        while len(self.x) <= t:
            n = len(self.x)
            self.x.append(self.x[-1]
                          + 0.5 * (self.speed(n - 1) + self.speed(n)))

    def x_at(self, t):
        self._grow_to(t)
        return self.x[t]

    def finalize(self, nsrc):
        """Smallest integer t with x(t) >= nsrc (0 for an empty source)."""
        if nsrc <= 0:
            self.nout = 0
            self.x = [0.0]
            return 0
        t = 0
        while self.x[t] < nsrc:
            t += 1
            self._grow_to(t)
        self.nout = t
        return t

    def invert(self, src_pos):
        """Output sample whose mapped input position is nearest src_pos."""
        if self.nout == 0:
            return 0
        lo, hi = 0, self.nout  # x monotone; x[hi] >= nsrc > src_pos
        while lo < hi:
            mid = (lo + hi) // 2
            if self.x[mid] < src_pos:
                lo = mid + 1
            else:
                hi = mid
        cands = [t for t in (lo - 1, lo) if 0 <= t < self.nout]
        return min(cands, key=lambda t: abs(self.x[t] - src_pos))


# --------------------------------------------------------------------------- #
# Streaming transient (onset) detector on the mono control signal
# --------------------------------------------------------------------------- #
class TransientDetector:
    """Fixed-latency energy-ratio onset detector.

    Analysis windows start on a fixed grid (index 0, hop, 2*hop, ...), so
    confirmation depends only on signal content, never on block boundaries.
    An onset is a local maximum of short-window energy against the running
    long-term energy, with an absolute floor and a refractory gap.
    """

    def __init__(self, rate):
        self.win = max(16, int(round(rate * 0.008)))      # 8 ms
        self.hop = max(8, self.win // 2)                  # 4 ms
        self.confirm = self.win                           # look-ahead
        self.refractory = max(self.win, int(round(rate * 0.03)))
        self.thresh = 2.5
        self.history = 8
        self.floor = 1e-8          # absolute short-window energy floor
        self._cursor = 0          # next window start index
        self._horizon = 0         # input position deterministically covered
        self._energies = deque()  # energies *before* the candidate window
        self._cand = None         # (start, energy, score) awaiting confirm
        self._min_hist = 3        # warm-up windows before any event fires
        self._last_event = -10 ** 9
        self.events = []          # confirmed transient input indices

    def _refine(self, mono, start):
        end = min(start + self.win, len(mono))
        best_i, best_a = start, -1.0
        for i in range(start, end):
            a = abs(mono[i])
            if a > best_a:
                best_a, best_i = a, i
        return best_i

    def _confirm(self, mono, start, energy, score, next_score, warm):
        """A candidate is confirmed when its successor window is known."""
        if (warm and energy > self.floor and score > self.thresh
                and score >= next_score):
            idx = self._refine(mono, start)
            if idx - self._last_event >= self.refractory:
                self.events.append(idx)
                self._last_event = idx

    def run(self, mono, n_avail, flush):
        """Confirm every event whose confirmation look-ahead is available."""
        limit = n_avail if flush else n_avail - self.confirm
        while self._cursor + self.win <= limit:
            start = self._cursor
            e = 0.0
            for i in range(start, start + self.win):
                e += mono[i] * mono[i]
            long_e = (sum(self._energies) / len(self._energies)
                      if self._energies else 0.0)
            score = e / (long_e + self.floor)

            if self._cand is not None:
                c_start, c_e, c_score, c_warm = self._cand
                self._confirm(mono, c_start, c_e, c_score, score, c_warm)
            # candidate enters the history only *after* it is evaluated,
            # so its own energy cannot inflate the long-term baseline
            warm = len(self._energies) >= self._min_hist
            self._energies.append(e)
            if len(self._energies) > self.history:
                self._energies.popleft()
            self._cand = (start, e, score, warm)
            self._cursor += self.hop

        # The transient decision is final up to here: any window fully
        # below this position has already been evaluated, so future data
        # cannot add an event with an index before the horizon.
        if flush:
            if self._cand is not None:
                c_start, c_e, c_score, c_warm = self._cand
                self._confirm(mono, c_start, c_e, c_score, 0.0, c_warm)
                self._cand = None
            self._horizon = n_avail
        else:
            self._horizon = max(self._horizon,
                                max(0, n_avail - self.confirm - self.win))


# --------------------------------------------------------------------------- #
# WSOLA stretching engine
# --------------------------------------------------------------------------- #
class StretchEngine:
    def __init__(self, rate, knots):
        self.rate = rate
        self.tm = TimeMap(knots)
        self.det = TransientDetector(rate)

        # long frames: 32 ms window / 8 ms hop; short frames: 8 ms / 2 ms
        self.H_long = max(32, int(round(rate * 0.008)))
        self.N_long = 4 * self.H_long
        self.D_long = self.H_long                       # correction bound
        self.H_short = max(4, int(round(rate * 0.002)))
        self.N_short = 4 * self.H_short
        self.D_short = 0
        self.guard = self.N_long // 2                   # transient zone

        self.latency = (self.N_long // 2 + self.D_long
                        + self.guard + self.det.confirm)

        self._L, self._R, self._M = [], [], []
        self._pf = [0.0]                    # prefix energy of mono
        self._n_avail = 0
        self._flush = False
        self._nsrc = None

        self._s = 0                         # next synthesis centre
        self._have_ref = False              # continuation frame available?
        self._prev_delta = None             # correction of the last frame
        self._prev_center = 0               # chosen analysis centre, last long frame
        self._ev_ptr = 0
        self._num_l = []
        self._num_r = []
        self._wgt = []
        self._emitted = 0
        self.frame_log = []                 # actual analysis map

        self._hann = {}
        for n in (self.N_long, self.N_short):
            self._hann[n] = [
                0.5 - 0.5 * math.cos(2 * math.pi * k / n)
                for k in range(n)]

    # ----------------------------- input handling -------------------------- #
    def process(self, block):
        for frame in block:
            l = float(frame[0])
            r = float(frame[1])
            self._L.append(l)
            self._R.append(r)
            m = 0.5 * (l + r)
            self._M.append(m)
            self._pf.append(self._pf[-1] + m * m)
        self._n_avail = len(self._M)
        self._pump()
        return self._emit(None)

    def flush(self):
        self._flush = True
        self._nsrc = len(self._M)
        self.tm.finalize(self._nsrc)
        self._pump()
        return self._emit(self.tm.nout)

    # ------------------------------- core loop ----------------------------- #
    def _near_transient(self, ideal):
        ev = self.det.events
        # advance pointer past events well behind the current centre
        while (self._ev_ptr < len(ev)
               and ev[self._ev_ptr] < ideal - self.guard):
            self._ev_ptr += 1
        j = self._ev_ptr
        while j < len(ev) and ev[j] <= ideal + self.guard:
            if abs(ev[j] - ideal) <= self.guard:
                return True
            j += 1
        return False

    def _seg_energy(self, center, n):
        """Energy of mono segment [center-n/2, center+n/2), prefix sums."""
        a = center - n // 2
        b = a + n
        a_c = max(0, a)
        b_c = min(self._n_avail, b)
        return self._pf[b_c] - self._pf[a_c] if b_c > a_c else 0.0

    def _correlate(self, ref_center, ideal, n, bound):
        """Normalised cross correlation; integer delta in [-bound, bound]."""
        half = n // 2
        ref_e = self._seg_energy(ref_center, n)
        if ref_e <= 1e-12:
            return 0
        r0 = ref_center - half
        i0_base = int(round(ideal)) - half
        n_avail = self._n_avail
        mono = self._M

        def score(d):
            c0 = i0_base + d
            cand_e = self._seg_energy(c0 + half, n)
            if cand_e <= 1e-12:
                return -1.0
            dot = 0.0
            if (r0 >= 0 and c0 >= 0 and r0 + n <= n_avail
                    and c0 + n <= n_avail):
                for k in range(n):              # tight fast path
                    dot += mono[r0 + k] * mono[c0 + k]
            else:
                for k in range(n):              # zero-padded path
                    ri = r0 + k
                    ci = c0 + k
                    a = mono[ri] if 0 <= ri < n_avail else 0.0
                    b = mono[ci] if 0 <= ci < n_avail else 0.0
                    dot += a * b
            return dot / math.sqrt(ref_e * cand_e)

        best_d, best_s = 0, -2.0
        for d in range(-bound, bound + 1, 2):   # coarse step 2
            s = score(d)
            if s > best_s:
                best_s, best_d = s, d
        for d in range(max(-bound, best_d - 2),
                       min(bound, best_d + 2) + 1):   # refine +-2
            if d % 2 == 0:
                continue
            s = score(d)
            if s > best_s:
                best_s, best_d = s, d
        return best_d if best_s > -2.0 else 0

    def _add_frame(self, center, n, s):
        half = n // 2
        w = self._hann[n]
        size = s + half + n
        if len(self._wgt) < size:
            pad = size - len(self._wgt)
            self._num_l.extend([0.0] * pad)
            self._num_r.extend([0.0] * pad)
            self._wgt.extend([0.0] * pad)
        start = s - half
        n_avail = self._n_avail
        L, R = self._L, self._R
        for k in range(n):
            out_i = start + k
            if out_i < 0:
                continue
            idx = center - half + k
            if 0 <= idx < n_avail:
                wk = w[k]
                self._num_l[out_i] += wk * L[idx]
                self._num_r[out_i] += wk * R[idx]
                self._wgt[out_i] += wk          # COLA: sum of Hann, not w^2

    def _pump(self):
        while True:
            self.det.run(self._M, self._n_avail, self._flush)
            ideal = self.tm.x_at(self._s)

            if self._flush and self.tm.nout is not None:
                # choose the frame size first, then test coverage vs nout
                is_short = self._near_transient(ideal)
                n = self.N_short if is_short else self.N_long
                if self._s - n // 2 >= self.tm.nout:
                    return

            # Single look-ahead gate serving both block invariance rules:
            # (1) the onset detector's *deterministic horizon* must cover the
            #     whole guarded zone, so the long/short frame decision cannot
            #     change when more input arrives;
            # (2) every sample the bounded correlation search may touch must
            #     already be present, so streaming == whole-input decisions.
            # flush() lifts this gate and zero-pads the tail instead.
            if not self._flush:
                need_detect = int(math.ceil(ideal)) + self.guard
                need_data = (int(math.ceil(ideal)) + self.D_long
                             + self.N_long // 2)
                if (need_detect > self.det._horizon
                        or need_data > self._n_avail):
                    return

            is_short = self._near_transient(ideal)
            if is_short:
                n, h = self.N_short, self.H_short
                delta = 0
                self._have_ref = False             # phase reset at onset
            else:
                n, h = self.N_long, self.H_long
                bound = self.D_long
                if not self._have_ref:
                    delta = 0                      # first frame after reset
                else:
                    # natural continuation: previous chosen analysis frame
                    # advanced by exactly one synthesis hop
                    ref_center = self._prev_center + h
                    need = (max(abs(ref_center), int(round(ideal)) + bound)
                            + n // 2)
                    if not self._flush and need > self._n_avail:
                        return
                    delta = self._correlate(
                        ref_center, ideal, n, bound)
            center = int(round(ideal)) + delta
            need = abs(center) + n // 2
            if not self._flush and need > self._n_avail:
                return

            self.frame_log.append({
                "out_center": self._s, "ideal_in": ideal,
                "delta": delta, "analysis_center": center,
                "window": n, "transient_mode": bool(is_short)})
            self._add_frame(center, n, self._s)
            if not is_short:
                self._have_ref = True
                self._prev_delta = delta
                self._prev_center = center
            self._s += h

    def _emit(self, hard_end):
        # future frames may still write at indices >= s - N_long/2
        safe = (hard_end if hard_end is not None
                else max(0, self._s - self.N_long // 2))
        out = []
        for i in range(self._emitted, safe):
            w = self._wgt[i]
            if w > 1e-12:
                out.append([self._num_l[i] / w,
                            self._num_r[i] / w])
            else:
                out.append([0.0, 0.0])
        self._emitted = safe
        return out


# --------------------------------------------------------------------------- #
# JSON contract
# --------------------------------------------------------------------------- #
def render(data):
    rate = int(data["rate"])
    knots = [(float(t), float(v)) for t, v in data["knots"]]
    engine = StretchEngine(rate, knots)
    frames = []
    for block in data["blocks"]:
        frames.extend(engine.process(block))
    frames.extend(engine.flush())

    nsrc = sum(len(b) for b in data["blocks"])
    nout = engine.tm.nout
    if len(frames) < nout:
        frames.extend([[0.0, 0.0]] * (nout - len(frames)))
    frames = frames[:nout]

    mapping = [engine.tm.x_at(t) for t in range(nout)]
    transients = [[int(idx), int(engine.tm.invert(idx))]
                  for idx in engine.det.events]
    return {
        "frames": frames,
        "mapping": mapping,
        "transients": transients,
        "latency": int(engine.latency),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    result = render(json.loads(a.input.read_text()))
    a.output.write_text(json.dumps(result, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
