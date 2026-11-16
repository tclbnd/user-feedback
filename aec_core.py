#!/usr/bin/env python3
"""Signal-level acoustic echo canceller (pure standard library).

Pipeline (streaming, sample-by-sample internally so the block size chosen by
the caller cannot change behaviour):

  reference x -> bulk delay line -> adaptive FIR w (block NLMS) -> echo
  estimate y_hat; error e = mic - y_hat is the cleaned output.

Freeze rules (the actual repair for the original bug):
  * double-talk (Geigel detector with hysteresis)  -> hold w, hold delay
  * reference silence                              -> hold w, hold delay
  * while frozen the previous filter coefficients keep producing y_hat, so
    near-end speech amplitude is preserved instead of being cancelled.
  * adaptation resumes on the *next* frame after the release condition is
    seen ("agreed" resume state), never mid-frame.

Delay relocation:
  * cross-correlate a window of reference and microphone while adapting;
    a peak whose lag moves beyond a tolerance triggers a resync of the bulk
    delay line and a "delay-shift" diagnostic event.

Block invariance:
  * every state decision is made per internal frame of ``frame_len`` samples.
    Feeding one frame per block or many frames per block yields the freeze /
    resume transitions at exactly the same sample positions.

Flushing:
  * ``flush()`` zero-pads the last partial block and drains the filter tail
    while continuing to emit per-frame diagnostics.
"""
import math

# --- tunables (documented so fixtures/acceptance share the same constants) --
FRAME_MS = 8                 # internal control frame length
DEFAULT_FS = 8000
FILTER_TAPS = 64             # adaptive FIR length (covers the echo path)
NLMS_MU = 0.7                # sample-by-sample NLMS step size
NLMS_EPS = 1e-3              # regularisation relative to reference power
GEIGEL_ENTER = 1.0           # smoothed mic/ref RMS ratio above -> double-talk
GEIGEL_EXIT = 0.9            # below this (with hysteresis) double-talk ends
DT_ENTER_FRAMES = 2          # consecutive high-ratio frames to enter DT
NEAR_HYST_FRAMES = 3         # near-end must stay low this many frames
DT_MIN_HOLD_FRAMES = 6       # a double-talk freeze lasts at least ~50 ms
POWER_SMOOTH = 0.35          # EMA factor for detector power estimates
SILENCE_POWER = 1e-5         # reference frame power at/below = silent
WARMUP_FRAMES = 25           # no double-talk freeze before convergence
ERLE_TRIGGER_DB = 3.0        # residual must beat mic by this much to trust DT
RESET_KEEP = 0.5             # coefficient retention after a delay relocation
RELOCALIZE_MU = 0.8          # faster NLMS step during post-shift reconvergence
RELOCALIZE_FRAMES = 50       # boosted-step window after a relocation (~0.4s)
REL_WINDOW_MS = 128          # cross-correlation window
REL_HOP_MS = 64              # relocation check interval
REL_PEAK_RATIO = 2.0         # peak vs mean correlation required
DELAY_TOL_SAMPLES = 16       # lag move beyond this => resync (jitter guard)
MAX_DELAY_SAMPLES = 320      # search range for the bulk delay


def _mean_power(seq):
    if not seq:
        return 0.0
    return sum(v * v for v in seq) / len(seq)


def _dot(a, b):
    s = 0.0
    for x, y in zip(a, b):
        s += x * y
    return s


