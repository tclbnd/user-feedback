"""Synthetic acceptance tests for the mixed telecine / interlaced replay.

Run with::

    python3 -m pytest tests/ -q        (if pytest is installed)
    python3 tests/test_acceptance.py   (standalone)

The tests build deterministic fields rather than relying on the demo fixture
so they also exercise native interlaced material inserted in the middle of a
2:3 cycle, cut handling, short shots, truncation and mislabelled parity.
"""

import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import inverse  # noqa: E402


FIELD = Fraction(1001, 60000)
PROG = Fraction(1001, 24000)


def make_frame(level, width=8, height=6):
    """Progressive source picture with small adjacent-scanline changes."""
    return [
        [((level + y * 3 + x * 7) % 256) for x in range(width)]
        for y in range(height)
    ]


def split_fields(frame, start_parity):
    top = frame[0::2]
    bottom = frame[1::2]
    if start_parity == "top":
        return [("top", top), ("bottom", bottom)]
    return [("bottom", bottom), ("top", top)]


def telecine_fields(frames, start_parity, start_index, counts, first_id):
    """Emit a run of 2/3-field telecine groups with exact triple duplicates.

    Parity alternates on every emitted field, just like a real field stream.
    """
    out = []
    field_index = start_index
    fid = first_id
    parity = start_parity
    for frame, count in zip(frames, counts):
        pairs = split_fields(frame, parity)
        for k in range(count):
            p, rows = pairs[k % 2]
            out.append({
                "id": fid,
                "parity": p,
                "rows": rows,
                "cut": False,
            })
            fid += 1
            field_index += 1
            parity = inverse.opposite(parity)
    return out, field_index, fid, parity


def native_fields(count, start_index, first_id, start_parity):
    """Moving native interlaced material: every field disagrees with its neighbour."""
    out = []
    parity = start_parity
    for j in range(count):
        rows = [
            [((31 + 125 * (j % 2) + x * 3) % 256) for x in range(8)]
            for _ in range(3)
        ]
        out.append({
            "id": first_id + j,
            "parity": parity,
            "rows": rows,
            "cut": False,
        })
        parity = inverse.opposite(parity)
    return out, start_index + count, first_id + count, parity


def frame_hash(frame):
    return hashlib.sha256(bytes(v for row in frame for v in row)).hexdigest()


def test_demo_fixture_round_trip():
    import make_demo
    data = json.loads(Path(__file__).resolve().parent.parent.joinpath("demo.json").read_text())
    result = inverse.convert(data)
    expected = make_demo.generate()[1]
    progressive = [f for f in result["frames"] if f["mode"] == "progressive"]
    assert len(progressive) == len(expected)
    for frame, (want_hash, want_ids) in zip(progressive, expected):
        assert frame["sha256"] == want_hash
        assert frame["field_ids"] == want_ids
        assert frame["duration"] == [1001, 24000]
    assert [f["mode"] for f in result["frames"]].count("interlaced") == 3
    assert result["timeline"]["max_abs_drift_fields"] == [0, 1]


def test_native_interlaced_inserted_in_middle_of_cycle():
    # Telecine: 2,3 | native pair | 2,3.  The native fields split the cadence.
    frames = [make_frame(20 + 7 * j) for j in range(4)]
    tele_a, index, fid, parity = telecine_fields(
        frames[:2], "top", 0, [2, 3], 100)
    native, index, fid, parity = native_fields(
        4, index, fid, parity)
    tele_b, index, fid, parity = telecine_fields(
        frames[2:], parity, index, [2, 3], fid)
    fields = tele_a + native + tele_b
    result = inverse.convert({"fields": fields})

    ids = [f["id"] for f in fields]
    assert [f["field_ids"] for f in result["frames"] if f["mode"] == "interlaced"] == [
        [ids[5], ids[6]], [ids[7], ids[8]]
    ]
    for frame in result["frames"]:
        if frame["mode"] == "interlaced":
            assert frame["duration"] == [1001, 30000]
            assert frame["field_order"] == ["bottom", "top"]
        else:
            assert frame["duration"] == [1001, 24000]

    # The native segment keeps its original ids and exact input duration.
    native_segment = [s for s in result["segments"] if s["mode"] == "interlaced"][0]
    assert native_segment["field_ids"] == ids[5:9]
    assert Fraction(*native_segment["duration"]) == 4 * FIELD
    assert native_segment["fps"] == [30000, 1001]

    # Cumulative timing error never exceeds one input field period.
    assert Fraction(*result["timeline"]["max_abs_drift_fields"]) <= 1

    # Progressive hashes still equal the original source frames.
    progressive = [f for f in result["frames"] if f["mode"] == "progressive"]
    for frame, source in zip(progressive, frames):
        assert frame["sha256"] == frame_hash(source)


def test_cuts_reset_matching_and_cannot_be_crossed():
    frames = [make_frame(40 + 11 * j) for j in range(4)]
    shot_a, index, fid, parity = telecine_fields(
        frames[:2], "top", 0, [2, 3], 0)
    shot_b, _, _, _ = telecine_fields(
        frames[2:], "bottom", index, [3, 2], fid)
    shot_b[0]["cut"] = True
    result = inverse.convert({"fields": shot_a + shot_b})
    cut_id = shot_b[0]["id"]
    for frame in result["frames"]:
        assert not (frame["field_ids"][0] < cut_id and cut_id in frame["field_ids"])
    cuts = result["timeline"]["cut_field_indices"]
    assert cuts == [index]


