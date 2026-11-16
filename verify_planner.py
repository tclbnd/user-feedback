#!/usr/bin/env python3
"""Independent brute-force verifier for planner.py.

The reference model does not reuse the planner.  It builds an explicit
per-decoded-sample truth table for each track: a sample plays iff it is inside
the inclusive playable interval and not covered by any priming/padding marker.
It then concatenates the surviving samples in decoded order, which yields the
golden global audio.  Every planner slice is expanded back into sample numbers
and compared one by one against that golden sequence.
"""

import random

import planner


def golden(payload):
    """Return (golden samples, per-track metadata) for a payload."""
    rate = payload["sample_rate"]
    golden_samples = []
    tracks = []

    for track in payload["tracks"]:
        try:
            frames = track["frames"]
            counts = [frame["samples"] for frame in frames]
            start, end = track["playable"]
        except Exception:  # malformed track: planner must reject it
            tracks.append(None)
            continue
        if any(not isinstance(c, int) or isinstance(c, bool) or c < 0 for c in counts):
            tracks.append(None)
            continue
        total = sum(counts)
        offsets = []
        acc = 0
        for count in counts:
            offsets.append(acc)
            acc += count
        if not (-1 <= end <= total - 1 and 0 <= start <= total):
            tracks.append(None)
            continue

        blocked = [False] * total
        for index, frame in enumerate(frames):
            offset = offsets[index]
            priming = frame.get("priming", 0)
            padding = frame.get("padding", 0)
            for i in range(max(0, offset), min(total, offset + priming)):
                blocked[i] = True
            for i in range(max(0, offset + counts[index] - padding), min(total, offset + counts[index])):
                blocked[i] = True

        played = [
            i for i in range(max(0, start), min(total, end + 1)) if not blocked[i]
        ]
        golden_samples.extend((track["id"], i) for i in played)
        tracks.append(
            {
                "id": track["id"],
                "total": total,
                "counts": counts,
                "offsets": offsets,
                "played": played,
            }
        )
    return golden_samples, tracks, rate


def expand(result, metas_by_id):
    """Expand planner slices into (track, decoded sample, frame offset) triples."""
    out = []
    for track in result["tracks"]:
        if "error" in track:
            continue
        meta = metas_by_id[track["id"]]
        for item in track["slices"]:
            frame = item["frame"]
            offsets = meta["offsets"]
            counts = meta["counts"]
            assert 0 <= frame < len(counts), (track["id"], item)
            assert 0 <= item["start"] <= item["end"] <= counts[frame], (
                "slice outside frame",
                track["id"],
                item,
            )
            for local in range(item["start"], item["end"]):
                out.append((track["id"], offsets[frame] + local, offsets[frame]))
        assert track["samples"] == sum(s["end"] - s["start"] for s in track["slices"])
    return out


def check_mapping(result, expanded):
    """Global mapping must be contiguous, gap-free and reversible."""
    for output, (track_id, sample, frame_offset) in enumerate(expanded):
        found = planner.locate(result, output)
        assert found is not None, ("unmapped output sample", output)
        assert found["track"] == track_id, (output, found, track_id)
        # ``locate`` reports a frame-local sample; translate for comparison.
        assert found["sample"] == sample - frame_offset, (output, found, sample)
        back = planner.source_to_output(
            result, track_id, found["frame"], found["sample"]
        )
        assert back == output, (output, track_id, sample, back)
    assert planner.locate(result, len(expanded)) is None

    cursor = 0
    for track in result["tracks"]:
        assert track["global_start"] == cursor, track
        assert track["global_end"] == cursor + track["samples"], track
        # A track's own mapping must tile its global range exactly: no gap and
        # no overlap at frame joins.
        inner = track["global_start"]
        for segment in track["mapping"]:
            assert segment["output_start"] == inner, (track["id"], segment, inner)
            assert segment["output_end"] > segment["output_start"], segment
            assert 0 <= segment["start"] < segment["end"], segment
            inner = segment["output_end"]
        assert inner == track["global_end"], track
        cursor += track["samples"]
    assert cursor == result["samples"] == len(expanded)

    # Every output sample must resolve, and no two output positions may map to
    # the same source sample: that is exactly the "no duplicate, no gap" rule
    # across track joins and empty tracks.
    resolved = [planner.locate(result, i) for i in range(len(expanded))]
    assert all(hit is not None for hit in resolved), "unmapped output sample"
    sources = [(hit["track"], hit["frame"], hit["sample"]) for hit in resolved]
    assert len(set(sources)) == len(sources), "duplicate source sample"
    assert planner.locate(result, len(expanded)) is None


