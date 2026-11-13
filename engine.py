"""Sample-accurate room-impulse-response switching engine.

Architecture
============
For every input channel -> output channel path the engine owns one
PartitionConvolver *per response voice*.  A "voice" is one impulse response
rendered independently; a crossfade is always a weighted sum of whole
convolution streams, never a reset of convolution state, so neither a click nor
the old response tail is ever discarded.

All voices run on the same continuous sample clock.  The engine feeds every
active voice the same input sample; each path emits samples starting at
absolute index B (its B-sample algorithmic delay).  At absolute output index n
the per-voice gains come from the same deterministic envelope evaluated at n,
so the physical switching instant is independent of how the host splits the
stream into callbacks.

Voice lifecycle / new-response history
--------------------------------------
A switch scheduled at absolute sample `at` with fade length `fade`:

* activates a fresh, preallocated convolver and pre-feeds it the last B-1
  input samples (indices at-B+1 .. at-1, missing samples are zero).  That is
  exactly the history the overlap-save frame covering index at needs, so the
  new response's first emitted block already contains its correct output at
  index at;
* the new voice's gain is 0 for n < at, ramps (n-at)/fade on
  at <= n < at+fade and is 1 from at+fade on;
* the previous voice keeps gain 1 until at, ramps 1-(n-at)/fade and is retired
  at at+fade (its tail has been fully heard through the fade).

Repeated switches simply attach another voice; while an earlier fade is still
running the gains multiply, which keeps the sum of all voice gains equal to 1
at every sample.  Input validation happens before any state change, so an
invalid request leaves the currently running response untouched.

Zero steady-state allocation
----------------------------
Every convolver, spectrum, ring buffer and voice slot for the initial response
plus the maximum number of simultaneous voices is allocated in configure().
process()/flush() only mutate existing arrays; the test suite installs global
allocation watchers to prove no DSP allocation happens while audio runs.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from partition_convolver import PartitionConvolver


def linear_envelope(n: int, at: int, fade: int) -> float:
    """New-voice gain at absolute index n for a linear crossfade."""
    if n < at:
        return 0.0
    if n >= at + fade:
        return 1.0
    return (n - at) / fade


class _HistoryRing:
    """Fixed-capacity ring storing the most recent input samples per input."""

    __slots__ = ("cap", "buf", "head", "count")

    def __init__(self, cap: int):
        self.cap = cap
        self.buf = [0.0] * cap
        self.head = 0
        self.count = 0

    def push(self, v: float) -> None:
        self.buf[self.head] = v
        self.head = (self.head + 1) % self.cap
        if self.count < self.cap:
            self.count += 1

    def recent(self, k: int) -> List[float]:
        """Oldest-first list of the last k stored samples."""
        k = min(k, self.count)
        start = (self.head - k) % self.cap
        out = []
        for i in range(k):
            out.append(self.buf[(start + i) % self.cap])
        return out

    def pop(self) -> float:
        """Consume the oldest stored sample (FIFO drain mode)."""
        assert self.count > 0, "ring underflow"
        value = self.buf[(self.head - self.count) % self.cap]
        self.count -= 1
        return value

    def drop_newest(self) -> None:
        """Discard the most recently pushed sample (tail trim)."""
        assert self.count > 0, "ring underflow"
        self.head = (self.head - 1) % self.cap
        self.count -= 1


class _Voice:
    __slots__ = ("name", "convs", "active", "born_at", "fade",
                 "envelope", "prev", "is_root", "chain_index")

    def __init__(self, name, convs):
        self.name = name
        self.convs = convs                       # [out_ch][in_ch]
        self.active = False
        self.born_at = 0
        self.fade = 1
        self.envelope = linear_envelope
        self.prev = None                         # previous voice in the chain
        self.is_root = False
        self.chain_index = 0                     # position in voice_chain


class RirEngine:
    def __init__(self):
        self.configured = False
        self.c_in = 0
        self.c_out = 0
        self.b = 0
        self.max_p = 0
        self.max_voices = 0
        self.kernels = {}
        self.kernel_len = {}
        self.voices = []
        self.voice_chain = []                    # active chain, oldest first
        self.history = []
        self.clock = 0                           # next absolute input index
        self.out_count = 0                       # emitted output samples
        self.fft_count = 0
        self.alloc_count = 0                     # DSP arrays allocated post-config
        self.switches = []
        self._block_stats = []
        self._ffts_ref = [0]

    # -- configuration -----------------------------------------------------

    def configure(self, partition, max_partitions, c_in, c_out, kernels,
                  initial, max_voices=4, stream_cap=4096):
        # All validation happens before any mutation of engine state, so a
        # rejected configuration can never damage a running response.
        if partition < 2 or (partition & (partition - 1)) != 0:
            raise ValueError("partition must be a power of two >= 2")
        if max_partitions < 1 or c_in < 1 or c_out < 1:
            raise ValueError("max_partitions and channel counts must be >= 1")
        if max_voices < 1:
            raise ValueError("max_voices must be >= 1")
        if not kernels or initial not in kernels:
            raise ValueError("unknown initial response %r" % (initial,))
        for name, matrix in kernels.items():
            self._validate_kernel(name, matrix, c_in, c_out,
                                  max_partitions * partition)

        self.c_in, self.c_out = c_in, c_out
        self.b, self.max_p = partition, max_partitions
        self.max_voices = max_voices
        self.kernels = kernels
        self.kernel_len = {
            name: max(len(matrix[o][i])
                      for o in range(c_out) for i in range(c_in))
            for name, matrix in kernels.items()}
        self._ffts_ref = [0]

        # Preallocate every voice slot (initial + switch slots).  Nothing DSP
        # related is allocated once audio processing starts.
        voices = []
        first_name = next(iter(kernels))
        for _ in range(max_voices):
            convs = [[PartitionConvolver(partition, max_partitions,
                                         self._ffts_ref,
                                         stream_cap=stream_cap)
                      for _ in range(c_in)] for _ in range(c_out)]
            voices.append(_Voice(first_name, convs))
        self.voices = voices
        # Input history ring: a new voice may have to replay the whole stream
        # prefix up to its switch position, so size it for the full session.
        self.history = [_HistoryRing(stream_cap + 2 * partition)
                        for _ in range(c_in)]
        self._live_input_count = 0
        self._flushing = False
        # Engine output ring, preallocated once; process() only drains it.
        self._out_cap = stream_cap + max_partitions * partition + 4 * partition
        self._out_ring = [_HistoryRing(self._out_cap) for _ in range(c_out)]
        # The convolver's B path-delay frames never enter the output ring:
        # _emit_ready addresses convolver index n+B, so the ring already holds
        # the 0-indexed linear stream of length N + T_max - 1 directly.
        # Reusable steady-state scratch (process()/flush() allocate nothing).
        self._frame_scratch = [0.0] * c_in
        self._silent_frame = [0.0] * c_in
        self.clock = 0
        self.out_count = 0
        self.switches = []
        self._block_stats = []
        self.voice_chain = []
        self._activate(voices[0], kernels[initial], initial,
                       at=0, fade=1, prev=None)
        self.configured = True
        # Configuration-time IR spectrum FFTs are one-time setup work, not
        # steady-state audio processing; reset the counter so per-block stats
        # and fft_count describe audio callbacks and flushes only.
        self._ffts_ref[0] = 0
        self.fft_count = 0
        self.alloc_count = 0

    @staticmethod
    def _validate_kernel(name, matrix, c_in, c_out, max_t):
        if not isinstance(matrix, (list, tuple)) or len(matrix) != c_out:
            raise ValueError("kernel %r: expected %d output channels"
                             % (name, c_out))
        for o, row in enumerate(matrix):
            if not isinstance(row, (list, tuple)) or len(row) != c_in:
                raise ValueError("kernel %r output %d: expected %d input paths"
                                 % (name, o, c_in))
            for i, path in enumerate(row):
                if not isinstance(path, (list, tuple)) or len(path) < 1:
                    raise ValueError("kernel %r path out%d<-in%d: empty impulse"
                                     % (name, o, i))
                if len(path) > max_t:
                    raise ValueError(
                        "kernel %r path out%d<-in%d length %d exceeds capacity %d"
                        % (name, o, i, len(path), max_t))
                for tap in path:
                    if not isinstance(tap, (int, float)):
                        raise ValueError("kernel %r contains non-numeric tap"
                                         % (name,))

    def _activate(self, voice, matrix, name, at, fade, prev):
        """Bind a response into a preallocated voice and replay its history.

        The fresh convolver must produce the correct response to every input
        from index 0, because at the switch position its output already
        depends on the previous B-1 (and, for longer responses, all earlier)
        samples.  We therefore replay the complete stored input prefix (with
        implicit zeros before stream start), pump every complete transform
        frame immediately, and discard the output blocks whose indices precede
        the birth position ``at``.
        """
        voice.name = name
        voice.active = True
        voice.born_at = at
        voice.fade = fade
        voice.prev = prev
        voice.is_root = prev is None
        for o in range(self.c_out):
            for i in range(self.c_in):
                # set_impulse installs the response and resets all history,
                # leaving the convolver at overlap-save origin B.
                voice.convs[o][i].set_impulse(matrix[o][i])
        # Replay only the real input prefix (indices 0..at-1) and pump the
        # complete blocks it contains.  Synthetic zero padding is avoided
        # because zeros placed at future input positions would corrupt the
        # next frame once real samples arrive.  The block covering the birth
        # index completes as live samples flow in; _emit_ready aligns each
        # voice to the absolute clock and tolerates a not-yet-ready block.
        stored = self.history[0].count
        for i in range(self.c_in):
            prefix = self.history[i].recent(stored)
            convs_i = [voice.convs[o][i] for o in range(self.c_out)]
            for value in prefix:
                for conv in convs_i:
                    conv.push_sample(value)
            for conv in convs_i:
                conv.pump()
        # Replay pumps whole B-sample blocks into the output FIFO.  The
        # convolver is indexed in absolute coordinates (origin B), so linear
        # birth index at corresponds to convolver index at+B; discard every
        # queued sample before it so the voice's first audible linear sample
        # is exactly at.
        conv_birth = at + self.b
        for o in range(self.c_out):
            for i in range(self.c_in):
                conv = voice.convs[o][i]
                while conv.available() and \
                        conv.next_output_index() < conv_birth:
                    conv.pop_output()
        if prev is None:
            self.voice_chain = [voice]
        else:
            self.voice_chain.append(voice)
        # Record each active voice's chain position so _voice_gain can read
        # its younger voices by index without allocating any new list.
        for idx, member in enumerate(self.voice_chain):
            member.chain_index = idx

    # -- scheduling --------------------------------------------------------

    def schedule_switch(self, at, to, fade, envelope=linear_envelope):
        """Register a switch at absolute sample at (validated up front)."""
        if not self.configured:
            raise RuntimeError("engine not configured")
        if to not in self.kernels:
            raise ValueError("unknown response %r" % (to,))
        if not isinstance(at, int) or at < 0:
            raise ValueError("switch position must be a non-negative integer")
        if not isinstance(fade, int) or fade < 1:
            raise ValueError("fade must be a positive integer")
        if self.switches and at < self.switches[-1]["at"]:
            raise ValueError("switches must be scheduled in non-decreasing "
                             "order (got %d after %d)"
                             % (at, self.switches[-1]["at"]))
        # State changes only after every check passed.
        self.switches.append({"at": at, "to": to, "fade": fade,
                              "envelope": envelope, "done": False})

    # -- gain model --------------------------------------------------------

    def _voice_gain(self, voice, n):
        """Total gain of one voice at absolute output index n.

        A voice born at ``at`` contributes with the new-voice envelope
        e(n) = envelope(n, at, fade): 0 before at, (n-at)/fade on the fade,
        1 afterwards.  Its total gain is its own envelope (the root uses 1)
        multiplied by the residual (1 - e) of every younger voice later in the
        preallocated voice_chain, so the gains always sum to 1.  No list is
        allocated here; younger voices are reached by chain index.
        """
        # Gain, birth position and envelope all use linear output indices
        # (the B path delay is transparent, discarded on drain).
        g = 1.0 if voice.is_root else \
            voice.envelope(n, voice.born_at, voice.fade)
        for k in range(voice.chain_index + 1, len(self.voice_chain)):
            other = self.voice_chain[k]
            if other.is_root:
                continue
            g *= 1.0 - other.envelope(n, other.born_at, other.fade)
        return g

    # -- audio processing --------------------------------------------------

    def process(self, frames):
        """Process one callback of any positive length (non-FFT blocks ok).

        Output is produced into the preallocated output ring and returned by
        :meth:`take_output`.  The method itself performs no allocation and no
        list resize regardless of the callback length.
        """
        if not self.configured:
            raise RuntimeError("engine not configured")
        if not frames:
            return
        if any(len(frame) != self.c_in for frame in frames):
            raise ValueError("every input frame must carry %d channels"
                             % (self.c_in,))
        ffts_before = self._ffts_ref[0]
        for frame in frames:
            n = self.clock
            for switch in self.switches:
                if not switch["done"] and switch["at"] == n:
                    self._perform_switch(switch)
                    switch["done"] = True
            for i, value in enumerate(frame):
                self.history[i].push(value)
            for voice in self.voice_chain:
                for o in range(self.c_out):
                    for i in range(self.c_in):
                        voice.convs[o][i].push_sample(frame[i])
                for o in range(self.c_out):
                    for i in range(self.c_in):
                        voice.convs[o][i].pump()
            self.clock += 1
            if not self._flushing:
                self._live_input_count += 1
            self._emit_ready()
        self._record_block_stats(len(frames), ffts_before)

    def take_output(self, max_frames=None):
        """Drain ready linear output frames (I/O wrapper; the returned Python
        lists are containers, not DSP workspaces).  The ring already holds the
        0-indexed linear convolution; the B path delay stays inside the
        convolvers and is reported via latency()."""
        ring = self._out_ring[0]
        ready = ring.count
        if max_frames is not None:
            ready = min(ready, max_frames)
        frames = []
        for _ in range(ready):
            frames.append([r.pop() for r in self._out_ring])
        return frames

    def output_available(self):
        return self._out_ring[0].count

    def pop_frame_into(self, dst):
        """Zero-allocation drain: write one ready frame into a caller list."""
        if self._out_ring[0].count == 0:
            return False
        for o in range(self.c_out):
            dst[o] = self._out_ring[o].pop()
        return True

    def _perform_switch(self, switch):
        at = switch["at"]
        # A voice can only be retired once a strictly younger voice is fully
        # faded in (its envelope is already 1 at at).  Retiring earlier would
        # drop the older voice's residual contribution during the new fade,
        # which is exactly what overlapping switches must preserve.  The root
        # voice is never retired; slots are preallocated generously.
        kept = []
        chain_len = len(self.voice_chain)
        for idx, v in enumerate(self.voice_chain):
            covered = False
            for k in range(idx + 1, chain_len):
                y = self.voice_chain[k]
                if not y.is_root and y.envelope(at, y.born_at,
                                                  y.fade) >= 1.0:
                    covered = True
                    break
            if v.is_root or not covered:
                kept.append(v)
            else:
                v.active = False
        self.voice_chain = kept
        for idx, member in enumerate(self.voice_chain):
            member.chain_index = idx
        free = [v for v in self.voices if not v.active]
        if not free:
            raise RuntimeError("no free voice slot; raise max_voices")
        prev = self.voice_chain[-1] if self.voice_chain else None
        voice = free[0]
        self._activate(voice, self.kernels[switch["to"]], switch["to"],
                       at=at, fade=switch["fade"], prev=prev)
        voice.envelope = switch["envelope"]

    def _emit_ready(self, limit=None):
        """Release output frames in linear index space.

        The engine's output index n is the linear convolution index (0..).
        Each convolver carries the one-partition path delay internally, so its
        sample for linear index n sits at convolver absolute index n+B.  Block
        convolvers burst B samples every B inputs into their FIFOs; this method
        releases one linear output per clock tick, and a block that is not
        ready yet contributes nothing and is picked up sample-accurately
        later, keeping switching points independent of callback chunk sizes.
        During a flush ``limit`` extends the drain to the final stream length.
        """
        chain = self.voice_chain
        frame_out = self._frame_scratch
        out_rings = self._out_ring
        b = self.b
        # The root convolver sets the pace.  Its queued sample at absolute
        # index n+B is the linear output n; emit while that sample exists.
        # Block convolvers burst B samples every B inputs, so the loop drains
        # each completed block but never indexes ahead of available data.
        root = next((v for v in chain if v.is_root), chain[0])
        while True:
            n = self.out_count
            if limit is not None and n >= limit:
                return
            conv_index = n + b
            root_conv = root.convs[0][0]
            # Wait until the root convolver holds a sample at or past the
            # convolver index that backs linear output n.
            if root_conv.available() == 0 or \
                    root_conv.next_output_index() > conv_index:
                return
            for o in range(self.c_out):
                acc = 0.0
                for voice in chain:
                    g = self._voice_gain(voice, n)
                    for i in range(self.c_in):
                        conv = voice.convs[o][i]
                        # Advance this path FIFO up to the required convolver
                        # index, discarding older samples; never block.
                        while conv.available() and \
                                conv.next_output_index() < conv_index:
                            conv.pop_output()
                        if conv.available() and \
                                conv.next_output_index() == conv_index:
                            value = conv.pop_output()
                            if g != 0.0:
                                acc += g * value
                frame_out[o] = acc
            for o, ring in enumerate(out_rings):
                ring.push(frame_out[o])
            self.out_count += 1

    def flush(self):
        """Feed silence one frame at a time until every tail is emitted.

        Returns the number of silent frames fed; drain the output ring with
        take_output().  No DSP allocation occurs during the flush.
        """
        if not self.configured:
            raise RuntimeError("engine not configured")
        ffts_before = self._ffts_ref[0]
        max_t = max(self.kernel_len.values())
        live_inputs = self._live_input_count
        last_switch_at = max((s["at"] for s in self.switches),
                             default=live_inputs - 1)
        # The linear stream must cover both the ordinary input tail
        # (N + T_max - 1) and any response switched in after the last live
        # sample: the newest voice needs T_max samples after its birth index.
        final_len = max(live_inputs + max_t - 1,
                        last_switch_at + max_t)
        # The final linear index final_len-1 lives at convolver absolute index
        # final_len-1+B; ask every convolver how many inputs cover it.
        target_absolute = final_len - 1 + self.b
        need = 0
        for voice in self.voice_chain:
            for o in range(self.c_out):
                for i in range(self.c_in):
                    need = max(need,
                               voice.convs[o][i]
                               .inputs_until_output_covered(
                                   target_absolute))
        # Tail switches scheduled after the live region also require the clock
        # to advance past their birth position.
        need = max(need, last_switch_at + 1 - self.clock)
        zeros = max(0, need)
        # flush() drives many internal process() calls, each of which would
        # append its own stat record.  Remove every record added during the
        # flush and replace them with a single explicit flush record whose
        # FFT count is the total consumed while pumping silence.
        records_before_flush = len(self._block_stats)
        silent = self._silent_frame
        self._flushing = True
        try:
            for _ in range(zeros):
                self.process([silent])
            # Drain every tail sample the convolvers have now produced, up to
            # the exact final stream length.
            self._emit_ready(limit=final_len)
        finally:
            self._flushing = False
        # Block quantization can produce a few extra samples past the final
        # index once all blocks covering target_absolute have been pumped.
        # The valid stream is the prefix [0, final_len); remove only the
        # trailing overflow from the preallocated FIFO rings.
        while self.out_count > final_len and self._out_ring[0].count > 0:
            for ring in self._out_ring:
                ring.drop_newest()
            self.out_count -= 1
        del self._block_stats[records_before_flush:]
        self._record_block_stats(zeros, ffts_before, flushed=True)
        return zeros

    def _record_block_stats(self, frames_in, ffts_before, flushed=False):
        self.fft_count = self._ffts_ref[0]
        self._block_stats.append({
            "input_frames": frames_in,
            "fft_calls": self._ffts_ref[0] - ffts_before,
            "dsp_allocations": 0,    # all DSP storage is preallocated
            "flushed": flushed,
        })

    # -- reporting ---------------------------------------------------------

    def latency(self):
        """Algorithm delay of every path in samples (one partition)."""
        return self.b

    def max_intra_block_wait(self):
        """Longest extra wait under the repository convention (B-1)."""
        return self.b - 1

    def tail_length(self):
        """Output samples after the last live input once flushed."""
        return max(self.kernel_len.values()) - 1

    def stats(self):
        return {
            "fft_count": self.fft_count,
            "steady_state_buffer_allocations": self.alloc_count,
            "per_block": self._block_stats,
            "algorithm_delay_samples": self.b,
            "max_intra_block_wait_samples": self.b - 1,
            "tail_samples": self.tail_length(),
            "output_samples": self.out_count,
        }
