"""Uniform-partition overlap-save convolver for one input->output path.

Time line / latency contract
----------------------------
Let ``B`` be the FFT partition size (a power of two).  Input samples are
``x[n]`` with *absolute* engine indices ``n`` (the first live sample is n=0).
Impulse samples are ``h[t]``, 0 <= t < T.

The overlap-save transform frame q is::

    frame_q = [x[qB-B], ..., x[qB-1],  x[qB], ..., x[(q+1)B-1]]
              \____ previous B ____/    \____ newest B ____/

After circular convolution with the zero-padded, partitioned response, the
*second half* of the inverse transform is a clean linear-convolution block::

    y[(q+1)B .. (q+2)B-1] = second_half(IFFT( sum_m X_{q-m} H_m ))

with X_k = 0 for k < 0 (silent past).  The very first emitted sample is y[B],
the response to x[0]: each path therefore has an algorithmic delay of exactly
``B`` samples.  Inside an arbitrarily sized callback the extra waiting time is
at most ``B-1`` samples; the sample clock itself never stops because incoming
samples are queued and frames are produced as soon as B samples accumulate.

All storage is allocated in ``__init__`` / ``set_impulse``.  ``push_sample``,
``pump`` and ``pop_output`` never allocate and never resize any list.
"""

from __future__ import annotations

from typing import List

from partition_fft import FftPlan


class _Ring:
    """Fixed-capacity single-producer/single-consumer ring of floats."""

    __slots__ = ("cap", "buf", "head", "tail", "count")

    def __init__(self, cap: int):
        self.cap = cap
        self.buf = [0.0] * cap
        self.head = 0   # next write index
        self.tail = 0   # next read index
        self.count = 0

    def push(self, v: float) -> None:
        assert self.count < self.cap, "ring overflow"
        self.buf[self.head] = v
        self.head = (self.head + 1) % self.cap
        self.count += 1

    def pop(self) -> float:
        assert self.count > 0, "ring underflow"
        v = self.buf[self.tail]
        self.tail = (self.tail + 1) % self.cap
        self.count -= 1
        return v


