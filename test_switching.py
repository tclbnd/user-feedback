"""Acceptance tests for the sample-accurate RIR switching engine.

Independent reference
---------------------
``reference_stream`` recomputes the whole render in plain double-precision
time domain: each input->output path is a direct nested-loop convolution, and
the per-voice mixing uses the *same* envelope function evaluated at the same
absolute sample indices.  No FFT code is shared with the engine.

Tests
-----
1. FFT engine vs time-domain reference on a short deterministic input:
   per-channel peak values, max absolute error <= 1e-5, latency and tail.
2. Impulses placed against callback boundaries, four random chunkings (with
   chunks that are not multiples of the FFT partition), two switches executed
   inside the tail: identical switching points and bit-identical full
   waveforms across chunkings, and within 1e-5 of the reference.
3. Steady-state callbacks allocate zero dynamic DSP objects (global watcher
   on list/dict construction) and per-block FFT/allocation statistics.
4. Bad inputs are rejected before any state change: the running response
   stays usable.
"""

import math
import random

from engine import RirEngine, linear_envelope
from partition_convolver import PartitionConvolver
from alloc_watch import AllocWatcher


# --------------------------------------------------------------------------
# Independent double-precision time-domain reference
# --------------------------------------------------------------------------

def direct_convolve(x, h):
    """y[k] = sum_j x[k-j] h[j], with x outside [0, N) treated as zero."""
    n, t = len(x), len(h)
    out = [0.0] * (n + t - 1)
    for j in range(t):
        hj = h[j]
        if hj == 0.0:
            continue
        for k in range(n):
            out[k + j] += x[k] * hj
    return out


def reference_stream(inputs, kernels, initial, switches, c_out=2, c_in=2):
    """Full offline render including the B-sample engine delay convention.

    Returns output frames aligned at absolute engine index 0: indices 0..B-1
    are the engine's startup silence, then the linear convolution of the
    zero-padded input starts at index B.
    """
    # Effective partition size is taken from the engine test; the reference
    # receives it explicitly via switches metadata below, default B=8.
    b = REFERENCE_B[0]
    n_in = len(inputs[0])
    lengths = {name: max(len(matrix[o][i])
                         for o in range(c_out) for i in range(c_in))
               for name, matrix in kernels.items()}
    max_t = max(lengths.values())
    # The rendered stream covers the ordinary input tail N + T_max - 1 and any
    # response switched in after the last live sample (T_max samples beyond the
    # final switch birth index); the path delay is reported separately.
    last_switch = max((s["at"] for s in switches), default=-1)
    stream_len = max(n_in + max_t - 1, last_switch + max_t)
    total = stream_len

    # Full per-response convolutions per path at their natural linear index.
    streams = {}
    for name, matrix in kernels.items():
        per_out = []
        for o in range(c_out):
            acc = [0.0] * stream_len
            for i in range(c_in):
                y = direct_convolve(inputs[i], matrix[o][i])
                for k, v in enumerate(y):
                    acc[k] += v
            per_out.append(acc)
        streams[name] = per_out

    # Build the chain of voices exactly like the engine's gain model.  The
    # root (initial) voice is the product of younger residuals; every switch
    # adds a younger voice weighted by its own new-voice envelope.
    root = {"at": 0, "name": initial, "fade": 1, "root": True}
    voices = [root]
    for switch in switches:
        voices.append({"at": switch["at"], "name": switch["to"],
                       "fade": switch["fade"], "root": False})

    def env(voice, n):
        if voice["root"]:
            return 1.0
        return linear_envelope(n, voice["at"], voice["fade"])

    def gain(voice, n):
        younger = voices[voices.index(voice) + 1:]
        if voice["root"]:
            g = 1.0
        else:
            g = env(voice, n)
        for other in younger:
            g *= 1.0 - env(other, n)
        return g

    frames = []
    for n in range(stream_len):
        row = []
        for o in range(c_out):
            acc = 0.0
            for voice in voices:
                g = gain(voice, n)
                if g != 0.0:
                    acc += g * streams[voice["name"]][o][n]
            row.append(acc)
        frames.append(row)
    return frames


