#!/usr/bin/env python3
"""Band-split de-esser: signal-driven detection, linked gain, clean switching.

The original ``deess.py`` built one gain per block from the ratio of two
pre-computed band energies.  That one number pulled the *whole* high band down
for the whole block, the gain stayed behind after the sibilant, both envelope
directions used the attack coefficient, and enable/bypass swapped branches
without a ramp - so the voice went dull and the switch clicked.  This module
fixes the three stages separately.

1. Band split - only the sibilant band is ever scaled::

       low  = FIR_lp(x)                 # linear phase, taps odd
       high = x[n - D] - low            # D = (taps - 1) / 2
       out  = low + g * high            # bypass = mix-in of dry

   ``low`` and ``high`` are complementary (``low + high == x[n - D]``) and
   carry identical latency, so recombination is sample-aligned: with
   ``g == 1`` the output is the input delayed by ``D``, bit for bit.  Because
   the low band is never multiplied by the gain, formants below the split are
   untouched and the voice does not go dull when reduction ends.

2. Detection envelope - the detector runs on the *filtered* signal.  Band and
   reference (low-band) energies are averaged with ``detector_ms`` and
   compared in dB::

       over      = band_db - max(threshold_db, ref_db + ref_offset_db)
       reduction = min(max_reduction_db, over * (1 - 1 / ratio))

   Reduction therefore needs the band to beat both an absolute threshold and
   the reference energy: a broadband hit lifts the reference just as much and
   does not trigger, while a sibilant has no low-frequency support and does.
   Attack applies while the gain falls, release while it rises; release is
   what lets the high band come back as soon as the sibilant is over.

3. Switching - enable/bypass is a linear cross-fade onto the
   delay-compensated dry signal (``out = dry + mix * (g - 1) * high``), while
   detector and envelope keep running.  State stays continuous, so a switch
   neither steps nor loses the current reduction.  Parameter automation is
   smoothed the same way and time-stamped, so step automation cannot click.

Stereo linking uses one gain from the maximum band and reference energies of
all channels.  Every sample advances the state individually, so any block
partition gives bit-identical output.

Pure Python, no third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import wave
from collections import deque
from operator import mul

__all__ = [
    "DEFAULTS",
    "DeEsser",
    "DeEsserError",
    "FrameDeEsser",
    "band_split",
    "crossover_delay",
    "default_taps",
    "design_lowpass",
    "process_frames",
    "process_audio",
    "run_frame_job",
    "run_stream_job",
    "main",
]

DB_FLOOR = 1e-30        # -300 dB, keeps logs finite on digital silence

#: Processing parameters; each one is automatable.
DEFAULTS = {
    "threshold_db": -34.0,     # absolute in-band level that starts reduction
    "ratio": 4.0,              # reduction slope above the effective threshold
    "ref_offset_db": 5.0,      # band must beat the low band by this much
    "max_reduction_db": 18.0,  # reduction ceiling
    "attack_ms": 0.5,          # envelope attack (gain moving down)
    "release_ms": 60.0,        # envelope release (gain moving up)
    "fade_ms": 8.0,            # enable/bypass cross-fade
    "detector_ms": 1.0,        # band/reference energy averager
    "smooth_ms": 5.0,          # automation smoothing
}

_MINIMUM = {
    "threshold_db": None,
    "ratio": 1.0,
    "ref_offset_db": None,
    "max_reduction_db": 0.0,
    "attack_ms": 0.0,
    "release_ms": 0.0,
    "fade_ms": 0.0,
    "detector_ms": 0.0,
    "smooth_ms": 0.0,
}
_STRICT = {"ratio"}          # ratio must be greater than 1

#: Frame-domain parameters (the documented ``deess.py`` job contract).
FRAME_DEFAULTS = {
    "threshold": 2.0,          # det (high/low energy ratio) that starts reduction
    "ratio": 4.0,              # reduction slope above the threshold
    "max_reduction_db": None,  # optional ceiling; None keeps the plain contract
    "attack_ms": 10.0,         # envelope attack (gain moving down)
    "release_ms": 80.0,        # envelope release (gain moving up)
    "fade_ms": 20.0,           # enable/bypass cross-fade
    "smooth_ms": 0.0,          # automation smoothing (0 = apply next frame)
}

_FRAME_MINIMUM = {
    "threshold": (0.0, False),
    "ratio": (1.0, True),
    "max_reduction_db": (None, False),
    "attack_ms": (0.0, False),
    "release_ms": (0.0, False),
    "fade_ms": (0.0, False),
    "smooth_ms": (0.0, False),
}

_FILTER_KEYS = ("sample_rate", "channels", "split_hz", "taps", "beta",
                "coefficients")


class DeEsserError(ValueError):
    """Invalid configuration, automation or audio data."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _number(value, name, minimum=None, strict=False):
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise DeEsserError("%s must be a number, got %r" % (name, value))
    if not math.isfinite(out):
        raise DeEsserError("%s must be finite, got %r" % (name, value))
    if minimum is not None:
        if strict and out <= minimum:
            raise DeEsserError("%s must be > %g, got %g" % (name, minimum, out))
        if not strict and out < minimum:
            raise DeEsserError("%s must be >= %g, got %g" % (name, minimum, out))
    return out


