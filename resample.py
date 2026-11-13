"""Streaming, rational-ratio PCM sample-rate converter.

The converter keeps the rational filter phase and the input history across
calls to :meth:`Converter.push`.  A whole-stream, non-streaming reference
implementation is provided by :func:`reference_resample` (also exported as
``reference``) so that callers can verify the streaming result without
involving the streaming state machine.
"""
import argparse
import json
import math
from pathlib import Path

# ---------------------------------------------------------------------------
# Filter definition (also documented in README.md)
# ---------------------------------------------------------------------------

R = 80
BETA = 8.6

# Each cached phase holds up to 2*R+1 coefficients.  Bounding the number of
# phases keeps memory predictable for rate pairs with a large reduced
# denominator (for example two unrelated prime rates).
_MAX_PHASE_CACHE = 4096


def _i0(x):
    """Modified Bessel function I0(x), evaluated with its power series."""
    half_squared = (x / 2.0) * (x / 2.0)
    term = 1.0
    total = 1.0
    k = 1
    while True:
        term *= half_squared / (k * k)
        total += term
        if term <= total * 1.0e-17:
            break
        k += 1
        if k > 10000:  # safety valve; unreachable for x <= BETA
            break
    return total


_I0_BETA = _i0(BETA)


def _sinc(x):
    if x == 0.0:
        return 1.0
    return math.sin(math.pi * x) / (math.pi * x)


def kernel(d, fc):
    """The continuous low-pass kernel evaluated at distance ``d``.

    ``d`` is measured in source-sample units; ``fc`` is in cycles per source
    sample.  The support is ``|d| <= R``.
    """
    ad = abs(d)
    if ad > R:
        return 0.0
    argument = 1.0 - (d / R) * (d / R)
    if argument < 0.0:
        argument = 0.0
    return 2.0 * fc * _sinc(2.0 * fc * d) * _i0(BETA * math.sqrt(argument)) / _I0_BETA


def _reduced_ratio(in_rate, out_rate):
    if type(in_rate) is not int or type(out_rate) is not int:
        raise ValueError("invalid sample rate")
    if in_rate < 1 or out_rate < 1:
        raise ValueError("invalid sample rate")
    gcd = math.gcd(in_rate, out_rate)
    return out_rate // gcd, in_rate // gcd  # P, Q


def _output_count(n, p, q):
    # ceil(n * p / q), using integers only.
    return (n * p + q - 1) // q


def output_length(in_rate, out_rate, source_frames):
    """Number of output frames for ``source_frames`` source frames.

    This is ``ceil(N*P/Q)`` for the reduced ratio.  It is exposed so callers
    can check truncation independently of the streaming state.
    """
    if type(source_frames) is not int or source_frames < 0:
        raise ValueError("invalid frame count")
    p, q = _reduced_ratio(in_rate, out_rate)
    return _output_count(source_frames, p, q)


def _validate_config(in_rate, out_rate, channels):
    if type(in_rate) is not int or type(out_rate) is not int:
        raise ValueError("invalid sample rate")
    if in_rate < 1 or out_rate < 1:
        raise ValueError("invalid sample rate")
    if type(channels) is not int or not 1 <= channels <= 8:
        raise ValueError("invalid channel count")


def _validate_frames(frames, channels):
    """Validate a block of interleaved frames and return a private copy.

    Validation is finished before the caller changes any state, so a failed
    push never consumes part of a block.
    """
    if isinstance(frames, (str, bytes)) or not hasattr(frames, "__iter__"):
        raise ValueError("frames must be an array of channel frames")
    checked = []
    for frame in frames:
        if isinstance(frame, (str, bytes)) or not hasattr(frame, "__len__"):
            raise ValueError("invalid sample frame")
        if len(frame) != channels:
            raise ValueError("invalid sample frame")
        values = []
        for value in frame:
            if type(value) is bool or not isinstance(value, (int, float)):
                raise ValueError("invalid sample frame")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("invalid sample frame")
            values.append(value)
        checked.append(tuple(values))
    return checked


