"""Self-test: FFT path agrees with a direct double-precision time-domain DFT.

Checks:
  1. forward FFT vs naive O(N^2) DFT,
  2. inverse FFT reconstructs the input,
  3. circular frequency-domain multiplication equals circular convolution,
  4. after plan construction, fft/ifft allocate no lists (steady-state check).
"""

import math

from partition_fft import FftPlan


def naive_dft(x, inverse=False):
    n = len(x)
    sign = 1.0 if inverse else -1.0
    out = []
    for k in range(n):
        acc = 0j
        for t, value in enumerate(x):
            angle = sign * 2.0 * math.pi * k * t / n
            acc += complex(value) * complex(math.cos(angle), math.sin(angle))
        if inverse:
            acc /= n
        out.append(acc)
    return out


def check_fft_size(size):
    plan = FftPlan(size)
    x = [math.sin(0.37 * i) + 0.5 * math.cos(0.11 * i + 1.3) for i in range(size)]

    ref = naive_dft(x)
    got_shared = plan.fft(x)
    got = list(got_shared)  # fft() reuses its output buffer; copy for the test
    max_err = max(abs(a - b) for a, b in zip(ref, got))
    assert max_err < 1e-10, (size, "forward", max_err)

    back = plan.ifft(got)
    rec_err = max(abs(a - b) for a, b in zip(x, back))
    assert rec_err < 1e-12, (size, "inverse", rec_err)

    # Circular convolution via FFT must match the direct circular sum.
    y = [math.cos(0.23 * i + 0.4) for i in range(size)]
    spec_y = list(plan.fft(y))
    circ_ref = [
        sum(x[(i - j) % size] * y[j] for j in range(size))
        for i in range(size)]
    circ_got = plan.ifft([a * b for a, b in zip(got, spec_y)])
    c_err = max(abs(a - b) for a, b in zip(circ_ref, circ_got))
    assert c_err < 1e-10, (size, "circular conv", c_err)

    # Steady-state allocation check for transforms on preallocated inputs.
    pre = [0j] * size
    from alloc_watch import AllocWatcher
    watcher = AllocWatcher()
    with watcher:
        for _ in range(50):
            plan.fft_complex(got, pre)
            plan.ifft(pre)
    assert watcher.events == 0, (size, "transform allocations",
                                 watcher.events, watcher.examples)
    return max_err, rec_err, c_err


def main():
    for size in (8, 16, 32, 64, 128, 256):
        errs = check_fft_size(size)
        print("FFT N=%3d  forward=%.2e  inverse=%.2e  circular=%.2e  ok"
              % ((size,) + errs))
    print("verify_fft: all checks passed")


if __name__ == "__main__":
    main()
