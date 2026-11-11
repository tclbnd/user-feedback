"""Final progress record.

Written last, after every segment and the playlist have been atomically
published, so its presence means the publication for this capture is complete.
A crash before this point simply leaves no (or an older) progress file and the
next run re-publishes the identical deterministic result.
"""

import json
from typing import Optional

from mux import MuxPlan, PLAYLIST_NAME
from playlist import atomic_write


def record_progress(output, session, plan: Optional[MuxPlan] = None) -> None:
    if plan is None:
        raise TypeError("record_progress requires the mux plan")
    state = {
        "epochs": [
            {
                "epoch": epoch.epoch,
                "tracks": {
                    name: {
                        "timescale": spec.timescale,
                        "clock_origin": spec.clock_origin,
                        "last": epoch.last.get(name, 0),
                        "missing": sorted(
                            seq for seq in range(1, epoch.last.get(name, 0) + 1)
                            if seq not in epoch.fragments.get(name, {})
                            or not epoch.fragments[name][seq].complete),
                    }
                    for name, spec in epoch.tracks.items()
                },
            }
            for epoch in session.epochs
        ],
        "segments": [
            {
                "number": segment.number,
                "file": "segment-{:03d}.m4s".format(segment.number),
                "epoch": segment.epoch,
                "source_seq": segment.source_seq,
                "init": segment.init_name,
                "discontinuity": segment.discontinuity,
                "extinf": round(segment.extinf, 6),
                "video_samples": len(segment.video_samples),
                "audio_samples": len(segment.audio_samples),
            }
            for segment in plan.segments
        ],
        "inits": sorted(plan.inits),
        "discontinuities": list(plan.discontinuities),
        "playlist": PLAYLIST_NAME,
    }
    atomic_write(output / "progress.json",
                 (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8"))