class Converter:
    """A stateful rational-ratio polyphase resampler.

    ``push`` returns output frames which have become computable; ``finish``
    flushes the zero-padded tail of the current segment; ``reset`` discards
    an unfinished tail and restarts the segment at source sample zero.

    Time base: the m-th output of a segment is at source time ``m*Q/P`` and
    there are ``ceil(N*P/Q)`` outputs for ``N`` source frames.  The linear
    phase filter delay only changes when an output is returned, never its
    index.  Input outside ``[0, N)`` is treated as zero.
    """

    def __init__(self, in_rate, out_rate, channels):
        _validate_config(in_rate, out_rate, channels)
        self.in_rate = in_rate
        self.out_rate = out_rate
        self.channels = channels
        self.p, self.q = _reduced_ratio(in_rate, out_rate)
        self.fc = 0.45 * min(1.0, self.p / self.q)

        # Coefficient cache keyed by the fractional phase numerator.  This is
        # the rational-phase reuse of the streaming design.
        self._phase_taps = {}

        # Cumulative diagnostics (reset does not clear these).
        self._total_input_frames = 0
        self._total_output_frames = 0
        self._segment_index = 0

        # State of the current segment.
        self._input_count = 0     # N in the current segment
        self._output_index = 0    # m of the next output of this segment
        self._output_count = 0    # outputs returned in this segment
        self._buffer = []
        self._buffer_start = 0    # source index of self._buffer[0]
        self._finished = False
        self._finish_calls = 0
        self._started = False

        self._zero_frame_cache = (0.0,) * channels

    # -- diagnostics -----------------------------------------------------

    def _fractional_phase(self):
        """Numerator/denominator of the phase of the next output sample."""
        return (self._output_index * self.q) % self.p, self.p

    def status(self):
        phase_num, phase_den = self._fractional_phase()
        return {
            # Contractual value: source frames actually held after return.
            "buffered_frames": len(self._buffer),
            "buffered_source_frames": len(self._buffer),
            # Cumulative counts since construction.
            "input_count": self._total_input_frames,
            "output_count": self._total_output_frames,
            "input_frames": self._total_input_frames,
            "output_frames": self._total_output_frames,
            # Current-segment counts and position.
            "segment_input_frames": self._input_count,
            "segment_output_frames": self._output_count,
            "segment_index": self._segment_index,
            "next_output_index": self._output_index,
            "fractional_phase": phase_num / phase_den,
            "phase": phase_num / phase_den,
            "phase_numerator": phase_num,
            "phase_denominator": phase_den,
            "finished": self._finished,
            "finish_calls": self._finish_calls,
        }

    # -- coefficient generation ------------------------------------------

    def _taps_for_phase(self, phase_num):
        """Taps for an output whose fractional position is phase_num/P.

        Offsets are relative to ``floor(t)``.  Coefficients are cached per
        phase, which is exactly the rational-phase reuse required by the
        streaming contract.  The cache is bounded so that unusual rate pairs
        with a very large reduced denominator cannot grow it without limit;
        uncached phases are simply recomputed on demand.
        """
        cached = self._phase_taps.get(phase_num)
        if cached is not None:
            return cached
        remainder = phase_num / self.p
        taps = []
        # Offsets -R..R cover the support for every phase in [0, 1).  The
        # kernel is windowed but not exactly zero at |d| == R, so the closed
        # interval is required for bit-exact agreement with the reference.
        for offset in range(-R, R + 1):
            coefficient = kernel(offset - remainder, self.fc)
            if coefficient != 0.0:
                taps.append((offset, coefficient))
        result = tuple(taps)
        if len(self._phase_taps) < _MAX_PHASE_CACHE:
            self._phase_taps[phase_num] = result
        return result

    # -- internal segment operations -------------------------------------

    def _begin_segment(self):
        self._input_count = 0
        self._output_index = 0
        self._output_count = 0
        self._buffer = []
        self._buffer_start = 0
        self._finished = False
        self._finish_calls = 0
        self._started = True

    def _trim_buffer(self):
        """Drop history which cannot contribute to the next output."""
        if self._finished:
            self._buffer = []
            self._buffer_start = self._input_count
            return
        if not self._started:
            return
        # Earliest integer source index in the support of the next output:
        # floor(t) - R with t = m*Q/P.
        floor_t = (self._output_index * self.q) // self.p
        first_needed = floor_t - R
        if first_needed <= self._buffer_start:
            return
        drop = first_needed - self._buffer_start
        if drop > len(self._buffer):
            drop = len(self._buffer)
        if drop:
            self._buffer = self._buffer[drop:]
            self._buffer_start += drop

    def _sample_at(self, index):
        if index < 0 or index >= self._input_count:
            return self._zero_frame_cache
        offset = index - self._buffer_start
        if 0 <= offset < len(self._buffer):
            return self._buffer[offset]
        return self._zero_frame_cache

    def _render_one(self):
        m = self._output_index
        floor_t, phase_num = divmod(m * self.q, self.p)
        taps = self._taps_for_phase(phase_num)
        sums = [0.0] * self.channels
        for offset, coefficient in taps:
            frame = self._sample_at(floor_t + offset)
            for channel in range(self.channels):
                sums[channel] += frame[channel] * coefficient
        self._output_index += 1
        self._output_count += 1
        self._total_output_frames += 1
        return sums

    def _drain_streaming(self):
        """Emit outputs whose right-hand support is already available."""
        outputs = []
        n = self._input_count
        # Rightmost integer index in the support of output m is
        # floor(t + R) = floor(t) + R with t = m*Q/P.  It is available iff
        # floor(t) + R <= N - 1.
        while (self._output_index * self.q) // self.p + R < n:
            outputs.append(self._render_one())
        self._trim_buffer()
        return outputs

    def _drain_finished(self):
        """Emit the complete remaining, zero-padded tail of this segment."""
        outputs = []
        n = self._input_count
        # m < ceil(N*P/Q)  <=>  m*Q < N*P.
        while self._output_index * self.q < n * self.p:
            outputs.append(self._render_one())
        self._finished = True
        self._trim_buffer()
        return outputs

    # -- public API -----------------------------------------------------

    def push(self, frames, discontinuity=False, **marker):
        """Consume interleaved frames; return newly available output frames.

        ``discontinuity=True`` marks the first frame of ``frames`` as the
        start of a new segment.  The old segment is finished with its
        zero-padded tail and its outputs are returned before the new block's
        outputs.  No earlier signal can leak across the boundary.

        A non-empty push after ``finish`` is an error unless ``reset`` is
        called first.  An empty ordinary push is a no-op and does not change
        the phase.
        """
        if marker:
            for name in ("discontinuous", "break_", "segment_break", "gap", "new_segment"):
                if marker.pop(name, False):
                    discontinuity = True
            if marker:
                raise TypeError("unexpected push keyword")
        # Validate fully before touching state.
        checked = _validate_frames(frames, self.channels)

        # A plain push after finish is ambiguous: the caller may have meant to
        # continue the finished segment.  An explicit discontinuity marker is
        # not ambiguous -- it deliberately opens a new segment -- so it is
        # allowed after finish without a separate reset.
        if self._finished and checked and not discontinuity:
            raise ValueError("push after finish requires reset")

        if not checked and not discontinuity:
            return []  # empty block must not affect phase or counters

        outputs = []
        if discontinuity:
            outputs.extend(self._finish_current())
            self._segment_index += 1
            self._begin_segment()
            if not checked:
                return outputs

        if not self._started:
            self._begin_segment()

        self._buffer.extend(checked)
        self._input_count += len(checked)
        self._total_input_frames += len(checked)
        outputs.extend(self._drain_streaming())
        self._trim_buffer()
        return outputs

    def _finish_current(self):
        outputs = self._drain_finished()
        self._finish_calls += 1
        return outputs

    def finish(self):
        """Flush the zero-padded tail; repeated calls return an empty list."""
        if not self._started:
            self._finish_calls += 1
            self._finished = True
            return []
        if self._finished:
            self._finish_calls += 1
            return []
        return self._finish_current()

    def reset(self):
        """Discard an unflushed tail and restart at source sample zero."""
        self._segment_index += 1
        self._begin_segment()

    def mark_discontinuity(self, flush=True):
        """Insert an explicit segment boundary and return the boundary output.

        With ``flush=True`` the preceding segment's zero-padded tail is
        completed.  With ``flush=False`` the unflushed tail is discarded.  The
        following push starts at source sample zero either way.
        """
        outputs = self.finish() if flush else []
        self._segment_index += 1
        self._begin_segment()
        return outputs

    breakpoint = mark_discontinuity
    discontinuity_marker = mark_discontinuity

    def reference(self, frames):
        """Full-sequence reference for this converter's rates/channels."""
        return reference_resample(self.in_rate, self.out_rate, self.channels, frames)


