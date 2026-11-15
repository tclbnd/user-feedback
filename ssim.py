"""SSIM / MS-SSIM - pure-stdlib implementation following README conventions.

Conventions (the two points where the defective baseline diverged):
  1. Window shape / local statistics
       gauss   : 11-tap 1-D Gaussian kernel (sigma=1.5), outer product to
                 11x11; applied as a *sliding* window (stride 1).
       uniform : 8x8 box window, also sliding (stride 1).
     Statistics are weighted with the SAME window; normalisation divides by
     the sum of window weights lying inside the image (valid convolution, no
     padding, no re-normalisation beyond the sum-1 kernel).
  2. Multi-scale order (scales=3)
       First down-sample x2 (mean pooling, odd row/column discarded), THEN
       compute the cs map at each scale; ssim map only at the last scale.
       i.e. downsample -> stats per level, never stats -> subsample.

Block tiling: block in 1,2,4 splits the current image into an even row/col
grid of tiles (with a halo of radius=(kernel-1)//2 on the inputs) and runs
the identical per-pixel arithmetic per tile; reassembled maps are therefore
bit-identical to the single-tile path.
"""

import json
import sys
import hashlib
import math

# SSIM constants: K1=0.01, K2=0.03, L=255
K1, K2, L = 0.01, 0.03, 255.0
C1 = (K1 * L) ** 2          # 6.5025
C2 = (K2 * L) ** 2          # 58.5225
C3 = C2 / 2.0

GAUSS_TAPS = 11
GAUSS_SIGMA = 1.5
UNIFORM_TAPS = 8


def gauss_1d(taps=GAUSS_TAPS, sigma=GAUSS_SIGMA):
    """1-D Gaussian kernel normalised to sum 1 (skimage-style)."""
    r = taps // 2
    k = [math.exp(-(i * i) / (2.0 * sigma * sigma)) for i in range(-r, r + 1)]
    s = sum(k)
    return [v / s for v in k]


def uniform_1d(taps=UNIFORM_TAPS):
    return [1.0 / taps] * taps


def kernel_for(mode):
    if mode == "gauss":
        return gauss_1d()
    if mode == "uniform":
        return uniform_1d()
    raise ValueError("window must be gauss or uniform: " + repr(mode))


def filter_sep(img, k1d):
    """Valid sliding-window weighted sums, separable, stride 1.

    Output size is (h-taps+1, w-taps+1). Only pixels fully inside the image
    contribute (no boundary padding); the kernel sums to 1 so results are
    already divided by the total window weight.
    Uniform and gaussian modes share this exact code path.
    """
    h = len(img)
    w = len(img[0])
    taps = len(k1d)
    tmp = [[0.0] * (w - taps + 1) for _ in range(h)]
    for y in range(h):
        row = img[y]
        out = tmp[y]
        for j in range(w - taps + 1):
            acc = 0.0
            # fixed summation order -> identical results under tiling
            for t in range(taps):
                acc += k1d[t] * row[j + t]
            out[j] = acc
    oh, ow = h - taps + 1, w - taps + 1
    out = [[0.0] * ow for _ in range(oh)]
    for i in range(oh):
        dst = out[i]
        for j in range(ow):
            acc = 0.0
            for t in range(taps):
                acc += k1d[t] * tmp[i + t][j]
            dst[j] = acc
    return out


def local_stats(A, B, k1d):
    """Weighted means / variances / covariance for every valid window."""
    muA = filter_sep(A, k1d)
    muB = filter_sep(B, k1d)
    A2 = [[v * v for v in row] for row in A]
    B2 = [[v * v for v in row] for row in B]
    AB = [[A[i][j] * B[i][j] for j in range(len(A[0]))] for i in range(len(A))]
    eA2 = filter_sep(A2, k1d)
    eB2 = filter_sep(B2, k1d)
    eAB = filter_sep(AB, k1d)
    h, w = len(muA), len(muA[0])
    varA = [[0.0] * w for _ in range(h)]
    varB = [[0.0] * w for _ in range(h)]
    cov = [[0.0] * w for _ in range(h)]
    for i in range(h):
        for j in range(w):
            ma, mb = muA[i][j], muB[i][j]
            va = eA2[i][j] - ma * ma
            vb = eB2[i][j] - mb * mb
            cv = eAB[i][j] - ma * mb
            # guard against tiny negative values from cancellation
            varA[i][j] = va if va > 0.0 else 0.0
            varB[i][j] = vb if vb > 0.0 else 0.0
            cov[i][j] = cv if cv > 0.0 else 0.0
    return muA, muB, varA, varB, cov