BASE_TAPS = 257              # 5.35 ms at 48 kHz
BASE_RATE = 48000.0


def _odd_taps(taps):
    if isinstance(taps, bool) or int(taps) != taps:
        raise DeEsserError("taps must be an odd integer, got %r" % (taps,))
    taps = int(taps)
    if taps < 5 or taps % 2 == 0:
        raise DeEsserError("taps must be an odd integer >= 5, got %d" % taps)
    return taps


def default_taps(sample_rate, split_hz=6000.0):
    """Kernel length that keeps the split's transition width rate independent.

    The prototype is specified in *time* (5.35 ms), so the same relative
    transition width is obtained at any sample rate; a fixed tap count would
    widen the crossover at 88.2/96 kHz and narrow it at 44.1 kHz.  The result
    is odd and long enough for the requested cutoff.
    """
    fs = _number(sample_rate, "sample_rate", 0.0, strict=True)
    span = BASE_TAPS * fs / BASE_RATE
    taps = int(round(span))
    if taps % 2 == 0:
        taps += 1
    minimum = int(math.ceil(3.0 * fs / (2.0 * _number(split_hz, "split_hz", 0.0,
                                                       strict=True))))
    if minimum % 2 == 0:
        minimum += 1
    return max(9, taps, minimum)


def _bessel_i0(x):
    """Modified Bessel function I0, evaluated by its power series."""
    total = 1.0
    term = 1.0
    k = 1
    while k < 100:
        term *= (x / (2.0 * k)) ** 2
        total += term
        if term < 1e-18 * total:
            break
        k += 1
    return total


def _one_pole(ms, rate):
    """Per-step coefficient of a one-pole averager with time constant ``ms``.

    ``rate`` is in steps per second (samples or frames).  ``ms <= 0`` yields a
    coefficient of 1, i.e. instant response.
    """
    if ms <= 0.0:
        return 1.0
    return 1.0 - math.exp(-1.0 / (ms * 1e-3 * rate))


def _db(power):
    return 10.0 * math.log10(power + DB_FLOOR)


def _validate_param(name, value):
    if name not in DEFAULTS:
        raise DeEsserError("unknown parameter %r (known: %s)"
                           % (name, ", ".join(sorted(DEFAULTS))))
    return _number(value, name, _MINIMUM[name], name in _STRICT)


# ---------------------------------------------------------------------------
# band split
# ---------------------------------------------------------------------------

def crossover_delay(taps):
    """Latency in samples of a linear-phase ``taps``-point band split."""
    return (_odd_taps(taps) - 1) // 2


def design_lowpass(split_hz, sample_rate, taps=257, beta=8.0):
    """Symmetric, DC-normalised Kaiser-windowed low-pass kernel.

    Linear phase, so the split delays both branches by exactly
    ``(taps - 1) / 2`` samples.  ``beta`` trades transition width for
    stop-band depth; the default gives about -80 dB.
    """
    taps = _odd_taps(taps)
    fs = _number(sample_rate, "sample_rate", 0.0, strict=True)
    fc = _number(split_hz, "split_hz", 0.0, strict=True)
    if fc >= fs / 2.0:
        raise DeEsserError("split_hz (%g) must be below Nyquist (%g)"
                           % (fc, fs / 2.0))
    beta = _number(beta, "beta", 0.0)

    m = taps - 1
    norm = 2.0 * fc / fs
    half = m / 2.0
    win_norm = _bessel_i0(beta)
    coeffs = []
    for i in range(taps):
        k = i - half
        if abs(k) < 1e-12:
            sinc = norm
        else:
            sinc = math.sin(math.pi * norm * k) / (math.pi * k)
        r = 2.0 * i / m - 1.0
        window = _bessel_i0(beta * math.sqrt(max(0.0, 1.0 - r * r))) / win_norm
        coeffs.append(sinc * window)

    dc = sum(coeffs)
    if abs(dc) < 1e-12:
        raise DeEsserError("low-pass design produced zero DC gain")
    return tuple(c / dc for c in coeffs)


def _check_symmetric(coeffs, what):
    if len(coeffs) < 5 or len(coeffs) % 2 == 0:
        raise DeEsserError("%s must have an odd length >= 5" % what)
    if not all(math.isfinite(c) for c in coeffs):
        raise DeEsserError("%s must be finite" % what)
    peak = max(abs(c) for c in coeffs)
    if peak == 0.0:
        raise DeEsserError("%s are all zero" % what)
    half = len(coeffs) // 2
    for i in range(half):
        if abs(coeffs[i] - coeffs[-1 - i]) > 1e-6 * peak:
            raise DeEsserError("%s must be symmetric about the centre" % what)
    return peak