class AEC:
    """Streaming adaptive echo canceller."""

    def __init__(self, fs=DEFAULT_FS, frame_len=None, filter_taps=FILTER_TAPS,
                 mu=NLMS_MU, max_delay=MAX_DELAY_SAMPLES,
                 initial_delay=0):
        self.fs = fs
        self.frame_len = frame_len or max(1, int(fs * FRAME_MS / 1000))
        self.L = filter_taps
        self.mu = mu
        self.max_delay = max_delay

        # Adaptive FIR coefficients and the reference delay line that feeds
        # it.  refline[0] is the oldest buffered reference sample; the bulk
        # delay d picks where the FIR window starts inside this line.
        self.w = [0.0] * self.L
        self.refline = [0.0] * (max_delay + self.L)
        self.delay = int(initial_delay)
        # Offset between a cross-correlation lag (bulk delay plus the
        # echo-path peak tap) and the bulk delay that aligns FIR tap 0.
        # The acoustic echo path is fixed for the whole session, so this is
        # estimated once on the initial single-talk convergence and then
        # latched; re-estimating it on a misaligned post-shift filter would
        # feed garbage back into the relocation.
        self._path_peak_offset = None

        # Block-NLMS accumulation across one internal frame.
        self._xblock = []
        self._eblock = []
        self._mblock = []

        # Double-talk state machine with explicit hysteresis.
        self.dt = False
        self._dt_low_frames = 0
        self._dt_high_frames = 0
        self._dt_hold = 0
        # EMA-smoothed detector powers suppress single-frame ratio spikes.
        self._px_ema = 0.0
        self._pm_ema = 0.0
        # Sticky convergence level: high water mark with slow decay.
        self._erle_best = 0.0
        # Post-relocation reconvergence booster.
        self._boost_frames = 0
        self.ref_silent = False
        self.frozen = False
        self.freeze_reason = ""
        self._resume_pending = False

        # Diagnostics / relocation bookkeeping.
        self.sample_index = 0
        self.frame_index = 0
        self.events = []
        self.diagnostics = []
        self._rel_window = max(1, int(fs * REL_WINDOW_MS / 1000))
        self._rel_hop = max(1, int(fs * REL_HOP_MS / 1000))
        self._rel_x = []
        self._rel_m = []
        self._last_lag = self.delay
        self.relocations = 0

    # ------------------------------------------------------------------ #
    # Delay line / filter
    # ------------------------------------------------------------------ #
    def _push_ref(self, x):
        self.refline.pop(0)
        self.refline.append(x)

    def _filter_input(self):
        """Reference window seen by the FIR for the current sample.

        ``refline`` ends at the current sample; element k of the returned
        window must be x(n - (delay + k)) so that coefficient w[k] models the
        echo-path tap at age (delay + k), matching the convolution used to
        build the microphone signal.
        """
        last = len(self.refline) - 1
        # element k has age delay+k and therefore index last-(delay+k)
        return [self.refline[last - (self.delay + k)]
                for k in range(self.L)]

    def _filter_sample(self, xbuf):
        return _dot(self.w, xbuf)

    def resync_delay(self, new_delay, reason):
        """Relocate the bulk delay that aligns FIR tap 0.

        Because ``delay`` is the age of FIR tap 0, a bulk-delay change only
        slides which reference samples fill the (same-shaped) FIR window; the
        echo-path impulse response represented by ``w`` keeps its tap
        positions.  We therefore retain the coefficients with a mild shrink
        to cover the part of the tail whose alignment changed, and boost the
        NLMS step briefly so the filter re-converges on the new alignment.
        """
        new_delay = max(0, min(self.max_delay, int(new_delay)))
        if abs(new_delay - self.delay) > DELAY_TOL_SAMPLES:
            old = self.delay
            self.delay = new_delay
            self.w = [RESET_KEEP * c for c in self.w]
            self._boost_frames = RELOCALIZE_FRAMES
            self.relocations += 1
            self.events.append({
                "at_sample": self.sample_index,
                "at_frame": self.frame_index,
                "kind": "delay-shift",
                "old_delay": old,
                "new_delay": new_delay,
                "reason": reason,
            })

    def _try_relocate(self, px):
        # Only trust correlation when the reference is active and the filter
        # is allowed to learn; otherwise double-talk/silence would drag the
        # lag estimate onto the near-end signal.
        if self.frozen or px <= SILENCE_POWER:
            return
        if len(self._rel_x) < self._rel_window:
            return
        if self.sample_index % self._rel_hop != 0:
            return
        x = self._rel_x[-self._rel_window:]
        m = self._rel_m[-self._rel_window:]
        lag, peak, mean_abs = self._estimate_lag(x, m)
        if lag is None or peak < REL_PEAK_RATIO * mean_abs:
            return
        # Latch the echo-path peak offset from the well-converged initial
        # filter, while we are still on the original bulk delay.
        if self._path_peak_offset is None and self._erle_best >= 20.0:
            self._path_peak_offset = self._dominant_tap()
        offset = self._path_peak_offset or 0
        # The correlator locates the dominant echo, whose lag is the FIR
        # bulk delay plus the echo-path peak tap.  Convert to the bulk delay
        # that aligns FIR tap 0 with the start of the echo path.
        bulk_lag = max(0, lag - offset)
        # Small jitter around the current operating point: track silently.
        if abs(bulk_lag - self.delay) <= DELAY_TOL_SAMPLES:
            self._last_lag = bulk_lag
            return
        # A confident, larger move is a genuine bulk-delay relocation.
        self.resync_delay(bulk_lag, "cross-correlation")
        self._last_lag = bulk_lag

    def _dominant_tap(self):
        """Index of the largest-magnitude FIR coefficient."""
        best, best_val = 0, 0.0
        for k, c in enumerate(self.w):
            if abs(c) > best_val:
                best, best_val = k, abs(c)
        return best

    def _estimate_lag(self, x, m):
        """Best bulk delay via normalised cross-correlation x vs mic."""
        N = len(x)
        sx = sum(v * v for v in x)
        sm = sum(v * v for v in m)
        if sx <= 1e-12 or sm <= 1e-12:
            return None, 0.0, 0.0
        norm = (sx * sm) ** 0.5
        best_lag, best_val = 0, 0.0
        total = 0.0
        count = 0
        max_lag = min(self.max_delay, N - self.L)
        for lag in range(0, max_lag + 1):
            s = 0.0
            # correlate the part where both windows fully overlap
            for n in range(N - max_lag):
                s += x[n] * m[n + lag]
            v = abs(s) / norm
            total += v
            count += 1
            if v > best_val:
                best_val, best_lag = v, lag
        mean = total / max(1, count)
        return best_lag, best_val, mean

    # ------------------------------------------------------------------ #
    # Freeze control, evaluated once per internal frame
    # ------------------------------------------------------------------ #
    def _control_frame(self, px, pm, pres, force_hold=False):
        """Per-frame freeze state machine (runs once every frame_len
        samples, so its output cannot depend on the caller's block size).

        px          reference power at the filter input
        pm          raw microphone power
        pres        residual (error) power of the frame
        force_hold  zero-padded flush frame: keep the previous state exactly
        """
        prev_frozen = self.frozen
        self.ref_silent = px <= SILENCE_POWER

        # A zero-padded flush frame never changes the state trajectory.
        if force_hold:
            self.ref_silent = False
            self.freeze_reason = self.freeze_reason if prev_frozen else ""
            return

        # EMA-smoothed powers (freeze the reference EMA during silence so
        # the ratio does not explode when the reference returns).
        if not self.ref_silent:
            self._px_ema = (POWER_SMOOTH * px
                            + (1.0 - POWER_SMOOTH) * self._px_ema)
        self._pm_ema = (POWER_SMOOTH * pm
                        + (1.0 - POWER_SMOOTH) * self._pm_ema)
        ratio = ((self._pm_ema ** 0.5) / (self._px_ema ** 0.5)
                 if self._px_ema > 0.0 else 0.0)

        # Convergence is a *latched* property: once the filter has achieved
        # good ERLE on single-talk we consider the echo path modelled and
        # keep the detector armed for the whole session.  Using the current
        # frame's ERLE here would disarm the detector exactly when
        # double-talk starts (the residual jumps immediately), and an
        # exponentially decaying marker would disarm it again after a delay
        # relocation - both are the failures we must avoid.  A relocation
        # handles its own coefficient soft reset independently.
        if not self.ref_silent and pm > 0.0:
            erle_db = 10.0 * math.log10(pm / max(pres, 1e-12))
            self._erle_best = max(self._erle_best, erle_db)
        converged = self._erle_best >= ERLE_TRIGGER_DB

        # 1. Apply a resume armed by an earlier frame, exactly at a frame
        #    boundary.  The current frame is then re-evaluated from scratch.
        if self._resume_pending:
            self._resume_pending = False
            self.dt = False
            self._dt_low_frames = 0
            self._dt_high_frames = 0
            self._dt_hold = 0

        # 2. Geigel double-talk detector with entry/exit hysteresis.
        #    Gated until the filter has had a warm-up window to learn the
        #    echo path: an all-zero initial filter would otherwise make every
        #    frame look like double-talk and adaptation would never start.
        warm = self.frame_index >= WARMUP_FRAMES
        dt_armed = warm and (converged or self.dt)

        if not self.dt:
            if (dt_armed and not self.ref_silent
                    and ratio >= GEIGEL_ENTER):
                self._dt_high_frames += 1
                # Require several consecutive high-ratio frames: a single
                # transient frame in single-talk must not freeze learning.
                if self._dt_high_frames >= DT_ENTER_FRAMES:
                    self.dt = True
                    self._dt_low_frames = 0
                    self._dt_hold = DT_MIN_HOLD_FRAMES
            else:
                self._dt_high_frames = 0
        else:
            # Minimum hold so a single quiet frame inside speech cannot
            # release the freeze; counts down regardless of the ratio.
            self._dt_hold = max(0, self._dt_hold - 1)
            if self._dt_hold == 0:
                if self.ref_silent or ratio < GEIGEL_EXIT:
                    self._dt_low_frames += 1
                else:
                    self._dt_low_frames = 0
                if self._dt_low_frames >= NEAR_HYST_FRAMES:
                    # Release condition seen: keep this frame frozen and
                    # resume adaptation on the NEXT frame (agreed resume).
                    self._resume_pending = True

        # 3. This frame's freeze state.  A pending resume leaves dt set for
        #    the current frame, so it stays frozen until the boundary.
        now_frozen = self.dt or self.ref_silent
        reason = ("double-talk" if self.dt else
                  "reference-silence" if self.ref_silent else "")

        if now_frozen and not prev_frozen:
            self.events.append({
                "at_sample": self.sample_index,
                "at_frame": self.frame_index,
                "kind": "freeze",
                "reason": reason,
            })
        elif prev_frozen and not now_frozen:
            self.events.append({
                "at_sample": self.sample_index,
                "at_frame": self.frame_index,
                "kind": "resume",
                "reason": self.freeze_reason,
            })

        self.frozen = now_frozen
        self.freeze_reason = reason if now_frozen else ""

    # ------------------------------------------------------------------ #
    # Streaming API
    # ------------------------------------------------------------------ #
    def process_block(self, ref, mic):
        """Process equal-length reference/mic lists; return cleaned samples
        plus one diagnostic record per internal frame in this block."""
        if len(ref) != len(mic):
            raise ValueError("ref and mic must have equal length")
        out = []
        diag = []
        fl = self.frame_len
        for x, m in zip(ref, mic):
            self._push_ref(x)
            xbuf = self._filter_input()
            y = self._filter_sample(xbuf)
            e = m - y
            out.append(e)
            self._xblock.append(xbuf)
            self._eblock.append(e)
            self._mblock.append(m)
            self._rel_x.append(x)
            self._rel_m.append(m)
            if len(self._rel_x) > self._rel_window:
                self._rel_x.pop(0)
                self._rel_m.pop(0)
            self.sample_index += 1

            if len(self._eblock) == fl:
                rec = self._end_of_frame(diag)
                if rec is not None:
                    diag.append(rec)
        return out, diag

    def _end_of_frame(self, diag_out, force_hold=False):
        xb, eb, mb = self._xblock, self._eblock, self._mblock
        # Reference power at the filter input (bulk-delay aligned) and raw
        # microphone power for the Geigel detector.
        px = _mean_power([b[0] for b in xb])
        pmic = _mean_power(mb)
        pm = _mean_power(eb)  # residual (echo estimate error) power
        self._control_frame(px, pmic, pm, force_hold=force_hold)
        self._try_relocate(px)

        if not self.frozen and px > SILENCE_POWER:
            # Sample-by-sample NLMS: every error sample updates the same
            # coefficients, so convergence is much faster than block
            # gradient accumulation while the freeze decision stays per
            # frame and therefore independent of the caller's block size.
            # A relocation temporarily boosts the step to re-converge fast.
            if self._boost_frames > 0:
                step_mu = RELOCALIZE_MU
                self._boost_frames -= 1
            else:
                step_mu = self.mu
            for b, e in zip(xb, eb):
                norm = _dot(b, b) + NLMS_EPS
                step = step_mu * e / norm
                for k in range(self.L):
                    self.w[k] += step * b[k]

        rec = {
            "frame": self.frame_index,
            "sample_start": self.sample_index - self.frame_len,
            "sample_end": self.sample_index,
            "state": "frozen" if self.frozen else "adapting",
            "reason": self.freeze_reason,
            "delay": self.delay,
            "dt_ratio": ((self._pm_ema ** 0.5) / (self._px_ema ** 0.5)
                         if self._px_ema > 0.0 else 0.0),
            "erle_best": self._erle_best,
            "ref_power": px,
            "mic_power": pmic,
            "residual_power": pm,
            "coeff_norm": sum(c * c for c in self.w) ** 0.5,
        }
        self.diagnostics.append(rec)
        self.frame_index += 1
        self._xblock = []
        self._eblock = []
        self._mblock = []
        return rec

    def flush(self):
        """Drain a partial frame with zero-padded input; return diagnostics.

        No adaptation happens on padded input; the filter tail keeps ringing
        out through the already produced error samples, and the final state
        is reported.
        """
        tail_diag = []
        if self._eblock:
            pad = self.frame_len - len(self._eblock)
            for _ in range(pad):
                self._push_ref(0.0)
                self._eblock.append(0.0)
                self._mblock.append(0.0)
                self._xblock.append([0.0] * self.L)
                self.sample_index += 1
            # Padded frame: hold the previous state exactly, emit a
            # diagnostic, and never adapt on synthetic zeros.
            rec = self._end_of_frame(tail_diag, force_hold=True)
            if rec is not None:
                rec["flushed"] = True
                tail_diag.append(rec)
        final = {
            "frames_processed": self.frame_index,
            "samples_processed": self.sample_index,
            "final_delay": self.delay,
            "final_coeff_norm": sum(c * c for c in self.w) ** 0.5,
            "relocations": self.relocations,
            "events": list(self.events),
            "frozen": self.frozen,
            "freeze_reason": self.freeze_reason,
        }
        return tail_diag, final

    # Convenience for tests: full signal in one shot -------------------- #
    def run(self, ref, mic, block_len=None):
        """Process a whole signal; block_len only changes chunking, never
        the internal state trajectory."""
        block_len = block_len or self.frame_len
        cleaned = []
        diags = []
        for start in range(0, len(ref), block_len):
            c, d = self.process_block(ref[start:start + block_len],
                                      mic[start:start + block_len])
            cleaned.extend(c)
            diags.extend(d)
        tail, final = self.flush()
        diags.extend(tail)
        return cleaned, diags, final
