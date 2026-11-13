#!/usr/bin/env python3
"""Generate fully synthetic replay sessions for self-testing.

All data is fabricated locally; nothing is fetched from a network. The
generator simulates how the original collector *recorded* each session and
injects exactly one well-understood discrepancy per diff session.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DIR = os.path.join(HERE, "fixtures")


def fetch(seq, url, response, ts=None, **extra):
    ev = {"seq": seq, "kind": "fetch", "url": url,
          "response": response, "ts": 100 + seq if ts is None else ts}
    ev.update(extra)
    return ev


def parse(seq, ref, fields, **extra):
    ev = {"seq": seq, "kind": "parse", "input_ref": ref,
          "fields": dict(fields)}
    ev.update(extra)
    return ev


def emit(seq, rid, fields_ref, **extra):
    ev = {"seq": seq, "kind": "emit",
          "record": {"id": rid}, "fields_ref": fields_ref}
    ev.update(extra)
    return ev


def build_sessions():
    sessions = {}

    # 0. Clean v1 session: replay must produce the empty diff set.
    sessions["s_clean_v1"] = {
        "session": "s_clean_v1",
        "code_version": "v1",
        "events": [
            fetch(0, "a", "raw-a"),
            parse(1, 0, {"title": " Keep Me ", "n": 1}),
            emit(2, "r1", 1),
        ],
        "snapshots": {"fetch": {"a": "raw-a"}},
        "recorded_outputs": [{"id": "r1", "title": " Keep Me ", "n": 1}],
    }

    # 1. code-change: v2 switch before parse; recorded output kept v1 shape.
    sessions["d1_code_change"] = {
        "session": "d1_code_change",
        "code_version": "v1",
        "events": [
            fetch(0, "a", "raw-a"),
            parse(1, 0, {"title": " Hello World "}, code_version="v2"),
            emit(2, "r1", 1),
        ],
        "snapshots": {"fetch": {"a": "raw-a"}},
        "recorded_outputs": [{"id": "r1", "title": " Hello World "}],
    }

    # 2. input-drift: snapshot response differs from the recorded response.
    sessions["d2_input_drift"] = {
        "session": "d2_input_drift",
        "code_version": "v1",
        "events": [
            fetch(0, "a", {"fields": {"title": "Recorded Title"}}),
            parse(1, 0, {"title": "Recorded Title"}),
            emit(2, "r1", 1),
        ],
        "snapshots": {"fetch": {"a": {"fields": {"title": "Drifted Title"}}}},
        "recorded_outputs": [{"id": "r1", "title": "Recorded Title"}],
    }

    # 3. order-sensitive: seq values are contiguous and semantically correct,
    #    but the physical event order in the log is shuffled, so the
    #    position-sensitive collector appended records in arrival order while
    #    deterministic seq-order replay yields the canonical ordering.
    shuffled = [
        emit(5, "rB", 4),
        parse(4, 3, {"title": "B"}),
        fetch(3, "b", "raw-b"),
        emit(2, "rA", 1),
        parse(1, 0, {"title": "A"}),
        fetch(0, "a", "raw-a"),
    ]
    sessions["d3_order_sensitive"] = {
        "session": "d3_order_sensitive",
        "code_version": "v1",
        "events": shuffled,
        "snapshots": {"fetch": {"a": "raw-a", "b": "raw-b"}},
        # Collector appended in physical arrival order; replay is seq order.
        "recorded_outputs": [
            {"id": "rB", "title": "B"},
            {"id": "rA", "title": "A"},
        ],
    }

    # 4. real-defect: no version switch, no drift, recorded output wrong.
    sessions["d4_real_defect"] = {
        "session": "d4_real_defect",
        "code_version": "v1",
        "events": [
            fetch(0, "a", "raw-a"),
            parse(1, 0, {"title": "Correct"}),
            emit(2, "r1", 1),
        ],
        "snapshots": {"fetch": {"a": "raw-a"}},
        "recorded_outputs": [{"id": "r1", "title": "CORRUPTED"}],
    }

    # 5. real-defect via missing + extra emit (same root cause class).
    sessions["d5_missing_extra"] = {
        "session": "d5_missing_extra",
        "code_version": "v1",
        "events": [
            fetch(0, "a", "raw-a"),
            parse(1, 0, {"title": "A"}),
            emit(2, "r-extra", 1),
        ],
        "snapshots": {"fetch": {"a": "raw-a"}},
        "recorded_outputs": [{"id": "r-missing", "title": "A"}],
    }

    # 6. Clean v3 session exercising trim/lower + empty title -> null.
    sessions["s_clean_v3"] = {
        "session": "s_clean_v3",
        "code_version": "v3",
        "events": [
            fetch(0, "a", "raw-a"),
            parse(1, 0, {"title": " Mixed  Case "}),
            emit(2, "r1", 1),
            fetch(3, "b", "raw-b"),
            parse(4, 3, {"title": "   "}),
            emit(5, "r2", 4),
        ],
        "snapshots": {"fetch": {"a": "raw-a", "b": "raw-b"}},
        "recorded_outputs": [
            {"id": "r1", "title": "mixed  case"},
            {"id": "r2", "title": None},
        ],
    }

    # 7. Truncated log: seq 3 is missing from a 0..5 stream.
    sessions["x_truncated"] = {
        "session": "x_truncated",
        "code_version": "v1",
        "events": [
            fetch(0, "a", "raw-a"),
            parse(1, 0, {"title": "A"}),
            emit(2, "r1", 1),
            # seq 3..5 intentionally absent
            emit(5, "r2", 1),
        ],
        "snapshots": {"fetch": {"a": "raw-a"}},
        "recorded_outputs": [
            {"id": "r1", "title": "A"},
            {"id": "r2", "title": "A"},
        ],
    }

    # 8. Unknown event kind is reported and replay continues.
    sessions["x_unknown_kind"] = {
        "session": "x_unknown_kind",
        "code_version": "v1",
        "events": [
            {"seq": 0, "kind": "teleport", "random": 42},
            fetch(1, "a", "raw-a"),
            parse(2, 1, {"title": "A"}),
            emit(3, "r1", 2),
        ],
        "snapshots": {"fetch": {"a": "raw-a"}},
        "recorded_outputs": [{"id": "r1", "title": "A"}],
    }

    # 9. Version switch spanning multiple ranges; clean, so diff is empty.
    sessions["s_clean_multi_version"] = {
        "session": "s_clean_multi_version",
        "code_version": "v1",
        "events": [
            fetch(0, "a", "raw-a"),
            parse(1, 0, {"title": " First "}),
            emit(2, "r1", 1),
            fetch(3, "b", "raw-b", code_version="v2"),
            parse(4, 3, {"title": " Second "}),
            emit(5, "r2", 4),
        ],
        "snapshots": {"fetch": {"a": "raw-a", "b": "raw-b"}},
        "recorded_outputs": [
            {"id": "r1", "title": " First "},
            {"id": "r2", "title": "second"},
        ],
    }

    return sessions


def main():
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    sessions = build_sessions()
    for name, session in sessions.items():
        path = os.path.join(FIXTURE_DIR, name + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(session, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    print("wrote %d synthetic sessions to %s" % (len(sessions), FIXTURE_DIR))


if __name__ == "__main__":
    main()