def normalise_lowpass(coefficients):
    """Validate a caller-supplied filter and return the low-pass prototype.

    Accepted forms (the "task package" coefficients):

    * a sequence - interpreted by its DC/Nyquist gains: a kernel that passes
      DC is taken as the low-pass prototype (DC-normalised); a kernel that
      passes Nyquist and blocks DC is converted to the exactly complementary
      low-pass ``lp = unit[D] - hp``, so the supplied numbers still define the
      split;
    * ``{"lowpass": seq}`` / ``{"lp": seq}`` - the low-pass kernel;
    * ``{"highpass": seq}`` / ``{"hp": seq}`` - the high-pass branch, as above.

    Either way the pair used at runtime is ``(lowpass, unit[D] - lowpass)``,
    which is delay matched and complementary by construction.
    """
    if isinstance(coefficients, dict):
        for key in ("lowpass", "lp", "low"):
            if key in coefficients:
                return normalise_lowpass(coefficients[key])
        for key in ("highpass", "hp", "high"):
            if key in coefficients:
                return normalise_lowpass(_complement(coefficients[key]))
        raise DeEsserError("coefficients mapping needs 'lowpass' or 'highpass'")
    if isinstance(coefficients, (str, bytes)):
        raise DeEsserError("coefficients must be a sequence of numbers")
    try:
        coeffs = [float(c) for c in coefficients]
    except (TypeError, ValueError):
        raise DeEsserError("coefficients must be a sequence of numbers")
    peak = _check_symmetric(coeffs, "coefficients")

    dc = sum(coeffs)
    nyquist = sum(c if i % 2 == 0 else -c for i, c in enumerate(coeffs))
    if abs(dc) < abs(nyquist):
        # Passes Nyquist but not DC: a high-pass branch - complement it.
        return normalise_lowpass(_complement(coeffs))
    if abs(dc) < 1e-6 * peak:
        raise DeEsserError(
            "coefficients pass neither DC nor Nyquist; supply the low-pass "
            "prototype, or {'highpass': ...} for the high-pass branch")
    return tuple(c / dc for c in coeffs)


def _complement(coefficients):
    """Exactly complementary branch: ``unit[D] - coefficients``."""
    try:
        coeffs = [float(c) for c in coefficients]
    except (TypeError, ValueError):
        raise DeEsserError("coefficients must be a sequence of numbers")
    _check_symmetric(coeffs, "coefficients")
    delay = (len(coeffs) - 1) // 2
    out = [-c for c in coeffs]
    out[delay] += 1.0
    return out


class BandSplit:
    """Complementary linear-phase low/high pair for interleaved channels.

    ``low + high`` equals the input delayed by ``delay`` samples, exactly.
    """

    __slots__ = ("coeffs", "delay", "channels", "_hist")

    def __init__(self, coeffs, channels=1):
        self.coeffs = tuple(coeffs)
        self.delay = (len(self.coeffs) - 1) // 2
        if int(channels) != channels or channels < 1:
            raise DeEsserError("channels must be a positive integer")
        self.channels = int(channels)
        self._hist = [deque([0.0] * len(self.coeffs), maxlen=len(self.coeffs))
                      for _ in range(self.channels)]

    def process(self, frame):
        """Return ``(low, high, dry)`` tuples; ``low + high == dry``."""
        low = []
        high = []
        dry = []
        delay = self.delay
        for ch in range(self.channels):
            hist = self._hist[ch]
            hist.appendleft(frame[ch])
            lp = sum(map(mul, self.coeffs, hist))
            d = hist[delay]
            low.append(lp)
            high.append(d - lp)
            dry.append(d)
        return tuple(low), tuple(high), tuple(dry)

    def clear(self):
        for hist in self._hist:
            hist.extend([0.0] * len(hist))


def band_split(frames, split_hz=6000.0, sample_rate=48000, taps=None,
               beta=8.0, coefficients=None):
    """Split frames into latency-aligned ``(low, high)`` band signals."""
    if coefficients is None:
        if taps is None:
            taps = default_taps(sample_rate, split_hz)
        kernel = design_lowpass(split_hz, sample_rate, taps, beta)
    else:
        kernel = normalise_lowpass(coefficients)
    split = BandSplit(kernel)
    low_out, high_out = [], []
    for frame in frames:
        low, high, _ = split.process(frame if isinstance(frame, (tuple, list))
                                     else (frame,))
        low_out.append(low)
        high_out.append(high)
    return low_out, high_out


# ---------------------------------------------------------------------------
# streaming processor
# ---------------------------------------------------------------------------

