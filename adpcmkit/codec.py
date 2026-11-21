"""Block decoders for IMA ADPCM (0x0011) and Microsoft ADPCM (0x0002)."""
import struct

from .tables import IMA_INDEX, IMA_STEPS, MS_ADAPT, MS_COEFS


def _clamp(v):
    return -32768 if v < -32768 else 32767 if v > 32767 else v


def ima_samples_per_block(align, channels):
    return (align - 4 * channels) * 2 // channels


def ima_block(block, channels):
    """Decode one IMA ADPCM block into a list of per-channel sample lists."""
    out, pred, index = [], [], []
    for c in range(channels):
        p, i = struct.unpack_from("<hB", block, 4 * c)
        pred.append(p)
        index.append(i)
        out.append([p])
    data = block[4 * channels:]
    for k, byte in enumerate(data):
        for nib in (byte & 0x0F, byte >> 4):
            c = k % channels
            step = IMA_STEPS[index[c]]
            diff = step >> 3
            if nib & 1:
                diff += step >> 2
            if nib & 2:
                diff += step >> 1
            if nib & 4:
                diff += step
            pred[c] = _clamp(pred[c] - diff if nib & 8 else pred[c] + diff)
            index[c] = index[c] + IMA_INDEX[nib]
            out[c].append(pred[c])
    return out


def ms_samples_per_block(align, channels):
    return (align - 7 * channels) * 2 // channels


def ms_block(block, channels):
    """Decode one Microsoft ADPCM block."""
    out, state = [], []
    pos = 0
    for c in range(channels):
        pred = block[pos]
        delta, s1, s2 = struct.unpack_from("<hhh", block, pos + 1)
        pos += 7
        c1, c2 = MS_COEFS[pred]
        state.append([c1, c2, delta, s1, s2])
        out.append([s1, s2])
    k = 0
    for byte in block[pos:]:
        for nib in (byte & 0x0F, byte >> 4):
            c = k % channels
            k += 1
            c1, c2, delta, s1, s2 = state[c]
            signed = nib - 16 if nib & 8 else nib
            p = _clamp(((s1 * c1 + s2 * c2) >> 8) + signed * delta)
            delta = (MS_ADAPT[nib] * delta) >> 8
            state[c] = [c1, c2, delta, p, s1]
            out[c].append(p)
    return out