def ssim_map_from_stats(stats, last_scale=True):
    """SSIM map (last scale) or contrast-structure cs map (other scales).

    CS map uses C3 = C2/2 on the covariance term per the MS-SSIM convention
    cited in the README; SSIM uses C2 on (2*cov + C2).
    """
    muA, muB, vA, vB, cov = stats
    h, w = len(muA), len(muA[0])
    out = [[0.0] * w for _ in range(h)]
    for i in range(h):
        for j in range(w):
            ma, mb = muA[i][j], muB[i][j]
            va, vb, cv = vA[i][j], vB[i][j], cov[i][j]
            lum = (2.0 * ma * mb + C1) / (ma * ma + mb * mb + C1)
            if last_scale:
                cs = (2.0 * cv + C2) / (va + vb + C2)
            else:
                cs = (2.0 * cv + C3) / (va + vb + C3)
            out[i][j] = lum * cs
    return out


def mean2(m):
    n = len(m) * len(m[0])
    if n == 0:
        return 1.0
    total = 0.0
    for row in m:
        total += sum(row)
    return total / n


def downsample2(img):
    """2x mean pooling; an odd trailing row/column is discarded (truncation).

    Order: pooling happens BEFORE the statistics of the next scale.
    """
    h, w = len(img), len(img[0])
    h2, w2 = h // 2, w // 2
    out = [[0.0] * w2 for _ in range(h2)]
    for i in range(h2):
        y = 2 * i
        src0, src1 = img[y], img[y + 1]
        dst = out[i]
        for j in range(w2):
            x = 2 * j
            dst[j] = (src0[x] + src0[x + 1] + src1[x] + src1[x + 1]) * 0.25
    return out


def _split(n, blocks):
    """Deterministic even grid split of n positions into blocks parts."""
    base, rem = divmod(n, blocks)
    edges, start = [], 0
    for b in range(blocks):
        size = base + (1 if b < rem else 0)
        edges.append((start, start + size))
        start += size
    return edges


def ssim_map_tiled(A, B, k1d, blocks=1, last_scale=True):
    """Tiled computation; blocks=1 is the direct single-tile path.

    Each tile gets a halo of radius taps//2 on its inputs so every output
    pixel is produced by exactly the same arithmetic as the full-image path.
    """
    taps = len(k1d)
    h, w = len(A), len(A[0])
    oh, ow = h - taps + 1, w - taps + 1
    if blocks <= 1 or oh < blocks or ow < blocks:
        return ssim_map_from_stats(local_stats(A, B, k1d), last_scale)

    result = [[0.0] * ow for _ in range(oh)]
    for i0, i1 in _split(oh, blocks):
        for j0, j1 in _split(ow, blocks):
            subA = [row[j0:j1 + taps - 1] for row in A[i0:i1 + taps - 1]]
            subB = [row[j0:j1 + taps - 1] for row in B[i0:i1 + taps - 1]]
            tile = ssim_map_from_stats(local_stats(subA, subB, k1d),
                                       last_scale)
            for u in range(i1 - i0):
                dst = result[i0 + u]
                src = tile[u]
                for v in range(j1 - j0):
                    dst[j0 + v] = src[v]
    return result


def multiscale(A, B, mode, scales, block):
    """Downsample first, then per-scale stats; cs per level, ssim last."""
    k1d = kernel_for(mode)
    taps = len(k1d)
    # After (scales-1) halvings the smallest level must fit one full window:
    # floor(h / 2**(scales-1)) >= taps (same for width). Odd remainders are
    # truncated by downsample2, hence the floor on each level.
    min_h = len(A) >> (scales - 1)
    min_w = len(A[0]) >> (scales - 1)
    if min_h < taps or min_w < taps:
        raise ValueError(
            "image %dx%d too small for %d scales with %d-tap window "
            "(smallest level %dx%d, need >= %d)"
            % (len(A), len(A[0]), scales, taps, min_h, min_w, taps))
    per_scale = []
    curA, curB = A, B
    last_map = None
    for s in range(scales):
        is_last = (s == scales - 1)
        m = ssim_map_tiled(curA, curB, k1d, block, last_scale=is_last)
        per_scale.append(mean2(m))
        last_map = m
        if not is_last:
            # truncation of the odd remainder happens here, before next level
            curA = downsample2(curA)
            curB = downsample2(curB)
    # Aggregate MS-SSIM as the product of per-level means (equal exponents),
    # the standard Wang et al. combination; per_scale keeps each level mean.
    ssim_value = 1.0
    for v in per_scale:
        ssim_value *= v
    return ssim_value, last_map, per_scale


def compute(req):
    A = [list(map(float, row)) for row in req["ref"]]
    B = [list(map(float, row)) for row in req["dist"]]
    mode = req.get("window", "gauss")
    scales = int(req.get("scales", 1))
    block = int(req.get("block", 1))
    if scales < 1:
        raise ValueError("scales must be >= 1")
    if block not in (1, 2, 4):
        raise ValueError("block must be one of 1, 2, 4")
    ssim_value, out_map, per_scale = multiscale(A, B, mode, scales, block)
    digest = hashlib.sha1(
        json.dumps(out_map, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "ssim": ssim_value,
        "map": out_map,
        "per_scale": per_scale,
        "digest": digest,
    }


def main():
    req = json.load(open(sys.argv[1]))
    res = compute(req)
    with open(sys.argv[2], "w") as f:
        json.dump(res, f)


if __name__ == "__main__":
    main()
