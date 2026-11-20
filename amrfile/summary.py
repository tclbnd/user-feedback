"""Voicemail summary: length, mode usage, comfort noise."""
from .frames import NB_KBPS, read


def summarise(data):
    info, frames = read(data)
    modes = {}
    speech = 0
    for f in frames:
        if f["ft"] <= 8:
            speech += 1
            key = NB_KBPS[f["ft"]] if f["ft"] < 8 else "sid"
            modes[key] = modes.get(key, 0) + 1
    return {**info, "frames": len(frames), "duration_ms": 20 * len(frames), "speech_frames": speech,
            "sid_frames": 0, "no_data_frames": 0, "bad_frames": 0, "modes": modes}