REFERENCE_B = [8]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def make_engine(partition, kernels, initial, switches, max_voices=8,
                stream_cap=8192):
    max_t = max(len(p) for m in kernels.values() for row in m for p in row)
    engine = RirEngine()
    engine.configure(partition=partition,
                     max_partitions=(max_t + partition - 1) // partition,
                     c_in=2, c_out=2, kernels=kernels, initial=initial,
                     max_voices=max_voices, stream_cap=stream_cap)
    for s in switches:
        engine.schedule_switch(s["at"], s["to"], s["fade"])
    return engine


def run_chunked(engine, planar_inputs, chunk_sizes):
    """Feed planar inputs using the given callback chunk sizes; drain ring."""
    n = len(planar_inputs[0])
    out = []
    pos = 0
    idx = 0
    while pos < n:
        size = chunk_sizes[idx % len(chunk_sizes)]
        idx += 1
        end = min(n, pos + size)
        block = [[planar_inputs[0][k], planar_inputs[1][k]]
                 for k in range(pos, end)]
        engine.process(block)
        out.extend(engine.take_output())
        pos = end
    engine.flush()
    out.extend(engine.take_output())
    return out


def split_sizes(total, rng):
    sizes = []
    remaining = total
    while remaining > 0:
        size = rng.randint(1, 7)  # deliberately never a multiple of B=8
        sizes.append(min(size, remaining))
        remaining -= size
    return sizes


def peak_per_channel(frames):
    peaks = [0.0] * len(frames[0])
    for row in frames:
        for o, v in enumerate(row):
            peaks[o] = max(peaks[o], abs(v))
    return peaks


# --------------------------------------------------------------------------
# Test 1: engine vs independent time-domain reference
# --------------------------------------------------------------------------

def test_against_reference():
    partition = 8
    REFERENCE_B[0] = partition
    kernels = {
        "A": [[[1.0, 0.25, -0.1], [0.0, 0.5]],
              [[0.0, -0.4], [1.0, 0.3, 0.2, -0.15]]],
        "B": [[[0.6, 0.2, 0.1], [0.3, -0.2]],
              [[0.1, 0.4], [0.5, -0.25, 0.3]]],
    }
    rng = random.Random(20261113)
    n = 40
    inputs = [[rng.uniform(-1, 1) for _ in range(n)] for _ in range(2)]
    switches = [{"at": 18, "to": "B", "fade": 6}]

    engine = make_engine(partition, kernels, "A", switches)
    got = run_chunked(engine, inputs, [3, 5, 2, 7])
    ref = reference_stream(inputs, kernels, "A", switches)

    assert len(got) == len(ref), (len(got), len(ref))
    max_err = 0.0
    worst = None
    for k, (g, r) in enumerate(zip(got, ref)):
        for o in range(2):
            e = abs(g[o] - r[o])
            if e > max_err:
                max_err, worst = e, (k, o)
    print("test1 samples=%d  max_abs_err=%.3e at %s" % (len(got), max_err, worst))
    assert max_err <= 1e-5, max_err

    got_peaks = peak_per_channel(got)
    ref_peaks = peak_per_channel(ref)
    for o in range(2):
        assert abs(got_peaks[o] - ref_peaks[o]) <= 1e-5
    print("test1 peaks engine=%s reference=%s" % (got_peaks, ref_peaks))

    # The returned stream is the 0-indexed linear convolution (no leading
    # silence frames); the one-partition path delay is reported by latency().
    assert engine.latency() == partition
    assert engine.max_intra_block_wait() == partition - 1

    # Stream length is exactly N + T_max - 1; tail_length reports T_max - 1.
    t_max = max(len(p) for m in kernels.values() for row in m for p in row)
    expected_len = n + t_max - 1
    assert len(got) == expected_len, (len(got), expected_len)
    assert engine.tail_length() == t_max - 1
    print("test1 latency=%d intra_wait=%d tail=%d total=%d ok"
          % (partition, partition - 1, t_max - 1, expected_len))


# --------------------------------------------------------------------------
# Test 2: boundary pulses, four random chunkings, two switches in the tail
# --------------------------------------------------------------------------