def check(payload, label=""):
    result = planner.plan_payload(payload)
    expected, metas, _rate = golden(payload)

    metas_by_id = {}
    for meta in metas:
        if meta is not None:
            metas_by_id[meta["id"]] = meta

    for index, track in enumerate(result["tracks"]):
        if metas[index] is None:
            assert "error" in track, (label, "golden rejects but planner accepted", track)
            assert track["slices"] == [] and track["samples"] == 0, track
        else:
            assert "error" not in track, (label, track)

    expanded = expand(result, metas_by_id)
    actual = [(track_id, sample) for track_id, sample, _offset in expanded]
    assert actual == expected, (
        label,
        "first divergence",
        next(
            ((i, a, b) for i, (a, b) in enumerate(zip(actual, expected)) if a != b),
            (min(len(actual), len(expected)), len(actual), len(expected)),
        ),
    )
    check_mapping(result, expanded)

    rate = payload["sample_rate"]
    assert result["sample_rate"] == rate
    print(
        "ok %-22s tracks=%d samples=%d rate=%s"
        % (label, len(payload["tracks"]), result["samples"], rate)
    )


def frame(samples, priming=0, padding=0):
    return {"samples": samples, "priming": priming, "padding": padding}


def cases():
    yield "basic", {
        "sample_rate": 48000,
        "tracks": [
            {"id": "a", "frames": [frame(4, 1, 1), frame(4, 0, 2)], "playable": [0, 7]},
            {"id": "b", "frames": [frame(3)], "playable": [0, 2]},
        ],
    }
    # Padding on the final frame reaches back across the frame boundary.
    yield "padding-crosses", {
        "sample_rate": 44100,
        "tracks": [
            {"id": "a", "frames": [frame(4), frame(4, 0, 6)], "playable": [0, 7]},
        ],
    }
    # Priming on the first frame reaches forward across the boundary.
    yield "priming-crosses", {
        "sample_rate": 44100,
        "tracks": [
            {"id": "a", "frames": [frame(4, 6), frame(4)], "playable": [0, 7]},
        ],
    }
    # Jump at exactly the frame boundary, empty and single-frame intervals.
    yield "frame-end-jump", {
        "sample_rate": 8000,
        "tracks": [
            {"id": "a", "frames": [frame(5, 0, 0), frame(5, 0, 0), frame(5, 0, 0)], "playable": [5, 9]},
            {"id": "empty", "frames": [frame(5)], "playable": [3, 2]},
            {"id": "one", "frames": [frame(5), frame(5)], "playable": [6, 6]},
            {"id": "tail", "frames": [frame(5), frame(5)], "playable": [4, 5]},
        ],
    }
    # Zero-length frame and markers spanning multiple frames.
    yield "zero-frame", {
        "sample_rate": 16000,
        "tracks": [
            {"id": "a", "frames": [frame(4), frame(0, 2), frame(4, 0, 3)], "playable": [0, 7]},
        ],
    }
    # Fully trimmed track plus a still-playable neighbour.
    yield "all-trimmed", {
        "sample_rate": 22050,
        "tracks": [
            {"id": "mute", "frames": [frame(4, 2, 2)], "playable": [0, 3]},
            {"id": "live", "frames": [frame(6)], "playable": [0, 5]},
        ],
    }
    # Consecutive playback: three tracks, one empty between two live ones.
    yield "serial", {
        "sample_rate": 48000,
        "tracks": [
            {"id": "s1", "frames": [frame(8, 2, 3), frame(8, 0, 0)], "playable": [0, 15]},
            {"id": "gap", "frames": [frame(6)], "playable": [4, 2]},
            {"id": "s2", "frames": [frame(5, 1, 1)], "playable": [0, 4]},
        ],
    }
    # Whole segment: the entire decoded track is playable, edges included.
    yield "whole", {
        "sample_rate": 96000,
        "tracks": [
            {"id": "w", "frames": [frame(3), frame(3), frame(3)], "playable": [0, 8]},
        ],
    }
    # Jump exactly at a frame end, into the next frame's first sample.
    yield "frame-end-jump-2", {
        "sample_rate": 44100,
        "tracks": [
            {"id": "j", "frames": [frame(5, 1, 2), frame(5, 1, 2), frame(5, 0, 0)], "playable": [3, 14]},
        ],
    }
    # Trailing padding that spans two frames: 11 padding on the last 4-sample
    # frame eats that frame plus all of the second and part of the first.
    yield "padding-spans-two", {
        "sample_rate": 48000,
        "tracks": [
            {"id": "p", "frames": [frame(4), frame(4), frame(4, 0, 11)], "playable": [0, 11]},
        ],
    }
    # Priming that spans two frames at the head, mirrored to the case above.
    yield "priming-spans-two", {
        "sample_rate": 48000,
        "tracks": [
            {"id": "p", "frames": [frame(4, 11), frame(4), frame(4)], "playable": [0, 11]},
        ],
    }