class DeEsser:
    """Streaming, stereo-linked band-split de-esser.

    Parameters
    ----------
    sample_rate, channels:
        Stream format; ``channels`` is 1 or 2 (more is accepted for testing).
    split_hz, taps, beta, coefficients:
        Band split.  ``taps=None`` (default) scales the kernel with the sample
        rate to keep the crossover width constant; an integer pins it.
        ``coefficients`` replaces the designed kernel with the caller's
        ("task package") numbers; see :func:`normalise_lowpass`.
    enabled:
        Initial routing state.
    **params:
        Any key of :data:`DEFAULTS`.

    Latency is :attr:`latency_samples` = ``(taps - 1) / 2``.
    """

    def __init__(self, sample_rate=48000, channels=1, split_hz=6000.0,
                 taps=None, beta=8.0, coefficients=None, enabled=True,
                 **params):
        self.sample_rate = _number(sample_rate, "sample_rate", 0.0, strict=True)
        if isinstance(channels, bool) or int(channels) != channels or channels < 1:
            raise DeEsserError("channels must be a positive integer, got %r"
                               % (channels,))
        self.channels = int(channels)
        self.split_hz = _number(split_hz, "split_hz", 0.0, strict=True)
        self.beta = _number(beta, "beta", 0.0)

        if coefficients is not None:
            kernel = normalise_lowpass(coefficients)
            self.taps = len(kernel)
        else:
            self.taps = (_odd_taps(taps) if taps is not None
                         else default_taps(self.sample_rate, self.split_hz))
            kernel = design_lowpass(self.split_hz, self.sample_rate,
                                    self.taps, self.beta)
        self.coefficients = kernel
        self._split = BandSplit(kernel, self.channels)

        unknown = sorted(set(params) - set(DEFAULTS))
        if unknown:
            raise DeEsserError("unknown parameter(s): %s (known: %s)"
                               % (", ".join(unknown),
                                  ", ".join(sorted(DEFAULTS))))
        self._params = dict(DEFAULTS)
        for name, value in params.items():
            self._params[name] = _validate_param(name, value)
        self._targets = dict(self._params)
        self._pending = set()

        self._band_energy = [0.0] * self.channels
        self._ref_energy = [0.0] * self.channels
        self._gain = 1.0
        self._n = 0
        self._events = []

        self._enabled = bool(enabled)
        self._mix = 1.0 if self._enabled else 0.0
        self._mix_target = self._mix

        self._meter_every = 0
        self._meter = []
        self._last = {}
        self._refresh_time_constants()

    # -- configuration -----------------------------------------------------

    def _refresh_time_constants(self):
        fs = self.sample_rate
        p = self._params
        self._detector_coef = _one_pole(p["detector_ms"], fs)
        self._param_coef = _one_pole(p["smooth_ms"], fs)
        self._attack_coef = _one_pole(p["attack_ms"], fs)
        self._release_coef = _one_pole(p["release_ms"], fs)
        fade = p["fade_ms"]
        self._mix_step = 0.0 if fade <= 0.0 else 1.0 / (fade * 1e-3 * fs)

    @property
    def latency_samples(self):
        """Total latency in samples (the split is the only delay)."""
        return self._split.delay

    @property
    def latency_ms(self):
        return 1e3 * self.latency_samples / self.sample_rate

    @property
    def enabled(self):
        return self._enabled

    @property
    def gain(self):
        return self._gain

    def parameter(self, name):
        return self._params[name]

    def parameters(self):
        return dict(self._params)

    def set_param(self, name, value, at_time=None):
        """Set a parameter now, or at an absolute time in seconds.

        Moves are smoothed over ``smooth_ms``, so step automation cannot
        click.  Scheduling into the past is rejected.
        """
        value = _validate_param(name, value)
        if at_time is None:
            self._targets[name] = value
            self._pending.add(name)
            return
        sample = self._event_sample(at_time)
        self._events.append((sample, len(self._events), "param", name, value))
        self._events.sort(key=lambda item: item[:2])

    def set_enabled(self, enabled, at_time=None):
        """Enable or bypass now, or at an absolute time in seconds."""
        enabled = bool(enabled)
        if at_time is None:
            self._enabled = enabled
            self._mix_target = 1.0 if enabled else 0.0
            return
        sample = self._event_sample(at_time)
        self._events.append((sample, len(self._events), "enabled", None, enabled))
        self._events.sort(key=lambda item: item[:2])

    def _event_sample(self, at_time):
        t = _number(at_time, "time")
        if t < 0.0:
            raise DeEsserError("time must be >= 0, got %g" % t)
        sample = int(round(t * self.sample_rate))
        if sample < self._n:
            raise DeEsserError(
                "cannot schedule at %.6fs: processing is already at %.6fs"
                % (t, self._n / self.sample_rate))
        return sample

    # -- processing --------------------------------------------------------

    def process_block(self, frames):
        """Process a block; returns frames of exactly the same shape.

        Frames are per-sample tuples; mono samples may be plain numbers.  The
        result does not depend on how the stream is split into blocks.
        """
        if isinstance(frames, (str, bytes, bytearray)):
            raise DeEsserError("audio block must be a sequence of frames")
        channels = self.channels
        out = []
        append = out.append
        for frame in frames:
            if not isinstance(frame, (tuple, list)):
                frame = (frame,)
            try:
                values = tuple(float(v) for v in frame)
            except (TypeError, ValueError):
                raise DeEsserError("audio samples must be numbers")
            if len(values) != channels:
                raise DeEsserError("expected %d channel(s), got %d"
                                   % (channels, len(values)))
            if not all(math.isfinite(v) for v in values):
                raise DeEsserError("audio samples must be finite")
            append(self._step(values))
        return out

    def process_mono(self, samples):
        """Process a mono stream; returns a list of floats."""
        if self.channels != 1:
            raise DeEsserError("process_mono needs channels == 1")
        return [frame[0] for frame in self.process_block(samples)]

    def _apply_events(self):
        while self._events and self._events[0][0] <= self._n:
            _, _, kind, name, value = self._events.pop(0)
            if kind == "param":
                self._targets[name] = value
                self._pending.add(name)
            else:
                self._enabled = bool(value)
                self._mix_target = 1.0 if value else 0.0

    def _smooth_parameters(self):
        if not self._pending:
            return
        coef = self._param_coef
        for name in list(self._pending):
            current = self._params[name]
            target = self._targets[name]
            if current == target:
                self._pending.discard(name)
                continue
            nxt = current + (target - current) * coef
            if abs(target - nxt) <= 1e-12 * max(1.0, abs(target)):
                nxt = target
                self._pending.discard(name)
            self._params[name] = nxt
        self._refresh_time_constants()

    def _detect(self, low, high):
        """Update band/reference energies and return ``(band_db, ref_db)``.

        The two values come from the *same* channel: the reference must
        describe the low band that accompanies the triggering high band, so
        a loud bass in one channel cannot mask a sibilant in the other.  The
        channel pair with the highest band energy wins, which is what "linked"
        means here - one gain for all channels.
        """
        k = self._detector_coef
        best = None
        for ch in range(self.channels):
            h = high[ch]
            l = low[ch]
            e = self._band_energy[ch] + (h * h - self._band_energy[ch]) * k
            r = self._ref_energy[ch] + (l * l - self._ref_energy[ch]) * k
            self._band_energy[ch] = e
            self._ref_energy[ch] = r
            if best is None or e > best[0]:
                best = (e, r)
        return _db(best[0]), _db(best[1])

    def _target_gain(self, band_db, ref_db):
        p = self._params
        threshold = max(p["threshold_db"], ref_db + p["ref_offset_db"])
        over = band_db - threshold
        if over <= 0.0:
            return 1.0
        reduction = min(p["max_reduction_db"], over * (1.0 - 1.0 / p["ratio"]))
        return 10.0 ** (-reduction / 20.0)

    def _step(self, frame):
        self._apply_events()
        self._smooth_parameters()

        low, high, dry = self._split.process(frame)
        band_db, ref_db = self._detect(low, high)

        target = self._target_gain(band_db, ref_db)
        gain = self._gain
        coef = self._attack_coef if target < gain else self._release_coef
        gain += (target - gain) * coef
        self._gain = min(1.0, max(0.0, gain))

        mix = self._mix
        if mix != self._mix_target:
            step = self._mix_step
            if step <= 0.0:
                mix = self._mix_target
            elif mix < self._mix_target:
                mix = min(self._mix_target, mix + step)
            else:
                mix = max(self._mix_target, mix - step)
            self._mix = mix

        # dry + mix*(g-1)*high cross-fades between the delay-compensated dry
        # signal and the processed one without ever touching the low band.
        scale = mix * (self._gain - 1.0)
        out = tuple(dry[ch] + high[ch] * scale for ch in range(self.channels))

        gain_db = 20.0 * math.log10(max(self._gain, 1e-12))
        self._last = {"band_db": band_db, "ref_db": ref_db,
                      "gain_db": gain_db, "mix": self._mix}
        if self._meter_every and self._n % self._meter_every == 0:
            self._meter.append({
                "t": round(self._n / self.sample_rate, 6),
                "band_db": round(band_db, 3),
                "ref_db": round(ref_db, 3),
                "gain_db": round(gain_db, 4),
                "mix": round(self._mix, 4),
            })
        self._n += 1
        return out

    # -- diagnostics -------------------------------------------------------

    @property
    def last(self):
        """Most recent level report: band_db, ref_db, gain_db, mix."""
        return dict(self._last)

    def state(self):
        """Snapshot of the continuous state (tests, debugging, host display)."""
        return {
            "sample": self._n,
            "latency_samples": self.latency_samples,
            "gain": self._gain,
            "gain_db": 20.0 * math.log10(max(self._gain, 1e-12)),
            "mix": self._mix,
            "enabled": self._enabled,
            "band_energy": list(self._band_energy),
            "ref_energy": list(self._ref_energy),
            "parameters": dict(self._params),
            "targets": dict(self._targets),
        }

    def set_meter(self, interval_ms=10.0):
        """Log a level report every ``interval_ms`` while processing."""
        if not interval_ms or interval_ms <= 0:
            self._meter_every = 0
        else:
            self._meter_every = max(
                1, int(round(interval_ms * 1e-3 * self.sample_rate)))

    @property
    def meter(self):
        return list(self._meter)

    def clear_meter(self):
        self._meter = []

    def reset(self):
        """Clear filters and analysis state, keeping parameters and routing."""
        self._split.clear()
        self._band_energy = [0.0] * self.channels
        self._ref_energy = [0.0] * self.channels
        self._gain = 1.0
        self._n = 0
        self._events = []
        self._mix = 1.0 if self._enabled else 0.0
        self._mix_target = self._mix