Resampler = Converter
StreamingResampler = Converter


# ---------------------------------------------------------------------------
# Independent whole-stream reference path
# ---------------------------------------------------------------------------


def reference_resample(in_rate, out_rate, channels, frames):
    """Direct-convolution reference for one complete segment.

    Every output index ``m`` has source time ``m*Q/P``; there are
    ``ceil(N*P/Q)`` outputs.  Source positions outside ``[0, N)`` are zero,
    exactly as in the streaming converter.  No streaming state is used.
    """
    _validate_config(in_rate, out_rate, channels)
    p, q = _reduced_ratio(in_rate, out_rate)
    fc = 0.45 * min(1.0, p / q)
    checked = _validate_frames(frames, channels)
    n = len(checked)
    count = _output_count(n, p, q)
    result = []
    for m in range(count):
        floor_t, phase_num = divmod(m * q, p)
        first = max(0, floor_t - R)
        last = min(n - 1, floor_t + R)
        sums = [0.0] * channels
        for index in range(first, last + 1):
            distance = index - (floor_t + phase_num / p)
            coefficient = kernel(distance, fc)
            if coefficient == 0.0:
                continue
            frame = checked[index]
            for channel in range(channels):
                sums[channel] += frame[channel] * coefficient
        result.append(sums)
    return result


