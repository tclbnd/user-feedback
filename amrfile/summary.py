"""Voicemail summary: length, mode usage, comfort noise."""
from .frames import BAD, CODECS, NO_DATA, SID, SPEECH, read

_COUNTED = {
    SPEECH: "speech_frames",
    SID: "sid_frames",
    NO_DATA: "no_data_frames",
    BAD: "bad_frames",
}


def summarise(data):
    info, frames = read(data)
    kbps = CODECS[info["codec"]]["kbps"]
    modes = {}
    counts = {name: 0 for name in _COUNTED.values()}
    for f in frames:
        counts[_COUNTED[f["kind"]]] += 1
        if f["kind"] == SPEECH:
            key = kbps[f["ft"]]
            modes[key] = modes.get(key, 0) + 1
    return {**info, "frames": len(frames), "duration_ms": 20 * len(frames),
            **counts, "modes": modes}