def process_audio(audio, block_size=None, meter_ms=None, **spec):
    """Process a whole signal in one call; returns ``(frames, DeEsser)``."""
    deesser = DeEsser(**spec)
    if meter_ms:
        deesser.set_meter(meter_ms)
    if not block_size:
        return deesser.process_block(audio), deesser
    out = []
    for start in range(0, len(audio), block_size):
        out.extend(deesser.process_block(audio[start:start + block_size]))
    return out, deesser


# ---------------------------------------------------------------------------
# frame-domain processor: the original documented job contract
# ---------------------------------------------------------------------------

class FrameDeEsser:
    """Frame-domain de-esser for the documented ``deess.py`` JSON format.

    Frames are ``frame_ms`` long (10 ms in the original contract) and carry
    pre-computed band energies ``low``/``high``.  The documented detector and
    gain computer are kept verbatim:

    * ``det = high / max(low, 1e-9)``; 0 when both bands are silent;
    * ``over = det - threshold`` when ``det > threshold``, else 0;
    * ``want = max(1 / ratio, 1 - over * ratio)``;
    * attack while ``want < gain``, release while ``want > gain``, with the
      per-frame coefficient ``1 - exp(-frame_ms / time_ms)`` so the timing is
      independent of how the frames are grouped into blocks.

    ``threshold`` lives in the detector domain (the band energy ratio the
    original contract reports), so a frame where both bands are zero never
    triggers.  ``max_reduction_db`` caps the reduction, ``fade_ms`` ramps
    enable/bypass, and ``smooth_ms`` smooths automation steps; all of them
    keep the envelope continuous and the reported gain click-free.
    """

    def __init__(self, frame_ms=10.0, enabled=True, **params):
        self.frame_ms = _number(frame_ms, "frame_ms", 0.0, strict=True)
        self._rate = 1e3 / self.frame_ms       # frames per second
        unknown = sorted(set(params) - set(FRAME_DEFAULTS))
        if unknown:
            raise DeEsserError("unknown parameter(s): %s (known: %s)"
                               % (", ".join(unknown),
                                  ", ".join(sorted(FRAME_DEFAULTS))))
        self._params = dict(FRAME_DEFAULTS)
        for name, value in params.items():
            self._params[name] = self._validate(name, value)
        self._targets = dict(self._params)
        self._pending = set()

        self._gain = 1.0
        self._enabled = bool(enabled)
        self._mix = 1.0 if self._enabled else 0.0
        self._mix_target = self._mix
        self._n = 0
        self._events = []
        self._last = {}

    # -- control -----------------------------------------------------------

    def _coef(self, ms):
        return _one_pole(ms, self._rate)

    @staticmethod
    def _validate(name, value):
        if name not in FRAME_DEFAULTS:
            raise DeEsserError("unknown parameter %r (known: %s)"
                               % (name, ", ".join(sorted(FRAME_DEFAULTS))))
        minimum, strict = _FRAME_MINIMUM[name]
        if minimum is None:
            if value is None:
                return None
            return _number(value, name)
        return _number(value, name, minimum, strict)

    def set_param(self, name, value, at_frame=None):
        """Set a parameter now, or starting at a given frame index."""
        value = self._validate(name, value)
        if at_frame is None:
            self._targets[name] = value
            self._pending.add(name)
            return
        frame = self._frame_index(at_frame)
        self._events.append((frame, len(self._events), "param", name, value))
        self._events.sort(key=lambda item: item[:2])

    def set_enabled(self, enabled, at_frame=None):
        """Enable or bypass now, or starting at a given frame index."""
        enabled = bool(enabled)
        if at_frame is None:
            self._enabled = enabled
            self._mix_target = 1.0 if enabled else 0.0
            return
        frame = self._frame_index(at_frame)
        self._events.append((frame, len(self._events), "enabled", None, enabled))
        self._events.sort(key=lambda item: item[:2])

    def _frame_index(self, at_frame):
        try:
            frame = int(at_frame)
        except (TypeError, ValueError):
            raise DeEsserError("at_frame must be an integer frame index")
        if frame < self._n:
            raise DeEsserError("cannot schedule at frame %d: already at %d"
                               % (frame, self._n))
        return frame

    def _apply_events(self):
        while self._events and self._events[0][0] <= self._n:
            _, _, kind, name, value = self._events.pop(0)
            if kind == "param":
                self._targets[name] = value
                self._pending.add(name)
            else:
                self._enabled = bool(value)
                self._mix_target = 1.0 if value else 0.0

    def _smooth_parameters(self):
        if not self._pending:
            return
        coef = self._coef(self._params["smooth_ms"])
        for name in list(self._pending):
            current = self._params[name]
            target = self._targets[name]
            if current == target:
                self._pending.discard(name)
                continue
            nxt = current + (target - current) * coef
            if abs(target - nxt) <= 1e-12 * max(1.0, abs(target)):
                nxt = target
                self._pending.discard(name)
            self._params[name] = nxt

    # -- processing --------------------------------------------------------

    def process(self, frame):
        """Process one frame; returns ``(det, gain, mix)``.

        ``frame`` is a mapping with ``high``/``low`` energies.  Both zero
        reports ``det = 0``; missing values count as zero.
        """
        if not isinstance(frame, dict):
            raise DeEsserError("frame must be a mapping with 'high'/'low'")
        self._apply_events()
        self._smooth_parameters()

        high = _number(frame.get("high", 0.0), "high", 0.0)
        low = _number(frame.get("low", 0.0), "low", 0.0)
        if high == 0.0 and low == 0.0:
            det = 0.0
        else:
            det = high / max(low, 1e-9)

        p = self._params
        over = det - p["threshold"] if det > p["threshold"] else 0.0
        want = min(1.0, max(1.0 / p["ratio"], 1.0 - over * p["ratio"]))
        if p["max_reduction_db"] is not None:
            floor = 10.0 ** (-p["max_reduction_db"] / 20.0)
            if want < floor:
                want = floor

        # Attack while the gain falls, release while it rises.  The original
        # used the attack coefficient in both directions, which kept the high
        # band down long after the sibilant was gone.
        gain = self._gain
        coef = self._coef(p["attack_ms"]) if want < gain else self._coef(p["release_ms"])
        gain += (want - gain) * coef
        self._gain = min(1.0, max(0.0, gain))

        mix = self._mix
        if mix != self._mix_target:
            step = self._coef(p["fade_ms"])
            if step <= 0.0:
                mix = self._mix_target
            elif mix < self._mix_target:
                mix = min(self._mix_target, mix + step)
            else:
                mix = max(self._mix_target, mix - step)
            self._mix = mix

        self._last = {"det": det, "want": want, "gain": self._gain, "mix": mix}
        self._n += 1
        return det, self._gain, mix

    @property
    def gain(self):
        return self._gain

    @property
    def last(self):
        return dict(self._last)

    def state(self):
        return {
            "frame": self._n,
            "gain": self._gain,
            "gain_db": 20.0 * math.log10(max(self._gain, 1e-12)),
            "mix": self._mix,
            "enabled": self._enabled,
            "parameters": dict(self._params),
            "targets": dict(self._targets),
        }

    def reset(self):
        self._gain = 1.0
        self._n = 0
        self._events = []
        self._pending = set()
        self._mix = 1.0 if self._enabled else 0.0
        self._mix_target = self._mix


