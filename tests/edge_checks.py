"""Ad-hoc boundary checks beyond the unittest suite."""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import convert  # noqa: E402
import reference  # noqa: E402

rng = random.Random(7)

# 1x1 frames for both sitings and ranges.
for siting in ("center", "left"):
    for range_kind in ("limited", "full"):
        frame = {
            "width": 1,
            "height": 1,
            "metadata": {
                "primaries": "bt2020", "transfer": "pq",
                "matrix": "bt2020nc", "range": range_kind, "siting": siting,
            },
            "y": [[rng.randint(64, 940)]],
            "cb": [[rng.randint(64, 960)]],
            "cr": [[rng.randint(64, 960)]],
        }
        if range_kind == "full":
            try:
                convert.convert(frame)
                raise SystemExit("full range must fail in single mode")
            except ValueError:
                pass
        seq = {
            "time_base": [1, 30000],
            "target_siting": "left",
            "frames": [{"pts": 0, "duration": 2, "frame": frame}],
        }
        out = convert.convert_sequence(seq, seed=123)
        ry, rcb, rcr, _, _ = reference.reference_pipeline(
            frame, out["shots"][0]["white_nits"], "left")
        got_frame = out["frames"][0]["frame"]
        for name, got, ref in (
                ("y", got_frame["y"], ry),
                ("cb", got_frame["cb"], rcb),
                ("cr", got_frame["cr"], rcr)):
            diff = abs(got[0][0] - ref[0][0])
            assert diff <= 1, (siting, range_kind, name, diff)
        ticks, p95, white = reference.reference_shot_stats(seq["frames"])
        assert ticks == 2 and p95 == out["shots"][0]["p95_nits"]
        assert abs(white - out["shots"][0]["white_nits"]) < 1e-12
print("1x1 / full-range / dither-vs-reference: OK")

# Exact 95% crossing with unequal integer frame durations.
meta = {
    "primaries": "bt2020", "transfer": "pq", "matrix": "bt2020nc",
    "range": "limited", "siting": "center",
}
f2 = {
    "width": 2, "height": 1, "metadata": meta,
    "y": [[64, 940]], "cb": [[512]], "cr": [[512]],
}
seq = {
    "time_base": [1, 1], "target_siting": "center",
    "frames": [
        {"pts": 0, "duration": 3, "frame": f2},
        {"pts": 3, "duration": 1, "frame": f2},
    ],
}
out = convert.convert_sequence(seq)
shot = out["shots"][0]
ticks, p95, white = reference.reference_shot_stats(seq["frames"])
assert shot["pixel_ticks"] == ticks == 8
assert shot["p95_nits"] == p95
assert shot["white_nits"] == white and shot["white_nits"] >= 203
print("P95 weighted boundary:", shot, "truth:", (ticks, p95, white))

# Dark shots keep the 203-nit floor.
dark = {
    "width": 2, "height": 2, "metadata": meta,
    "y": [[64, 70], [72, 80]],
    "cb": [[512]],
    "cr": [[512]],
}
dark_seq = {
    "time_base": [1, 1], "target_siting": "center",
    "frames": [{"pts": 0, "duration": 1, "frame": dark}],
}
out2 = convert.convert_sequence(dark_seq)
assert out2["shots"][0]["white_nits"] == 203.0
print("dark shot white floor:", out2["shots"][0])

# Peak-PQ frame lands in the 10000 nit bucket without overflow.
peak = {
    "width": 1, "height": 1, "metadata": meta,
    "y": [[940]], "cb": [[512]], "cr": [[512]],
}
peak_out = convert.convert_sequence({
    "time_base": [1, 1], "target_siting": "center",
    "frames": [{"pts": 0, "duration": 1, "frame": peak}],
})
peak_shot = peak_out["shots"][0]
assert peak_shot["p95_nits"] <= 10000
print("peak frame shot:", peak_shot)

# JSON round trip keeps integer PTS/durations and rational time base.
blob = json.dumps(out, allow_nan=False)
reloaded = json.loads(blob)
assert reloaded["time_base"] == [1, 1]
for entry in reloaded["frames"]:
    assert type(entry["pts"]) is int
    assert type(entry["duration"]) is int
    assert type(entry["shot_start_pts"]) is int
for shot in reloaded["shots"]:
    assert type(shot["start_pts"]) is int
    assert type(shot["pixel_ticks"]) is int
    assert type(shot["p95_nits"]) is int
print("integer timestamp contract: OK")