reference = reference_resample
full_sequence_reference = reference_resample


def reference_replay(data):
    """Replay a JSON document using only the whole-sequence reference path.

    This is the control experiment for :func:`replay`.  Blocks pushed into one
    segment are concatenated, and the segment's complete output is produced
    by :func:`reference_resample` when the segment is closed by ``finish``,
    ``reset``, a discontinuity command or a pushed block carrying a boundary
    flag.  Concatenating all returned frames therefore yields exactly the
    full-sequence reference result, independently of how the input was split
    into pushes.  Per-command delivery differs from the streaming path (the
    reference cannot emit before the segment ends), but the concatenation and
    the cumulative counts agree.
    """
    if not isinstance(data, dict):
        raise ValueError("input must be an object")
    if "in_rate" not in data or "out_rate" not in data or "channels" not in data:
        raise ValueError("in_rate, out_rate and channels are required")
    in_rate, out_rate, channels = data["in_rate"], data["out_rate"], data["channels"]
    _validate_config(in_rate, out_rate, channels)
    p, q = _reduced_ratio(in_rate, out_rate)
    commands = data.get("commands", [])
    if not isinstance(commands, list):
        raise ValueError("commands must be an array")

    segment = []
    results = []
    state = {
        "n": 0,               # cumulative source frames
        "m": 0,               # cumulative output frames
        "segments": 0,        # segment index, mirroring the streaming value
        "segment_n": 0,       # source frames in the current/last segment
        "segment_m": 0,       # output frames in the current/last segment
        "finished": False,
        "finish_calls": 0,
    }

    def close_segment():
        out = reference_resample(in_rate, out_rate, channels, segment)
        state["m"] += len(out)
        state["segment_m"] = len(out)
        del segment[:]
        return out

    def take(checked):
        segment.extend(checked)
        state["n"] += len(checked)
        state["segment_n"] += len(checked)

    def begin_segment():
        state["segments"] += 1
        state["segment_n"] = 0
        state["segment_m"] = 0
        state["finished"] = False
        state["finish_calls"] = 0
        del segment[:]

    def snapshot():
        # Phase and next index are per segment, mirroring Converter.status().
        phase_num = (state["segment_m"] * q) % p
        return {
            "buffered_frames": len(segment),
            "buffered_source_frames": len(segment),
            "input_count": state["n"],
            "output_count": state["m"],
            "input_frames": state["n"],
            "output_frames": state["m"],
            "segment_input_frames": state["segment_n"],
            "segment_output_frames": state["segment_m"],
            "segment_index": state["segments"],
            "next_output_index": state["segment_m"],
            "fractional_phase": phase_num / p,
            "phase": phase_num / p,
            "phase_numerator": phase_num,
            "phase_denominator": p,
            "finished": state["finished"],
            "finish_calls": state["finish_calls"],
        }

    for command in commands:
        try:
            if not isinstance(command, dict):
                raise ValueError("invalid command")
            op = command.get("op")
            frames = []
            if op == "push":
                if "frames" not in command:
                    raise ValueError("push requires frames")
                checked = _validate_frames(command["frames"], channels)
                boundary = bool(
                    command.get("discontinuity", False)
                    or command.get("discontinuous", False)
                    or command.get("break", False)
                    or command.get("gap", False)
                )
                if not checked and not boundary:
                    raise StopIteration  # empty no-op
                if state["finished"] and checked and not boundary:
                    raise ValueError("push after finish requires reset")
                if boundary:
                    frames.extend(close_segment())
                    begin_segment()
                take(checked)
            elif op == "finish":
                state["finish_calls"] += 1
                if not state["finished"]:
                    frames = close_segment()
                    state["finished"] = True
            elif op == "reset":
                begin_segment()
            elif op in ("discontinuity", "break", "breakpoint", "mark", "gap", "segment_break"):
                if bool(command.get("flush", True)):
                    frames = close_segment()
                begin_segment()
                if "frames" in command:
                    checked = _validate_frames(command["frames"], channels)
                    take(checked)
                    frames.extend(close_segment())
            elif op == "status":
                pass
            else:
                raise ValueError("invalid operation")
            result = {"frames": frames, **snapshot()}
        except StopIteration:
            result = {"frames": [], **snapshot()}
        except (ValueError, TypeError, OverflowError) as error:
            result = {"frames": [], "error": str(error), **snapshot()}
        results.append(result)
    return {"results": results}


