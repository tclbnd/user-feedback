import hashlib
import json
from pathlib import Path
import sys


def analyze(samples, rate, frame_size, hop_size):
    frames = []
    for offset in range(0, len(samples) - frame_size + 1, hop_size):
        frame = samples[offset:offset + frame_size]
        crossings = sum(a <= 0 < b for a, b in zip(frame, frame[1:]))
        hz = crossings * rate / frame_size
        voiced = 70 <= hz <= 600
        frames.append(dict(offset=offset, hz=hz if voiced else None, voiced=voiced,
                           confidence=1.0 if voiced else 0.0))
    voiced_count = sum(row["voiced"] for row in frames)
    digest = hashlib.sha256(json.dumps(frames, sort_keys=True).encode()).hexdigest()
    return dict(frames=frames, stats={"voiced": voiced_count, "unvoiced": len(frames) - voiced_count}, digest=digest)


if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text())
    result = analyze(data["samples"], data["sample_rate"], data["frame_size"], data["hop_size"])
    Path(sys.argv[2]).write_text(json.dumps(result))