class PartitionConvolver:
    def __init__(self, partition: int, max_partitions: int,
                 fft_counters: List[int], stream_cap: int = 4096):
        if partition < 2 or (partition & (partition - 1)) != 0:
            raise ValueError("partition must be a power of two >= 2")
        if max_partitions < 1:
            raise ValueError("max_partitions must be >= 1")
        self.b = partition
        self.n = 2 * partition
        self.max_p = max_partitions
        self._ffts = fft_counters  # shared engine counter list

        self.plan = FftPlan(self.n)
        # A mid-stream activation replays the whole input prefix before its
        # first pump, so the input FIFO must hold the full session length.
        self.in_ring = _Ring(stream_cap + 2 * partition)
        # Frequency-domain input frames X_0 .. X_P (P+1 circular slots).
        self.x_specs = [[0j] * self.n for _ in range(max_partitions + 1)]
        # Frequency-domain response partitions (preallocated to max depth).
        self.h_specs = [[0j] * self.n for _ in range(max_partitions)]
        self.p = 0                       # number of active IR partitions
        self.x_pos = 0                   # next X slot to overwrite
        # Absolute-sample bookkeeping.  ``fed`` is the absolute index of the
        # next input sample this convolver expects; ``emitted`` is the
        # absolute index of its next queued output.  Standard uniform
        # partition overlap-save has a one-partition latency: the first B
        # input samples form frame 0 whose second half is circular wrap, and
        # the clean linear response to x[0] is produced at output index B in
        # frame 1.  Output origin is consequently B; indices 0..B-1 are the
        # path delay.  The engine prepends B silence to 0-align its stream.
        self.fed = 0
        self.emitted = self.b
        self.output_origin = self.b   # absolute index of first queued sample
        # Output FIFO.  A convolver produces one sample per input sample once
        # primed; between engine drains it must hold up to the whole session
        # length (plus filter depth), never resized at run time.
        self.out_ring = _Ring(stream_cap + max_partitions * partition
                              + 2 * partition)
        self._frame = [0.0] * self.n     # real time-domain frame
        self._prev_half = [0.0] * self.b  # previous block's new B samples
        self._cacc = [0j] * self.n       # complex frequency accumulator

    # -- configuration -----------------------------------------------------

    def set_impulse(self, impulse: List[float]) -> None:
        """Install an impulse response of length 1 .. max_p * B.

        Every partition is transformed immediately; steady-state processing
        consequently allocates nothing.  Input/output history is cleared so a
        fresh convolver starts from a silent past (the engine then pre-feeds
        the required input history when the response is activated).
        """
        b = self.b
        t = len(impulse)
        if t < 1 or t > self.max_p * b:
            raise ValueError(
                "impulse length %d out of range [1, %d]" % (t, self.max_p * b))
        p = (t + b - 1) // b
        frame = self._frame
        for m in range(p):
            for i in range(self.n):
                frame[i] = 0.0
            start = m * b
            for i in range(b):
                idx = start + i
                if idx < t:
                    frame[i] = impulse[idx]  # h_m in the FIRST half
            spec = self.plan.fft(frame)
            target = self.h_specs[m]
            for i in range(self.n):
                target[i] = spec[i]
            self._ffts[0] += 1
        self.p = p
        self.reset_history()

    def reset_history(self) -> None:
        """Forget all input/output history (silent past). No allocation."""
        for ring in (self.in_ring, self.out_ring):
            ring.head = ring.tail = ring.count = 0
        for slot in self.x_specs:
            for i in range(self.n):
                slot[i] = 0j
        for i in range(self.n):
            self._frame[i] = 0.0
        for i in range(self.b):
            self._prev_half[i] = 0.0
        self.x_pos = 0
        self.fed = 0
        self.emitted = self.output_origin

    # -- steady state ------------------------------------------------------

    def push_sample(self, v: float) -> None:
        self.in_ring.push(v)
        self.fed += 1

    def next_output_index(self) -> int:
        return self.emitted

    def pop_output(self) -> float:
        value = self.out_ring.pop()
        self.emitted += 1
        return value

    def pump(self) -> None:
        """Convert every queued group of B samples into one output block."""
        b = self.b
        while self.in_ring.count >= b:
            frame = self._frame
            # Slide the previous new half into the old half in place, then
            # fill the new half from the FIFO.  _frame retains the previous
            # block between calls, so no separate history array is needed and
            # there is no duplicated/shifted state.
            for i in range(b):
                frame[i] = frame[b + i]
            for i in range(b):
                frame[b + i] = self.in_ring.pop()
            spec = self.plan.fft(frame)
            slot = self.x_specs[self.x_pos]
            for i in range(self.n):
                slot[i] = spec[i]
            self._ffts[0] += 1
            self.x_pos = (self.x_pos + 1) % (self.p + 1)
            self._accumulate_and_emit()

    def _accumulate_and_emit(self) -> None:
        n = self.n
        p = self.p
        cacc = self._cacc
        for i in range(n):
            cacc[i] = 0j
        newest = self.x_pos  # one past the newest stored frame
        for m in range(p):
            xs = self.x_specs[(newest - 1 - m) % (p + 1)]
            hs = self.h_specs[m]
            for i in range(n):
                cacc[i] += xs[i] * hs[i]
        block = self.plan.ifft(cacc)
        self._ffts[0] += 1
        out = self.out_ring
        for i in range(self.b, n):  # second half = clean linear result
            out.push(block[i])

    def available(self) -> int:
        return self.out_ring.count

    def inputs_until_output_covered(self, target_absolute_index: int) -> int:
        """Additional inputs needed to emit output through an absolute index.

        Convolver output indices start at origin B.  Output block 0 covers
        absolute indices B..2B-1 (linear 0..B-1) and is produced once B input
        samples have been pumped; block q needs (q+1)B pumped inputs.
        Covering linear index k = t-B requires (floor(k/B)+1) blocks and
        (floor(k/B)+1)*B pumped input samples.  Samples already queued in the
        input FIFO count toward that total.
        """
        if target_absolute_index < self.output_origin:
            return 0
        linear = target_absolute_index - self.output_origin
        required_inputs = (linear // self.b + 1) * self.b
        # fed already counts every sample pushed, including those still queued
        # in the input FIFO, so it alone is the "already accounted" total.
        return max(0, required_inputs - self.fed)