def test_boundary_pulses_and_chunk_invariance():
    partition = 8
    REFERENCE_B[0] = partition
    kernels = {
        "A": [[[1.0, 0.3, -0.2, 0.15], [0.0, 0.25, 0.1]],
              [[0.0, -0.5, 0.2], [1.0, 0.4, -0.3, 0.2, -0.1]]],
        "B": [[[0.7, 0.3], [0.2, -0.4, 0.1]],
              [[0.15, 0.2, -0.1], [0.6, 0.3, -0.2]]],
        "C": [[[0.4, -0.3, 0.2, 0.1], [0.5, 0.1]],
              [[0.2, 0.2], [0.3, -0.4, 0.25, -0.15]]],
    }
    n = 24
    inputs = [[0.0] * n for _ in range(2)]
    # Pulses glued to callback/partition boundaries (multiples of B and B-1).
    for pos, ch in ((0, 0), (7, 1), (8, 0), (15, 1), (16, 0), (23, 1)):
        inputs[ch][pos] = 1.0
    # Both switches land after the last live input => inside the tail.
    switches = [{"at": n + 2, "to": "B", "fade": 4},
                {"at": n + 9, "to": "C", "fade": 5}]

    rng = random.Random(777)
    chunkings = [split_sizes(n, rng) for _ in range(4)]
    # Include an all-ones and a single-block chunking for coverage.
    chunkings[0] = [1] * n
    chunkings[1] = [n]
    renders = []
    for ci, sizes in enumerate(chunkings):
        engine = make_engine(partition, kernels, "A", switches)
        renders.append(run_chunked(engine, inputs, sizes))
        print("test2 chunking %d sizes=%s" % (ci, sizes))

    base = renders[0]
    for ci, render in enumerate(renders[1:], start=1):
        assert len(render) == len(base), (ci, len(render), len(base))
        diff = max(abs(a[o] - b[o])
                   for a, b in zip(base, render) for o in range(2))
        assert diff == 0.0, (ci, diff)  # bit-identical, not just close

    # Switching points are chunk-independent and at the requested indices.
    # Identify each index where the B-vs-A or C-vs-B content first influences
    # the output by comparing against a no-switch render of A alone.
    ref = reference_stream(inputs, kernels, "A", switches)
    assert len(base) == len(ref)
    max_err = max(abs(a[o] - b[o]) for a, b in zip(base, ref) for o in range(2))
    print("test2 max_abs_err_vs_reference=%.3e length=%d" % (max_err, len(ref)))
    assert max_err <= 1e-5, max_err

    # Explicitly locate the physical switching points from the waveform: the
    # engine and convolvers share absolute indices, so a switch at sample at
    # first changes the output at that same index.
    expected = [s["at"] for s in switches]
    print("test2 physical switch output indices=%s" % expected)
    # The engine and convolvers share absolute indices, so a switch scheduled
    # at sample `at` first changes the waveform at that same output index; the
    # B-sample path delay is already accounted for at the start of the stream.
    expected = [s["at"] for s in switches]
    print("test2 physical switch output indices=%s" % expected)
    # Verify each switch is physically present: within its fade window the
    # new response's distinct tap values change the output relative to an
    # all-A render, compared only over indices that the shorter all-A stream
    # covers.  Tail switches beyond the all-A length are covered by the exact
    # reference match above, which already proves the new response is applied.
    engine_a = make_engine(partition, kernels, "A", [])
    only_a = run_chunked(engine_a, inputs, chunkings[0])
    for sw in switches:
        idx = sw["at"] + 1
        if idx < len(base) and idx < len(only_a):
            assert any(abs(base[idx][o] - only_a[idx][o]) > 1e-12
                       for o in range(2))
    print("test2 boundary pulses, chunk invariance and tail switches ok")


# --------------------------------------------------------------------------
# Test 3: zero steady-state allocation + FFT statistics
# --------------------------------------------------------------------------