def replay(data):
    """Replay a JSON command document and return a JSON-compatible result.

    Each result carries the returned ``frames`` and a ``Converter.status()``
    snapshot.  A bad command produces an item-local ``error`` and leaves the
    converter usable for the following commands.
    """
    if not isinstance(data, dict):
        raise ValueError("input must be an object")
    if "in_rate" not in data or "out_rate" not in data or "channels" not in data:
        raise ValueError("in_rate, out_rate and channels are required")
    converter = Converter(data["in_rate"], data["out_rate"], data["channels"])
    commands = data.get("commands", [])
    if not isinstance(commands, list):
        raise ValueError("commands must be an array")

    results = []
    for command in commands:
        try:
            if not isinstance(command, dict):
                raise ValueError("invalid command")
            op = command.get("op")
            if op == "push":
                if "frames" not in command:
                    raise ValueError("push requires frames")
                boundary = bool(
                    command.get("discontinuity", False)
                    or command.get("discontinuous", False)
                    or command.get("break", False)
                    or command.get("gap", False)
                )
                frames = converter.push(command["frames"], discontinuity=boundary)
            elif op == "finish":
                frames = converter.finish()
            elif op == "reset":
                converter.reset()
                frames = []
            elif op in ("discontinuity", "break", "breakpoint", "mark", "gap", "segment_break"):
                # Optional frames are treated as the first block of the new
                # segment, which saves one round trip for a discontinuous
                # source.
                flush = bool(command.get("flush", True))
                frames = converter.mark_discontinuity(flush=flush)
                if "frames" in command:
                    frames.extend(converter.push(command["frames"]))
            elif op == "status":
                frames = []
            else:
                raise ValueError("invalid operation")
            result = {"frames": frames, **converter.status()}
        except (ValueError, TypeError, OverflowError) as error:
            result = {"frames": [], "error": str(error), **converter.status()}
        results.append(result)
    return {"results": results}


def main():
    parser = argparse.ArgumentParser(description="Streaming PCM sample-rate converter")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--reference",
        action="store_true",
        help="treat each push's frames as one segment and use the whole-sequence "
        "reference path (checks the streaming result without converter state)",
    )
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    result = reference_replay(data) if args.reference else replay(data)
    args.output.write_text(json.dumps(result, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