def process_frames(frames, frame_ms=10.0, enabled=True, events=None, **params):
    """Process a list of ``{"high","low"}`` frames in one call.

    Returns ``(rows, active_frames)`` where each row is ``{"t","det","gain"}``
    with ``det`` at 3 and ``gain`` at 4 decimals - the documented output.
    ``events`` is an optional list of ``{"param"/"enabled", "value",
    "frame"}`` automation steps.
    """
    detector = FrameDeEsser(frame_ms=frame_ms, enabled=enabled, **params)
    for event in events or []:
        if "enabled" in event:
            detector.set_enabled(event["enabled"], at_frame=event.get("frame"))
        else:
            detector.set_param(event["param"], event["value"],
                               at_frame=event.get("frame"))
    rows = []
    active = 0
    for index, frame in enumerate(frames):
        det, gain, mix = detector.process(frame)
        # The reported gain includes the bypass ramp, so a switch shows up as
        # a ramp rather than a jump (the original reported the raw gain).
        gain_out = 1.0 + mix * (gain - 1.0)
        if gain_out < 0.99:
            active += 1
        t = frame.get("t", index * frame_ms)
        rows.append({"t": t, "det": round(det, 3), "gain": round(gain_out, 4)})
    return rows, active


# ---------------------------------------------------------------------------
# JSON and WAV front ends
# ---------------------------------------------------------------------------