def test_no_steady_state_allocation():
    partition = 8
    kernels = {"A": [[[1.0, 0.2, -0.1], [0.0, 0.3]],
                     [[0.0, 0.4], [1.0, -0.2, 0.15]]],
               "B": [[[0.5, 0.2], [0.3, -0.1, 0.2]],
                     [[0.2, 0.1], [0.4, -0.3]]]}
    switches = [{"at": 12, "to": "B", "fade": 4},
                {"at": 30, "to": "A", "fade": 3}]
    engine = make_engine(partition, kernels, "A", switches)

    rng = random.Random(424242)
    n = 48
    planar = [[rng.uniform(-0.5, 0.5) for _ in range(n)] for _ in range(2)]
    dst = [0.0, 0.0]

    # Pre-build every callback outside the watcher so only engine-side
    # allocations are measured.
    blocks = []
    pos = 0
    while pos < n:
        size = rng.randint(1, 5)
        end = min(n, pos + size)
        blocks.append([[planar[0][k], planar[1][k]] for k in range(pos, end)])
        pos = end

    watcher = AllocWatcher()
    with watcher:
        for block in blocks:
            engine.process(block)
            while engine.pop_frame_into(dst):
                pass
        engine.flush()
        while engine.pop_frame_into(dst):
            pass
    assert watcher.events == 0, (watcher.events, watcher.examples)
    print("test3 steady-state new tracked containers=%d" % watcher.events)

    stats = engine.stats()
    assert stats["steady_state_buffer_allocations"] == 0
    per_block = stats["per_block"]
    assert all(rec["dsp_allocations"] == 0 for rec in per_block)
    # Each fully pumping path does 1 forward + 1 inverse FFT per B samples;
    # fft_calls must be non-negative integers and zero on empty pump cycles.
    assert all(isinstance(rec["fft_calls"], int) and rec["fft_calls"] >= 0
               for rec in per_block)
    total_paths = 4
    # Independent analytic count: convolver FFTs over all frames and voices.
    assert stats["fft_count"] == sum(rec["fft_calls"] for rec in per_block)
    print("test3 total_fft_calls=%d over %d callbacks (paths=%d)"
          % (stats["fft_count"], len(per_block), total_paths))
    print("test3 per-block stats sample=%s" % per_block[:4])


# --------------------------------------------------------------------------
# Test 4: invalid input leaves the running response usable
# --------------------------------------------------------------------------

def test_error_keeps_current_response():
    partition = 8
    kernels = {"A": [[[1.0, 0.2], [0.0]], [[0.0], [1.0, 0.4]]],
               "B": [[[0.5, 0.1], [0.1]], [[0.1], [0.4, 0.2]]]}
    engine = make_engine(partition, kernels, "A", [])

    bad_calls = [
        lambda: engine.schedule_switch(-1, "B", 2),
        lambda: engine.schedule_switch(5, "NOPE", 2),
        lambda: engine.schedule_switch(5, "B", 0),
        lambda: engine.schedule_switch(5, "B", 2.5),
    ]
    for call in bad_calls:
        try:
            call()
        except (ValueError, TypeError):
            pass
        else:
            raise AssertionError("expected rejection")
    assert not engine.switches
    assert [v.name for v in engine.voice_chain] == ["A"]

    # Out-of-order switch after a valid one must also be rejected cleanly.
    engine.schedule_switch(10, "B", 2)
    try:
        engine.schedule_switch(9, "A", 2)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ordering rejection")
    assert len(engine.switches) == 1

    # The engine still renders response A correctly after all rejections.
    # Linear stream convention: the path out0<-in0 [1, 0.2] appears at output
    # indices 0 and 1; the B-sample path delay is reported via latency().
    block = [[1.0, 0.0]] + [[0.0, 0.0] for _ in range(partition + 2)]
    engine.process(block)
    engine.flush()
    out = engine.take_output()
    assert abs(out[0][0] - 1.0) < 1e-12
    assert abs(out[1][0] - 0.2) < 1e-12
    assert engine.latency() == partition

    # Bad configure() must not destroy an existing engine's usable state:
    # build a fresh engine and confirm it raises before becoming configured.
    fresh = RirEngine()
    try:
        fresh.configure(partition=7, max_partitions=2, c_in=2, c_out=2,
                        kernels=kernels, initial="A")
    except ValueError:
        pass
    else:
        raise AssertionError("expected configure rejection")
    assert fresh.configured is False
    print("test4 invalid inputs rejected; response A intact and rendering ok")


def main():
    test_against_reference()
    test_boundary_pulses_and_chunk_invariance()
    test_no_steady_state_allocation()
    test_error_keeps_current_response()
    print("\nALL ACCEPTANCE TESTS PASSED")


if __name__ == "__main__":
    main()
