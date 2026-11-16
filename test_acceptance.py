#!/usr/bin/env python3
"""Acceptance tests for remap.py.

Coverage required by the task:
  1. per-channel independent pulses (every source channel has a unique value);
  2. duplicate layout labels (conflict metadata + muted output);
  3. changing device target layout (stereo / 5.1 / 7.1 re-render runs);
  4. same-frame conflicting metadata (conflict on a segment boundary);
plus assertions on speaker outputs, missing-channel reasons and the sample
identity at every layout switch point.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from remap import remap  # noqa: E402

FAILURES = []


def check(cond, msg):
    if cond:
        print("  PASS", msg)
    else:
        FAILURES.append(msg)
        print("  FAIL", msg)


def run_cli(payload):
    """Exercise the real CLI (track-layout read path), return parsed output."""
    with tempfile.TemporaryDirectory() as td:
        ip = os.path.join(td, "in.json")
        op = os.path.join(td, "out.json")
        with open(ip, "w") as f:
            json.dump(payload, f)
        r = subprocess.run([sys.executable, os.path.join(HERE, "remap.py"), ip, op],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        with open(op) as f:
            return json.load(f)


def pulse_value(seg_idx, ch_idx):
    return 100 * seg_idx + ch_idx


def load_fixture():
    with open(os.path.join(HERE, "fixtures", "remux51.json")) as f:
        return json.load(f)


def test_independent_pulses_unique(fixture):
    print("[1] independent per-channel pulses")
    seen = set()
    for si, seg in enumerate(fixture["segments"]):
        for ci, ch in enumerate(seg["samples"]):
            check(len(set(ch)) == 1, f"seg{si} ch{ci} is one constant pulse")
            check(ch[0] not in seen, f"pulse {ch[0]} globally unique")
            seen.add(ch[0])


def expected_pulse_for(layout, target, seg_idx, tid, status="ok"):
    """Ground truth: find the source channel *by label*, never by position.

    Conflict/unknown segments are muted by design, and an ok segment
    silence-fills any label its source layout does not carry.
    """
    if status != "ok" or tid not in layout:
        return 0
    return pulse_value(seg_idx, layout.index(tid))


def test_remux51_routing():
    print("[2] 5.1 remux: LFE / rear-surround swap fixed by explicit labels")
    fixture = load_fixture()
    target = fixture["target_speakers"]
    out = run_cli(fixture)
    chans, report = out["channels"], out["segment_report"]
    check(len(chans) == len(target), "one output array per target speaker")
    check(all(len(c) == 48 for c in chans), "all outputs span 48 samples")

    layouts = [s["source_speakers"] for s in fixture["segments"]]
    bounds = [0, 8, 16, 24, 32, 40, 48]
    for si, seg in enumerate(fixture["segments"]):
        lo, hi = bounds[si], bounds[si + 1]
        for p, tid in enumerate(target):
            want = expected_pulse_for(layouts[si], target, si, tid,
                                      report[si]["status"])
            got = chans[p][lo:hi]
            check(got == [want] * (hi - lo),
                  f"seg{si} target[{p}]={tid} [{report[si]['status']}]: "
                  f"routed pulse {want} (source labels {layouts[si]})")

    # The exact remux defect: in seg1 LFE sits in slot 5 and Ls in slot 3.
    # An alphabetical/position guess would put Ls' pulse (103) on the LFE
    # output; the label-based mapping must put LFE's own pulse (105) there.
    lfe = target.index("LFE")
    check(chans[lfe][8:16] == [pulse_value(1, 5)] * 8,
          "LFE output in remuxed segment follows LFE label, not slot order")
    check(chans[target.index("Ls")][8:16] == [pulse_value(1, 3)] * 8,
          "Ls output in remuxed segment follows Ls label, not slot order")
    check(report[1]["map"] == [0, 1, 2, 5, 3, 4],
          "seg1 map points at label-matched source indices [0,1,2,5,3,4]")
    check([r["status"] for r in report] ==
          ["ok", "ok", "ok", "unknown", "conflict", "ok"],
          "segment statuses ok/ok/ok/unknown/conflict/ok")


def test_switch_point_sample_identity():
    print("[3] layout switch points: sample identity on both sides of edges")
    fixture = load_fixture()
    target = fixture["target_speakers"]
    out = remap(fixture)
    chans = out["channels"]
    layouts = [s["source_speakers"] for s in fixture["segments"]]
    for edge in (8, 16, 24, 32, 40):
        si = edge // 8 - 1
        statuses = [r["status"] for r in out["segment_report"]]
        for p, tid in enumerate(target):
            before = expected_pulse_for(layouts[si], target, si, tid,
                                        statuses[si])
            after = expected_pulse_for(layouts[si + 1], target, si + 1, tid,
                                       statuses[si + 1])
            check(chans[p][edge - 1] == before and chans[p][edge] == after,
                  f"edge@{edge} {tid}: last={chans[p][edge-1]} (want {before}), "
                  f"first={chans[p][edge]} (want {after})")


def test_missing_and_unknown_reasons():
    print("[4] missing-channel reason and unknown-layout reporting")
    fixture = load_fixture()
    out = remap(fixture)
    report = out["segment_report"]
    # seg2: LFE absent -> -1 at the LFE target slot, reason recorded.
    r2 = report[2]
    lfe = fixture["target_speakers"].index("LFE")
    check(r2["status"] == "ok" and r2["map"][lfe] == -1,
          "seg2 LFE target slot maps to -1")
    check(r2["missing"] == ["LFE"], "seg2 reports missing=['LFE']")
    check(out["channels"][lfe][16:24] == [0] * 8,
          "seg2 silence is placed at the LFE *target* position")
    check(all(m >= 0 for p, m in enumerate(r2["map"]) if p != lfe),
          "seg2 remaining speakers still routed")
    # seg3: fully foreign layout -> unknown, all -1, muted, no guessing.
    r3 = report[3]
    check(r3["status"] == "unknown", "seg3 marked unknown (not guessed)")
    check(r3["map"] == [-1] * 6, "seg3 map all -1")
    check(r3["missing"] == list(fixture["target_speakers"]),
          "seg3 missing lists every target speaker")
    check(all(all(v == 0 for v in c[24:32]) for c in out["channels"]),
          "seg3 outputs fully muted")
    check(r3["source_speakers"] == ["Aux1", "Aux2", "Aux3", "Aux4"],
          "seg3 foreign layout labels retained in report")


def test_duplicate_labels_conflict():
    print("[5] duplicate layout labels: conflict metadata + muted segment")
    fixture = load_fixture()
    out = remap(fixture)
    r4 = out["segment_report"][4]
    check(r4["status"] == "conflict", "seg4 marked conflict")
    check(r4["duplicates"] == ["Ls"], "seg4 reports duplicated label Ls")
    check(r4["map"] == [-1] * 6, "conflict segment maps everything to -1")
    check(all(all(v == 0 for v in c[32:40]) for c in out["channels"]),
          "seg4 whole segment muted on every speaker")
    # recovery after the conflict: seg5 routes normally again
    r5 = out["segment_report"][5]
    check(r5["status"] == "ok" and r5["map"] == [0, 1, 2, 3, 4, 5],
          "seg5 recovers to clean label mapping")


def test_same_frame_conflict():
    print("[6] same-frame conflicting metadata on a segment boundary")
    with open(os.path.join(HERE, "fixtures", "same_frame_conflict.json")) as f:
        fixture = json.load(f)
    out = run_cli(fixture)
    report, chans = out["segment_report"], out["channels"]
    check([r["status"] for r in report] == ["ok", "conflict"],
          "first frame-range ok, boundary-starting range conflict")
    check(report[1]["duplicates"] == ["L"], "conflict names duplicated L")
    check(chans[0][0:4] == [pulse_value(0, 0)] * 4,
          "pre-boundary L samples keep their identity")
    check(all(c[4:8] == [0] * 4 for c in chans),
          "conflict frame range muted on every target position")
    check(all(len(c) == 8 for c in chans), "both ranges concatenate to 8")


def test_device_target_layout_changes():
    print("[7] device target layout changes (stereo / 5.1 / 7.1)")
    base = load_fixture()
    layouts = [s["source_speakers"] for s in base["segments"]]

    # Stereo downmix device: only L/R are targets; silence fill never leaks
    # into non-target speakers because they simply are not output positions.
    stereo = dict(base, target_speakers=["L", "R"])
    out = remap(stereo)
    check(len(out["channels"]) == 2, "stereo device renders 2 outputs")
    for si in range(6):
        lo, hi = si * 8, si * 8 + 8
        for p, tid in enumerate(("L", "R")):
            want = expected_pulse_for(layouts[si], None, si, tid,
                                      out["segment_report"][si]["status"])
            check(out["channels"][p][lo:hi] == [want] * 8,
                  f"stereo seg{si} {tid} pulse {want}")

    # 7.1 device: adds Rrs/Lrs; no 5.1 source carries those labels, so the
    # new target positions are silence-filled and named in missing.
    target71 = ["L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs"]
    seven = dict(base, target_speakers=target71)
    out7 = remap(seven)
    check(len(out7["channels"]) == 8, "7.1 device renders 8 outputs")
    check(all(all(v == 0 for v in c) for c in out7["channels"][6:8]),
          "Lrs/Rrs target positions silence-filled for every segment")
    check(all({"Lrs", "Rrs"}.issubset(set(r["missing"]))
              for r in out7["segment_report"] if r["status"] == "ok"),
          "every ok 7.1 segment names Lrs/Rrs among the missing channels")
    # Existing 5.1 routing must be byte-identical across the device change.
    out5 = remap(base)
    check(out7["channels"][:6] == out5["channels"],
          "shared 5.1 speaker outputs identical between device layouts")
    # Unknown/conflict statuses are independent of the chosen target layout.
    check([r["status"] for r in out7["segment_report"]] ==
          [r["status"] for r in out5["segment_report"]],
          "segment statuses stable under target layout change")


def test_order_independence():
    print("[8] source order independence (no positional/lexicographic guess)")
    target = ["L", "R", "C", "LFE", "Ls", "Rs"]
    labels = list(reversed(target))  # Rs,Ls,LFE,C,R,L
    seg = {
        "start": 0, "end": 4,
        "source_speakers": labels,
        "samples": [[pulse_value(0, i)] * 4 for i in range(6)],
    }
    out = remap({"target_speakers": target, "segments": [seg]})
    check(out["segment_report"][0]["map"] == [5, 4, 3, 2, 1, 0],
          "reversed source order still maps by label identity")
    for p, tid in enumerate(target):
        want = pulse_value(0, labels.index(tid))
        check(out["channels"][p] == [want] * 4,
              f"reversed-order target {tid} receives pulse {want}")


def main():
    fixture = load_fixture()
    test_independent_pulses_unique(fixture)
    test_remux51_routing()
    test_switch_point_sample_identity()
    test_missing_and_unknown_reasons()
    test_duplicate_labels_conflict()
    test_same_frame_conflict()
    test_device_target_layout_changes()
    test_order_independence()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED")
        for m in FAILURES:
            print(" -", m)
        sys.exit(1)
    print("ALL ACCEPTANCE CHECKS PASSED")


if __name__ == "__main__":
    main()