def test_short_shot_and_odd_native_run_keep_leftover_field():
    # One lone field must survive, unweaved, with its own diagnostic.
    lone = [{
        "id": 7, "parity": "top", "rows": make_frame(50)[:3], "cut": True,
    }]
    result = inverse.convert({"fields": lone})
    assert result["frames"][0]["mode"] == "interlaced"
    assert result["frames"][0]["field_ids"] == [7]
    assert result["frames"][0]["duration"] == [1001, 60000]
    codes = [d["code"] for d in result["diagnostics"]]
    assert "leftover_field" in codes

    # An odd-length native run yields pairs plus one single field.
    native, _, _, _ = native_fields(5, 1, 20, "top")
    result2 = inverse.convert({"fields": native})
    counts = [f["field_count"] for f in result2["frames"]]
    assert counts == [2, 2, 1]
    assert Fraction(*result2["timeline"]["total_duration"]) == 5 * FIELD


def test_truncated_field_is_diagnosed_and_never_weaved():
    frame = make_frame(90)
    top, bottom = frame[0::2], frame[1::2]
    fields = [
        {"id": 0, "parity": "top", "rows": top[:2], "cut": False},
        {"id": 1, "parity": "bottom", "rows": bottom, "cut": True},
    ]
    result = inverse.convert({"fields": fields})
    codes = [d["code"] for d in result["diagnostics"]]
    assert "truncated_field" in codes
    # No progressive frame can be built from a structurally broken field.
    assert all(f["mode"] == "interlaced" for f in result["frames"])


def test_mislabelled_parity_is_repaired_and_diagnosed():
    frame = make_frame(130)
    top, bottom = frame[0::2], frame[1::2]
    fields = [
        {"id": 0, "parity": "top", "rows": top, "cut": False},
        # Wrong label: should be "bottom", but is marked "top".
        {"id": 1, "parity": "top", "rows": bottom, "cut": False},
        {"id": 2, "parity": "top", "rows": top, "cut": False},
    ]
    result = inverse.convert({"fields": fields})
    codes = [d["code"] for d in result["diagnostics"]]
    assert "parity_mismatch" in codes
    # The credible weave is still reconstructed once the label is repaired.
    assert any(f["mode"] == "progressive" for f in result["frames"])


def test_repeated_static_frames_keep_distinct_frames():
    frame = make_frame(170)
    # Two identical source frames sent as a 2-field then a 3-field group.
    a, index, fid, parity = telecine_fields([frame, frame], "top", 0, [2, 3], 0)
    result = inverse.convert({"fields": a})
    progressive = [f for f in result["frames"] if f["mode"] == "progressive"]
    assert len(progressive) == 2
    assert progressive[0]["field_count"] == 2
    assert progressive[1]["field_count"] == 3
    assert progressive[0]["sha256"] == frame_hash(frame)
    assert progressive[1]["sha256"] == frame_hash(frame)
    # The duplicate third field is listed but not used for the weave.
    assert len(progressive[1]["field_ids"]) == 3


def test_rational_clock_is_exact_and_re_anchored_at_cuts():
    frames = [make_frame(5 + 9 * j) for j in range(4)]
    shot_a, index, fid, parity = telecine_fields(
        frames[:2], "bottom", 0, [3, 2], 0)
    shot_b, _, _, _ = telecine_fields(
        frames[2:], "top", index, [2, 3], fid)
    shot_b[0]["cut"] = True
    result = inverse.convert({"fields": shot_a + shot_b})
    for frame in result["frames"]:
        Fraction(*frame["pts"])
        Fraction(*frame["duration"])
    total = sum((Fraction(*f["duration"]) for f in result["frames"]), Fraction(0))
    # Re-anchoring at a cut keeps the whole timeline on the field clock.
    assert total == (len(shot_a) + len(shot_b)) * FIELD
    assert Fraction(*result["timeline"]["max_abs_drift_fields"]) <= 1


def test_spurious_cut_marker_is_honoured_and_diagnosed():
    frame = make_frame(210)
    top, bottom = frame[0::2], frame[1::2]
    fields = [
        {"id": 0, "parity": "top", "rows": top, "cut": False},
        {"id": 1, "parity": "bottom", "rows": bottom, "cut": True},
        {"id": 2, "parity": "top", "rows": top, "cut": False},
    ]
    result = inverse.convert({"fields": fields})
    codes = [d["code"] for d in result["diagnostics"]]
    assert "cut_without_discontinuity" in codes
    # The marker is still honoured: fields 0 and 1 are never in one frame.
    for frame_out in result["frames"]:
        assert frame_out["field_ids"] != [0, 1]
    assert result["timeline"]["cut_field_indices"] == [1]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print("FAIL", test.__name__, "-", exc)
        else:
            print("ok  ", test.__name__)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()