def run_frame_job(job):
    """Run a legacy frame job and return the documented result object.

    The original keys ``threshold``, ``ratio``, ``attack`` and ``release`` are
    read from the top level (or from ``params``); ``threshold`` stays in the
    detector domain (band energy ratio).
    """
    if not isinstance(job, dict):
        raise DeEsserError("job must be a JSON object")
    frames = job.get("frames")
    if not isinstance(frames, list):
        raise DeEsserError("job.frames must be a list")

    params = dict(job.get("params") or {})
    for key in ("threshold", "ratio", "attack", "release"):
        if key in job:
            if key in params:
                raise DeEsserError("specify %r once (top level or params)" % key)
            params[key] = job[key]
    aliases = {"attack": "attack_ms", "release": "release_ms"}
    for old, new in aliases.items():
        if old in params:
            if new in params:
                raise DeEsserError("specify either %r or %r, not both"
                                   % (old, new))
            params[new] = params.pop(old)
    frame_ms = params.pop("frame_ms", job.get("frame_ms", 10.0))
    enabled = params.pop("enabled", job.get("enabled", True))

    events = []
    for event in job.get("automation") or []:
        events.append({"param": event["param"], "value": event["value"],
                       "frame": event.get("t")})
    for event in job.get("bypass") or []:
        events.append({"enabled": event["enabled"], "frame": event.get("t")})

    rows, active = process_frames(frames, frame_ms=frame_ms, enabled=enabled,
                                  events=events, **params)
    return {"frames": rows, "active_frames": active}


def run_stream_job(job):
    """Run a signal-domain job (``job["audio"]``) and return the result."""
    if not isinstance(job, dict):
        raise DeEsserError("job must be a JSON object")
    audio = job.get("audio")
    if not isinstance(audio, list):
        raise DeEsserError("job.audio must be a list of frames")

    block = job.get("block_size", 64)
    if isinstance(block, bool) or int(block) != block or block <= 0:
        raise DeEsserError("block_size must be a positive integer")
    block = int(block)

    spec = dict(job.get("params") or {})
    for key in _FILTER_KEYS:
        if key in job and key not in spec:
            spec[key] = job[key]
    bad = sorted(set(spec) - set(DEFAULTS) - set(_FILTER_KEYS) - {"enabled"})
    if bad:
        raise DeEsserError("unknown job key(s): %s" % ", ".join(bad))
    spec.setdefault("enabled", job.get("enabled", True))

    deesser = DeEsser(**spec)
    if job.get("meter_ms"):
        deesser.set_meter(float(job["meter_ms"]))
    for event in job.get("automation") or []:
        deesser.set_param(event["param"], event["value"], event.get("t"))
    for event in job.get("bypass") or []:
        deesser.set_enabled(event["enabled"], event.get("t"))

    frames = []
    for start in range(0, len(audio), block):
        frames.extend(deesser.process_block(audio[start:start + block]))

    return {
        "sample_rate": int(deesser.sample_rate),
        "channels": deesser.channels,
        "split_hz": deesser.split_hz,
        "taps": deesser.taps,
        "latency_samples": deesser.latency_samples,
        "block_size": block,
        "parameters": {key: round(value, 6)
                       for key, value in deesser.parameters().items()},
        "frames": frames,
        "meter": deesser.meter,
    }


