"""Energy based voice activity for call recordings."""


def frames(samples, size=160):
    return [samples[i:i + size] for i in range(0, len(samples) - size + 1, size)]


def energy(frame):
    return sum(s * s for s in frame) / len(frame)


def active(samples, threshold=1e5, hangover=3):
    """Per 20 ms frame (8 kHz) True when speech is present, with a short hangover."""
    out, hold = [], 0
    for f in frames(samples):
        if energy(f) >= threshold:
            hold = hangover
            out.append(True)
        elif hold:
            hold -= 1
            out.append(True)
        else:
            out.append(False)
    return out
