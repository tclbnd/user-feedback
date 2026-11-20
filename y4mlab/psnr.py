"""PSNR between two decoded luma planes (lists of sample values)."""
import math


def mse(a, b):
    if len(a) != len(b) or not a:
        raise ValueError("planes differ in size")
    return sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)


def psnr(a, b, bit_depth=8):
    e = mse(a, b)
    peak = (1 << bit_depth) - 1
    return float("inf") if e == 0 else round(10 * math.log10(peak * peak / e), 3)
