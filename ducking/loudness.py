"""Short-term loudness analysis, voice confidence and integrated LUFS (pure Python)."""
import math
from .filters import Biquad, kweighting_coeffs

MIN_DB = -120.0


def to_db(x, floor=1e-12):
    return 20.0 * math.log10(max(x, floor))


# --------------------------------------------------------------------------- #
# K-weighted mean-square frame analysis
# --------------------------------------------------------------------------- #
def kweighted_ms_frames(samples, fs, win_s=0.1, hop_s=0.05):
    """Run K-weighting once, then hop a window and return per-frame mean square.

    A sliding sum-of-squares makes this O(n) instead of O(n * window).
    Frames are centred on an absolute time grid: frame k covers
    [k*hop - win/2, k*hop + win/2), giving a fixed frame count for any signal,
    independent of any processing chunking.
    """
    shelf_c, hpf_c = kweighting_coeffs(fs)
    f1, f2 = Biquad(shelf_c), Biquad(hpf_c)
    kw = f2.run(f1.run(samples))  # deterministic full-track analysis pass
    n = len(kw)
    win = max(1, int(round(win_s * fs)))
    hop = max(1, int(round(hop_s * fs)))
    half = win // 2
    n_frames = max(1, int(math.ceil(max(n - 1, 0) / hop)) + 1)
    sq = [v * v for v in kw]
    frames = []
    for k in range(n_frames):
        c = k * hop
        lo, hi = c - half, c - half + win
        lo_c = max(lo, 0)
        hi_c = min(hi, n)
        if hi_c <= lo_c:
            frames.append(0.0)
            continue
        s = sum(sq[lo_c:hi_c])
        frames.append(s / (hi_c - lo_c))  # mean square over actual samples
    return frames, hop, win


def ms_to_lufs(ms):
    """Mono K-weighted loudness (stereo gating constant +0.691 dB omitted for
    a single channel; everything is mono here)."""
    if ms <= 0.0:
        return MIN_DB
    return -0.691 + 10.0 * math.log10(ms)


def integrated_lufs(samples, fs, block_s=0.4, hop_s=0.1):
    """BS.1770-4 integrated loudness with absolute (-70 LUFS) and relative
    (-10 LU) gates, using 400 ms gating blocks."""
    ms_blocks, _, _ = kweighted_ms_frames(samples, fs, block_s, hop_s)
    blocks = [ms_to_lufs(m) for m in ms_blocks]
    abs_pass = [b for b in blocks if b > -70.0]
    if not abs_pass:
        return MIN_DB
    mean_abs = 10.0 * math.log10(
        sum(10.0 ** (b / 10.0) for b in abs_pass) / len(abs_pass)
    )
    rel_thr = mean_abs - 10.0
    rel_pass = [b for b in abs_pass if b >= rel_thr]
    if not rel_pass:
        return mean_abs
    return 10.0 * math.log10(
        sum(10.0 ** (b / 10.0) for b in rel_pass) / len(rel_pass)
    )


# --------------------------------------------------------------------------- #
# Voice activity confidence (0..1): energy + spectral tilt + zero-cross rate,
# temporal one-pole smoothing, noise-floor gating.
# --------------------------------------------------------------------------- #
def _frame_rms(samples, lo, hi):
    lo = max(lo, 0)
    hi = min(hi, len(samples))
    if hi <= lo:
        return 0.0
    return math.sqrt(sum(x * x for x in samples[lo:hi]) / (hi - lo))


def _zero_cross_rate(samples, lo, hi):
    lo = max(lo, 1)
    hi = min(hi, len(samples))
    if hi <= lo:
        return 0.0
    zc = 0
    prev = samples[lo - 1]
    for i in range(lo, hi):
        x = samples[i]
        if (x >= 0.0) != (prev >= 0.0):
            zc += 1
        prev = x
    return zc / (hi - lo)


def _spectral_centroid_norm(samples, lo, hi, fs):
    """Normalised spectral centroid from a small zero-padded DFT (power-of-two)."""
    lo = max(lo, 0)
    hi = min(hi, len(samples))
    seg = samples[lo:hi]
    if len(seg) < 8:
        return 0.0
    nfft = 1
    while nfft < len(seg):
        nfft <<= 1
    nfft = min(nfft, 1024)
    # naive DFT magnitude on a down-selected length keeps stdlib cost bounded
    seg = seg[:nfft]
    re = [0.0] * nfft
    im = [0.0] * nfft
    for k in range(nfft // 2):
        sr = si = 0.0
        for t, x in enumerate(seg):
            ang = -2.0 * math.pi * k * t / nfft
            sr += x * math.cos(ang)
            si += x * math.sin(ang)
        re[k], im[k] = sr, si
    num = den = 0.0
    for k in range(1, nfft // 2):
        mag = math.sqrt(re[k] * re[k] + im[k] * im[k])
        num += k * mag
        den += mag
    if den <= 0.0:
        return 0.0
    centroid_bin = num / den
    return min(1.0, centroid_bin / (nfft // 2))


def voice_confidence_frames(voice, fs, hop, win, n_frames,
                            noise_db=-55.0, smooth=0.35):
    half = win // 2
    raw = []
    floor = 10.0 ** (noise_db / 20.0)
    for k in range(n_frames):
        c = k * hop
        lo, hi = c - half, c - half + win
        rms = _frame_rms(voice, lo, hi)
        level_db = to_db(rms)
        # energy membership: -55..-30 dBFS maps to 0..1
        e = min(1.0, max(0.0, (level_db - noise_db) / 25.0))
        zcr = _zero_cross_rate(voice, lo, hi)
        # voiced speech: moderate ZCR (roughly 0.02..0.25 at 16 k)
        z = 1.0 - min(1.0, abs(zcr - 0.10) / 0.20)
        sc = _spectral_centroid_norm(voice, lo, hi, fs)
        # speech band-limited: centroid in lower ~55% of Nyquist
        s = 1.0 - min(1.0, max(0.0, (sc - 0.18) / 0.45))
        c = (0.65 * e + 0.20 * z + 0.15 * s)
        if rms < floor:
            c = 0.0
        raw.append(min(1.0, max(0.0, c)))
    # temporal one-pole smoothing (causal, fixed coefficient -> deterministic)
    out = [0.0] * n_frames
    prev = 0.0
    for k in range(n_frames):
        prev = smooth * raw[k] + (1.0 - smooth) * prev
        out[k] = prev
    return out, raw