def _read_wav(path):
    with wave.open(path, "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if channels not in (1, 2):
        raise DeEsserError("only mono/stereo WAV is supported, got %d channels"
                           % channels)
    if width not in (1, 2, 3, 4):
        raise DeEsserError("unsupported WAV sample width: %d bytes" % width)
    scale = float(1 << (8 * width - 1))
    step = width * channels
    frames = []
    for pos in range(0, len(raw) - step + 1, step):
        values = []
        for ch in range(channels):
            chunk = raw[pos + ch * width: pos + (ch + 1) * width]
            if width == 1:
                values.append((chunk[0] - 128) / scale)
            else:
                values.append(int.from_bytes(chunk, "little", signed=True) / scale)
        frames.append(tuple(values))
    return rate, channels, width, frames


def _write_wav(path, rate, channels, width, frames):
    scale = float(1 << (8 * width - 1))
    limit = (1 << (8 * width - 1)) - 1
    chunks = []
    for frame in frames:
        for value in frame:
            sample = int(round(max(-1.0, min(1.0, value)) * scale))
            sample = max(-limit - 1, min(limit, sample))
            if width == 1:
                chunks.append(bytes((sample + 128,)))
            else:
                chunks.append(int(sample).to_bytes(width, "little", signed=True))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(b"".join(chunks))


def main(argv=None):
    """``deess.py input.json output.json`` (or ``.wav`` in / ``.wav`` out)."""
    parser = argparse.ArgumentParser(
        description="Band-split de-esser (frame JSON job, audio JSON job, "
                    "or PCM WAV in/out).")
    parser.add_argument("input", help="job JSON or input .wav")
    parser.add_argument("output", help="result JSON or output .wav")
    parser.add_argument("--block-size", type=int, default=None,
                        help="processing block size (output is block invariant)")
    parser.add_argument("--meter-ms", type=float, default=10.0,
                        help="level report interval in ms (0 disables)")
    parser.add_argument("--split-hz", type=float)
    parser.add_argument("--taps", type=int)
    parser.add_argument("--beta", type=float)
    parser.add_argument("--coefficients", metavar="FILE.json",
                        help="JSON list of the low-pass prototype, or an object "
                             "with a 'lowpass'/'highpass' entry")
    for name in DEFAULTS:
        parser.add_argument("--" + name.replace("_", "-"), dest=name, type=float)
    args = parser.parse_args(argv)

    overrides = {}
    for key in ("split_hz", "taps", "beta"):
        value = getattr(args, key)
        if value is not None:
            overrides[key] = value
    if args.coefficients:
        try:
            with open(args.coefficients, "r", encoding="utf-8") as handle:
                overrides["coefficients"] = json.load(handle)
        except (OSError, ValueError) as exc:
            sys.stderr.write("error: cannot read coefficients: %s\n" % exc)
            return 2
    for name in DEFAULTS:
        value = getattr(args, name)
        if value is not None:
            overrides[name] = value

    try:
        if args.input.lower().endswith(".wav"):
            rate, channels, width, audio = _read_wav(args.input)
            spec = dict(overrides)
            spec.setdefault("sample_rate", rate)
            spec.setdefault("channels", channels)
            deesser = DeEsser(**spec)
            if args.meter_ms:
                deesser.set_meter(args.meter_ms)
            out = []
            step = max(1, args.block_size or 512)
            for start in range(0, len(audio), step):
                out.extend(deesser.process_block(audio[start:start + step]))
            _write_wav(args.output, rate, channels, width, out)
            reductions = [m["gain_db"] for m in deesser.meter]
            sys.stderr.write(
                "de-essed %d frames, %d ch, latency %d samples (%.2f ms), "
                "max reduction %.1f dB\n"
                % (len(audio), channels, deesser.latency_samples,
                   deesser.latency_ms,
                   min(reductions) if reductions else 0.0))
        else:
            with open(args.input, "r", encoding="utf-8") as handle:
                job = json.load(handle)
            if "frames" in job and "audio" not in job:
                if overrides:
                    job.setdefault("params", {}).update(overrides)
                result = run_frame_job(job)
            else:
                job.setdefault("params", {}).update(overrides)
                if args.block_size:
                    job["block_size"] = args.block_size
                if args.meter_ms:
                    job["meter_ms"] = args.meter_ms
                result = run_stream_job(job)
            with open(args.output, "w", encoding="utf-8") as handle:
                json.dump(result, handle, indent=1)
                handle.write("\n")
    except (DeEsserError, OSError, ValueError, KeyError, TypeError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
