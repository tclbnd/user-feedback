"""Partitioned FFT primitives used by the overlap-save convolver.

Radix-2 iterative Cooley-Tukey in double precision.  Every buffer a transform
needs (bit-reversal table, twiddle table, scratch complex array) is allocated
once when an :class:`FftPlan` is built; ``fft`` / ``ifft`` never allocate.

Convention (the only thing that matters for real convolution):

    forward  X[k]   = sum_n x[n] * exp(-j 2 pi k n / N)
    inverse  x[n]   = 1/N * sum_k X[k] * exp(+j 2 pi k n / N)

so that circular multiplication in the frequency domain implements circular
convolution in the time domain.
"""

from __future__ import annotations

import math
from typing import List


class FftPlan:
    """Precomputed tables and scratch space for one fixed FFT size."""

    def __init__(self, size: int):
        if size < 2 or (size & (size - 1)) != 0:
            raise ValueError("FFT size must be a power of two >= 2, got %r" % (size,))
        self.size = size
        log = size.bit_length() - 1

        # Bit-reversal permutation table (precomputed once).
        rev = [0] * size
        for i in range(1, size):
            rev[i] = (rev[i >> 1] >> 1) | ((i & 1) << (log - 1))
        self._rev = rev

        # Twiddle factors per stage, stored flat.
        twiddles: List[complex] = []
        stage = 1
        while stage < size:
            for k in range(stage):
                angle = -math.pi * k / stage
                twiddles.append(complex(math.cos(angle), math.sin(angle)))
            stage <<= 1
        self._twiddles = twiddles
        self._scratch = [0j] * size
        self._fft_out = [0j] * size
        self._ifft_out = [0.0] * size

    def fft(self, real_in):
        """Forward transform of a real sequence into a preallocated buffer.

        The returned list is reused on every call, so a caller that needs to
        retain a spectrum must copy its contents (steady-state convolvers do
        exactly that into their own preallocated slots).  No allocation.
        """
        n = self.size
        rev = self._rev
        out = self._fft_out
        for i in range(n):
            out[i] = complex(real_in[rev[i]], 0.0)
        self._butterflies(out, inverse=False)
        return out

    def fft_complex(self, cplx_in, out):
        """Forward transform of a complex sequence, written into ``out``."""
        n = self.size
        rev = self._rev
        for i in range(n):
            out[i] = cplx_in[rev[i]]
        self._butterflies(out, inverse=False)

    def ifft(self, spec):
        """Inverse transform; real part returned in a preallocated buffer."""
        n = self.size
        rev = self._rev
        buf = self._scratch
        for i in range(n):
            buf[i] = spec[rev[i]]
        self._butterflies(buf, inverse=True)
        inv_n = 1.0 / n
        out = self._ifft_out
        for i in range(n):
            out[i] = buf[i].real * inv_n
        return out

    def _butterflies(self, buf: List[complex], inverse: bool) -> None:
        n = self.size
        tw = self._twiddles
        stage = 1
        base = 0
        while stage < n:
            half = stage << 1
            if inverse:
                # Conjugate the forward twiddles for the inverse transform.
                for start in range(0, n, half):
                    for k in range(stage):
                        w = tw[base + k].conjugate()
                        a = buf[start + k]
                        b = buf[start + k + stage] * w
                        buf[start + k] = a + b
                        buf[start + k + stage] = a - b
            else:
                for start in range(0, n, half):
                    for k in range(stage):
                        w = tw[base + k]
                        a = buf[start + k]
                        b = buf[start + k + stage] * w
                        buf[start + k] = a + b
                        buf[start + k + stage] = a - b
            base += stage
            stage = half
