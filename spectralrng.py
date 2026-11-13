"""Counter-based, tile-order independent random number generation.

The renderer must produce the exact same per-pixel sample sequence no matter
how rays are grouped into tiles or how many worker threads consume them.  A
counter-based construction achieves this: the stream for ``(pixel, sample,
channel)`` is a pure function of the base seed and those indices, with no
shared mutable RNG state that scheduling could perturb.

SplitMix64 is used as the mixing function (same constants as the reference
Java implementation).
"""
from __future__ import annotations

MASK64 = (1 << 64) - 1


def splitmix64(x: int) -> int:
    """Return one 64-bit SplitMix64 output for counter value ``x``."""
    x = (x + 0x9E3779B97F4A7C15) & MASK64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & MASK64
    return x ^ (x >> 31)


def _mix_seed(seed: int) -> int:
    return splitmix64(seed & MASK64)


def stream_state(pixel: int, sample: int, seed: int) -> int:
    """Per (pixel, sample) base state, independent of scheduling."""
    h = (
        _mix_seed(seed)
        + splitmix64(pixel & MASK64) * 0xD1B54A32D192ED03
        + splitmix64(sample & MASK64) * 0xA24BAED4963EE407
    ) & MASK64
    return splitmix64(h)


def stream_uint(pixel: int, sample: int, channel: int, seed: int, k: int) -> int:
    """Deterministic 64-bit draw k for a pixel/sample/channel sub-stream.

    A channel is a SplitMix64 iteration chain seeded from the per-pixel base
    state, so distinct channels are consecutive outputs of one well-mixed
    generator instead of hand-tuned hash combinations.
    """
    state = stream_state(pixel, sample, seed)
    # Separate chains per channel, then advance k steps along the chain.
    state = (state + channel * 0x9E3779B97F4A7C15) & MASK64
    state = splitmix64(state)
    for _ in range(k):
        state = splitmix64(state)
    return state


def unit_float(pixel: int, sample: int, channel: int, seed: int, k: int) -> float:
    """Uniform draw in [0, 1) with 53 bits of mantissa."""
    # Two consecutive 64-bit words fill the 53-bit mantissa (26 + 27 bits).
    hi = stream_uint(pixel, sample, channel, seed, 2 * k) >> 38
    lo = stream_uint(pixel, sample, channel, seed, 2 * k + 1) >> 37
    return ((hi << 27) + lo) / float(1 << 53)


def exponential(pixel: int, sample: int, channel: int, seed: int, k: int,
                rate: float) -> float:
    """Inverse-CDF exponential draw: -ln(1-u)/rate."""
    u = unit_float(pixel, sample, channel, seed, k)
    # u is strictly below 1 by construction; guard anyway for safety.
    if u >= 1.0:
        u = 1.0 - 1e-16
    import math
    return -math.log1p(-u) / rate


class CounterRNG:
    """Per-pixel random source with independent named channels.

    Channels keep draws for wavelength selection, light selection, transport
    sampling and scattering sampling from aliasing one another, which makes
    adding a new sampling dimension reproducible against older runs.  Each
    channel is its own SplitMix64 chain off the per-pixel base state.
    """

    CH_WAVELENGTH = 1
    CH_LIGHT = 2
    CH_TRANSPORT = 3
    CH_SCATTER = 4
    CH_SCATTER_DIR = 5

    def __init__(self, pixel: int, sample: int, seed: int):
        self.pixel = pixel
        self.sample = sample
        self.seed = seed
        base = stream_state(pixel, sample, seed)
        # One pre-mixed 64-bit state per channel; draw() advances the chain.
        self._states = {
            ch: splitmix64((base + ch * 0x9E3779B97F4A7C15) & MASK64)
            for ch in (self.CH_WAVELENGTH, self.CH_LIGHT, self.CH_TRANSPORT,
                       self.CH_SCATTER, self.CH_SCATTER_DIR)
        }

    def _next_word(self, channel: int) -> int:
        x = self._states[channel]
        x = splitmix64(x)
        self._states[channel] = x
        return x

    def draw(self, channel: int) -> float:
        """One uniform [0, 1) draw consuming two words of the channel chain."""
        hi = self._next_word(channel) >> 38   # 26 bits
        lo = self._next_word(channel) >> 37   # 27 bits
        return ((hi << 27) + lo) / float(1 << 53)

    def draw_exp(self, channel: int, rate: float) -> float:
        import math
        u = self.draw(channel)
        if u >= 1.0:
            u = 1.0 - 1e-16
        return -math.log1p(-u) / rate


def categorical_index(rng: "CounterRNG", channel: int, probs) -> int:
    """Sample a categorical proposal in a stable cumulative order."""
    u = rng.draw(channel)
    cum = 0.0
    last = len(probs) - 1
    for i, p in enumerate(probs):
        cum += p
        if u < cum or i == last:
            return i
    return last
