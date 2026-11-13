#!/usr/bin/env python3
"""End-to-end self-test for replay_audit.py.

Runs the real CLI on the synthetic fixtures (including a bare two-argument
invocation to prove CLI compatibility), asserts every injected diff is hit
with the correct cause/location, verifies clean sessions report the empty
set, and checks chunked replay is identical to whole replay for every
chunk size.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import replay_audit  # noqa: E402
import make_fixtures  # noqa: E402

FAILURES = []


def check(condition, message):
    if condition:
        print("  PASS  " + message)
    else:
        FAILURES.append(message)
        print("  FAIL  " + message)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def run_cli(session_path, output_path, chunks=None):
    cmd = [sys.executable, os.path.join(ROOT, "replay_audit.py"),
           session_path, output_path]
    if chunks is not None:
        cmd += ["--chunks", str(chunks)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("CLI failed: %s\n%s" % (cmd, proc.stderr))
    return load(output_path)


def summarize(diff):
    return [(d["seq"], d["kind"], d["cause"]) for d in diff]


def main():
    make_fixtures.main()
    sessions = make_fixtures.build_sessions()
    tmp = tempfile.mkdtemp(prefix="replay-audit-", dir=os.environ.get("TMPDIR"))
    print("temporary outputs under %s" % tmp)

    expected = {
        "s_clean_v1": {
            "diff": [],
            "errors": [],
            "outputs": [{"id": "r1", "title": " Keep Me ", "n": 1}],
            "ranges": [{"from_seq": 0, "to_seq": 2, "version": "v1"}],
        },
        "d1_code_change": {
            "diff": [(2, "emit-mismatch", "code-change")],
            "errors": [],
            "outputs": [{"id": "r1", "title": "hello world"}],
            "ranges": [
                {"from_seq": 0, "to_seq": 0, "version": "v1"},
                {"from_seq": 1, "to_seq": 2, "version": "v2"},
            ],
        },
        "d2_input_drift": {
            "diff": [(2, "emit-mismatch", "input-drift")],
            "errors": [],
            "outputs": [{"id": "r1", "title": "Drifted Title"}],
            "ranges": [{"from_seq": 0, "to_seq": 2, "version": "v1"}],
        },
        "d3_order_sensitive": {
            "diff": [(2, "emit-mismatch", "order-sensitive"),
                     (5, "emit-mismatch", "order-sensitive")],
            "errors": [],
            "outputs": [
                {"id": "rA", "title": "A"},
                {"id": "rB", "title": "B"},
            ],
            "ranges": [{"from_seq": 0, "to_seq": 5, "version": "v1"}],
        },
        "d4_real_defect": {
            "diff": [(2, "emit-mismatch", "real-defect")],
            "errors": [],
            "outputs": [{"id": "r1", "title": "Correct"}],
            "ranges": [{"from_seq": 0, "to_seq": 2, "version": "v1"}],
        },
        "d5_missing_extra": {
            "diff": [(2, "emit-extra", "real-defect"),
                     (-1, "emit-missing", "real-defect")],
            "errors": [],
            "outputs": [{"id": "r-extra", "title": "A"}],
            "ranges": [{"from_seq": 0, "to_seq": 2, "version": "v1"}],
        },
        "s_clean_v3": {
            "diff": [],
            "errors": [],
            "outputs": [
                {"id": "r1", "title": "mixed  case"},
                {"id": "r2", "title": None},
            ],
            "ranges": [{"from_seq": 0, "to_seq": 5, "version": "v3"}],
        },
        "x_truncated": {
            "diff": [],
            "errors_truncated": {"seq": -1, "reason": "truncated-log",
                                 "missing": [3, 4]},
            "outputs": [
                {"id": "r1", "title": "A"},
                {"id": "r2", "title": "A"},
            ],
            "ranges": [{"from_seq": 0, "to_seq": 5, "version": "v1"}],
        },
        "x_unknown_kind": {
            "diff": [],
            "errors": [{"seq": 0, "reason": "unknown-kind"}],
            "outputs": [{"id": "r1", "title": "A"}],
            "ranges": [{"from_seq": 0, "to_seq": 3, "version": "v1"}],
        },
        "s_clean_multi_version": {
            "diff": [],
            "errors": [],
            "outputs": [
                {"id": "r1", "title": " First "},
                {"id": "r2", "title": "second"},
            ],
            "ranges": [
                {"from_seq": 0, "to_seq": 2, "version": "v1"},
                {"from_seq": 3, "to_seq": 5, "version": "v2"},
            ],
        },
    }

    for name, session in sessions.items():
        print("\n=== %s ===" % name)
        fixture = os.path.join(make_fixtures.FIXTURE_DIR, name + ".json")
        out_path = os.path.join(tmp, name + ".out.json")

        # Bare two-argument CLI (backward-compatible entry point).
        report = run_cli(fixture, out_path)
        spec = expected[name]

        check(set(report) == {"replayed_outputs", "diff", "errors",
                              "version_ranges"},
              "output contract has exactly the documented keys")
        check(report["replayed_outputs"] == spec["outputs"],
              "replayed_outputs=%r" % report["replayed_outputs"])
        check(summarize(report["diff"]) == spec["diff"],
              "diff summary=%r" % summarize(report["diff"]))
        check(report["version_ranges"] == spec["ranges"],
              "version_ranges=%r" % report["version_ranges"])

        if "errors" in spec:
            check(report["errors"] == spec["errors"],
                  "errors=%r" % report["errors"])
        if "errors_truncated" in spec:
            check(spec["errors_truncated"] in report["errors"],
                  "truncation located at missing seqs: %r"
                  % report["errors"])

        # Evidence strings must actually locate the cause.
        for d in report["diff"]:
            check(isinstance(d["detail"], str) and len(d["detail"]) > 10,
                  "diff detail carries locating evidence: %s" % d["detail"])

        # Library API must match the CLI result.
        lib_report = replay_audit.run(session)
        check(lib_report == report, "library run() matches CLI output")

        # Chunked replay: every chunk count must equal the whole replay.
        n_events = len(session["events"])
        for chunks in (2, 3, max(2, n_events), n_events + 3):
            chunked = replay_audit.run(session, chunks=chunks)
            check(chunked == report,
                  "chunks=%s replay identical to whole replay" % chunks)

        # CLI --chunks path as well.
        if n_events:
            chunk_cli = run_cli(fixture, out_path + ".chunk", chunks=3)
            check(chunk_cli == report,
                  "CLI --chunks 3 replay identical to whole replay")

    # Attribution precedence: a title mismatch with BOTH a version switch
    # and input drift must be attributed to code-change (higher priority).
    precedence_session = {
        "session": "p_precedence",
        "code_version": "v1",
        "events": [
            {"seq": 0, "kind": "fetch", "url": "a",
             "response": {"fields": {"title": " Recorded "}}, "ts": 1},
            {"seq": 1, "kind": "parse", "input_ref": 0,
             "fields": {"title": " Recorded "}, "code_version": "v2"},
            {"seq": 2, "kind": "emit", "record": {"id": "r1"},
             "fields_ref": 1},
        ],
        "snapshots": {"fetch": {"a": {"fields": {"title": " Snapshot "}}}},
        "recorded_outputs": [{"id": "r1", "title": " Recorded "}],
    }
    print("\n=== precedence (code-change over input-drift) ===")
    rep = replay_audit.run(precedence_session)
    check(summarize(rep["diff"]) == [(2, "emit-mismatch", "code-change")],
          "higher-priority code-change wins: %r" % summarize(rep["diff"]))
    check("version switch at seq 1 (v1->v2)" in rep["diff"][0]["detail"],
          "evidence names the version interval of the switch")
    check("version range 1-2=v2" in rep["diff"][0]["detail"],
          "evidence names the containing version range")

    # Empty event stream stays well-formed.
    print("\n=== empty stream ===")
    empty = replay_audit.run(
        {"session": "e", "code_version": "v1", "events": [],
         "snapshots": {}, "recorded_outputs": []})
    check(empty == {"replayed_outputs": [], "diff": [], "errors": [],
                    "version_ranges": []},
          "empty session yields an empty, well-formed report")

    # Duplicate seq is reported as a log-integrity error (non-contiguous
    # gaps take priority and surface as truncation).
    print("\n=== duplicate seq ===")
    dup = replay_audit.run({
        "session": "dup",
        "code_version": "v1",
        "events": [
            {"seq": 0, "kind": "fetch", "url": "a", "response": "x"},
            {"seq": 1, "kind": "fetch", "url": "b", "response": "y"},
            {"seq": 1, "kind": "fetch", "url": "c", "response": "z"},
            {"seq": 2, "kind": "parse", "input_ref": 0,
             "fields": {"title": "A"}},
            {"seq": 3, "kind": "emit", "record": {"id": "r1"},
             "fields_ref": 2},
        ],
        "snapshots": {"fetch": {"a": "x", "b": "y", "c": "z"}},
        "recorded_outputs": [{"id": "r1", "title": "A"}],
    })
    check({"seq": -1, "reason": "duplicate-seq", "duplicates": [1]}
          in dup["errors"],
          "duplicate seq reported: %r" % dup["errors"])
    check(dup["diff"] == [], "duplicate-seq session has no content diff")

    print("\nfixture directory: %s" % make_fixtures.FIXTURE_DIR)
    print("tmp output directory: %s" % tmp)
    if FAILURES:
        print("\n%d CHECK(S) FAILED" % len(FAILURES))
        for f in FAILURES:
            print(" - " + f)
        sys.exit(1)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