def random_case(rng):
    tracks = []
    for t in range(rng.randint(1, 4)):
        style = rng.random()
        frames = []
        for _ in range(rng.randint(1, 8)):
            if style < 0.5:
                # Realistic encoder output: small trims relative to the frame.
                samples = rng.choice([8, 12, 16, 20, 24])
                priming = rng.randint(0, min(4, samples))
                padding = rng.randint(0, min(4, samples))
            elif style < 0.8:
                # Stress: markers allowed to outgrow their frame and cross.
                samples = rng.choice([0, 1, 2, 3, 5, 9])
                priming = rng.randint(0, 14)
                padding = rng.randint(0, 14)
            else:
                # Clean frames, no trims at all.
                samples = rng.choice([1, 2, 3, 4, 6])
                priming = padding = 0
            frames.append(frame(samples, priming, padding))
        total = sum(f["samples"] for f in frames)
        if total == 0:
            start, end = 0, -1
        elif rng.random() < 0.2:
            start, end = rng.randint(0, total), rng.randint(-1, total - 1)
        else:
            a = rng.randint(0, total - 1)
            b = rng.randint(a, total - 1)
            start, end = a, b
        tracks.append(
            {"id": "t%d" % t, "frames": frames, "playable": [start, end]}
        )
    return {"sample_rate": rng.choice([8000, 44100, 48000]), "tracks": tracks}


def random_invalid(rng):
    # Track ids must be unique: the reverse map is keyed by id, so duplicate
    # ids would be genuinely ambiguous rather than an invalid-metadata case.
    first = {"id": "good-0", "frames": [frame(4, 1, 1)], "playable": [0, 3]}
    last = {"id": "good-1", "frames": [frame(4, 1, 1)], "playable": [0, 3]}
    bad_choices = [
        {"id": "outside", "frames": [frame(4)], "playable": [0, 4]},
        {"id": "negative", "frames": [frame(4)], "playable": [-1, 2]},
        {"id": "end-before", "frames": [frame(4)], "playable": [3, -2]},
        {"id": "no-frames", "frames": [], "playable": [0, 0]},
        {"id": "bad-samples", "frames": [{"samples": -3}], "playable": [0, 0]},
        {"id": "float-samples", "frames": [{"samples": 2.5}], "playable": [0, 1]},
        {"id": "missing-playable", "frames": [frame(4)]},
        {"id": "bad-priming", "frames": [frame(4, -1)], "playable": [0, 3]},
        {"id": "bool-samples", "frames": [{"samples": True}], "playable": [0, 0]},
        {"id": "bad-playable-shape", "frames": [frame(4)], "playable": [0]},
    ]
    rng.shuffle(bad_choices)
    return {
        "sample_rate": 32000,
        "tracks": [first] + bad_choices[: rng.randint(1, 3)] + [last],
    }


def check_frame_end_jumps():
    """Skipping to a frame's last sample must land on exactly that sample."""
    payload = {
        "sample_rate": 48000,
        "tracks": [
            {"id": "j", "frames": [frame(6), frame(6), frame(6)], "playable": [0, 17]}
        ],
    }
    result = planner.plan_payload(payload)
    for output in (0, 5, 6, 11, 12, 17):
        hit = planner.locate(result, output)
        assert hit is not None, output
        expected_frame, expected_local = divmod(output, 6)
        assert (hit["frame"], hit["sample"]) == (expected_frame, expected_local), (output, hit)
    # One past the end is not part of the output.
    assert planner.locate(result, 18) is None
    print("ok frame-end-jumps      6 probes across 3 frames")


def main():
    for name, payload in cases():
        check(payload, name)
    check_frame_end_jumps()

    rng = random.Random(20261117)
    for i in range(120):
        check(random_case(rng), "random-%03d" % i)

    good_meta = {
        "good-0": {"total": 4, "counts": [4], "offsets": [0]},
        "good-1": {"total": 4, "counts": [4], "offsets": [0]},
    }
    for i in range(40):
        payload = random_invalid(rng)
        result = planner.plan_payload(payload)
        errors = [t for t in result["tracks"] if "error" in t]
        live = [t for t in result["tracks"] if "error" not in t]
        assert len(live) == 2 and all(t["samples"] == 2 for t in live), result
        assert [t["id"] for t in live] == ["good-0", "good-1"], result
        assert sum(t["samples"] for t in live) == result["samples"] == 4, result
        check_mapping(result, expand(result, good_meta))
        assert errors
        print("ok invalid-%03d             rejected=%d kept=2" % (i, len(errors)))

    print("all checks passed")


if __name__ == "__main__":
    main()
