#!/usr/bin/env python3
"""Acceptance harness for deterministic replay and diff attribution.

Checks:
  1. the five injected-difference sessions are hit one-by-one with the
     correct attribution and locating evidence;
  2. the clean session reports an empty diff set;
  3. dynamically injected code-version switch lands in the correct
     version interval; dynamically truncated logs pinpoint the missing
     segment;
  4. chunked replay (chunk sizes 1, 2, 3, 5, 999) is identical to the
     whole-session replay for every session.
Writes outputs/<name>.json per run and outputs/report.md summary.
"""
import copy
import json
import os
import sys

import replay_audit as ra

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
OUT = os.path.join(HERE, "outputs")

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print("[%s] %s%s" % (PASS if cond else FAIL, name,
                         (" -- " + detail) if detail and not cond else ""))
    return cond


def load(fname):
    with open(os.path.join(FIX, fname), encoding="utf-8") as fh:
        return json.load(fh)


def diffs_by_seq(result):
    return {d["seq"]: d for d in result["diff"]}


def errors_by_reason(result):
    out = {}
    for e in result["errors"]:
        out.setdefault(e["reason"], []).append(e)
    return out


# ------------------------------------------------------------- static cases
def test_static_sessions():
    expected = {
        "S1_code_change.json": ("code-change", [5]),
        "S2_input_drift.json": ("input-drift", [2]),
        "S3_order_sensitive.json": ("order-sensitive", [5]),
        "S4_real_defect.json": ("real-defect", [-1, 2, 3]),
        "S5_code_change_v3.json": ("code-change", [5]),
    }
    all_ok = True
    for fname, (cause, seqs) in expected.items():
        sess = load(fname)
        res = ra.run(sess)
        got = diffs_by_seq(res)
        ok = sorted(got) == sorted(seqs) and all(
            got[s]["cause"] == cause for s in seqs)
        all_ok &= check("%s diffs attributed to %s at %s"
                        % (fname, cause, seqs), ok,
                        json.dumps(res["diff"], ensure_ascii=False))
        # every diff must carry locating evidence
        ok_ev = all(d["detail"].get("fields") or d["detail"].get("evidence")
                    for d in res["diff"])
        all_ok &= check("%s every diff has evidence" % fname, ok_ev)
    # clean session: empty diff set and no errors
    clean = ra.run(load("S0_clean.json"))
    all_ok &= check("S0 clean session -> empty diff set",
                    clean["diff"] == [], json.dumps(clean["diff"]))
    all_ok &= check("S0 clean session -> no errors",
                    clean["errors"] == [], json.dumps(clean["errors"]))
    # unknown kind is reported, replay continues, outputs still match
    s7 = ra.run(load("S7_unknown_kind.json"))
    errs = errors_by_reason(s7)
    all_ok &= check("S7 unknown-kind error recorded at seq 1",
                    errs.get("unknown-kind", [{}])[0].get("seq") == 1)
    all_ok &= check("S7 replay continues -> empty diff set",
                    s7["diff"] == [])
    # truncated log: missing segment 2..3 located, dangling ref reported
    s6 = ra.run(load("S6_truncated.json"))
    errs = errors_by_reason(s6)
    trunc = errs.get("truncated-log", [{}])[0]
    all_ok &= check("S6 truncated-log located with missing segment [[2,3]]",
                    trunc.get("missing") == [[2, 3]],
                    json.dumps(trunc, ensure_ascii=False))
    all_ok &= check("S6 dangling parse reported as missing-ref",
                    any(e.get("seq") == 4 for e in errs.get(
                        "missing-ref", [])))
    return all_ok


# ---------------------------------------------------------- dynamic injection
def test_injected_version_switch():
    """Take the clean session, inject a v2 switch before the second emit."""
    sess = load("S0_clean.json")
    sess = copy.deepcopy(sess)
    # seq 3 is the second fetch; switching there changes title handling for
    # every field produced afterwards. Use a dict payload so the title flows
    # through the version-specific normalisation rule.
    sess["events"][3]["code_version"] = "v2"
    sess["events"][3]["response"] = {"title": "  Raw Title  "}
    sess["snapshots"]["fetch"]["c2"] = {"title": "  Raw Title  "}
    # recorded output keeps the un-normalised v1 value
    sess["recorded_outputs"][1] = {"id": "c-r2",
                                   "title": "  Raw Title  "}
    res = ra.run(sess)
    d = diffs_by_seq(res).get(5)
    ok = d is not None and d["cause"] == "code-change"
    check("injected version switch -> code-change on affected emit", ok,
          json.dumps(d, ensure_ascii=False) if d else "no diff at seq 5")
    # version interval must place the switch boundary at seq 3
    ranges = [{k: v for k, v in r.items() if k != "switched"}
              for r in res["version_ranges"]]
    ok_r = ranges == [{"from_seq": 0, "to_seq": 2, "version": "v1"},
                      {"from_seq": 3, "to_seq": 5, "version": "v2"}]
    check("injected switch attributed to version interval v2[3..5]", ok_r,
          json.dumps(ranges, ensure_ascii=False))
    ev = d["detail"]["fields"]["title"] if ok else {}
    check("switch evidence names switch_at_seq=3 and active v2",
          ev.get("switch_at_seq") == 3 and ev.get("active_version") == "v2",
          json.dumps(ev, ensure_ascii=False))
    # events before the boundary stay clean
    check("pre-switch emit (seq 2) unaffected", 2 not in diffs_by_seq(res))
    return ok and ok_r


def test_injected_truncation():
    """Dynamically punch gaps in a clean session; missing seqs must show."""
    sess = copy.deepcopy(load("S0_clean.json"))
    # remove seq 3 but keep the tail (4,5): gap appears inside the log
    sess["events"] = [e for e in sess["events"] if e["seq"] != 3]
    res = ra.run(sess)
    errs = errors_by_reason(res)
    trunc = errs.get("truncated-log", [{}])[0]
    ok = trunc.get("missing") == [[3, 3]]
    check("injected tail-side truncation locates missing segment [[3,3]]",
          ok, json.dumps(trunc, ensure_ascii=False))
    # middle cut
    sess2 = copy.deepcopy(load("S0_clean.json"))
    sess2["events"] = [e for e in sess2["events"] if e["seq"] not in (2, 3)]
    res2 = ra.run(sess2)
    trunc2 = errors_by_reason(res2).get("truncated-log", [{}])[0]
    ok2 = trunc2.get("missing") == [[2, 3]]
    check("injected middle truncation locates missing segment [[2,3]]", ok2,
          json.dumps(trunc2, ensure_ascii=False))
    return ok and ok2


# ------------------------------------------------------------ chunk parity
def test_chunk_parity():
    fnames = sorted(f for f in os.listdir(FIX) if f.endswith(".json"))
    sizes = [1, 2, 3, 5, 999]
    all_ok = True
    for fname in fnames:
        sess = load(fname)
        whole = ra.run(sess)
        for size in sizes:
            chunked = ra.run_chunked(sess, size)
            same = chunked == whole
            all_ok &= check("%s chunk size %d == whole replay"
                            % (fname, size), same,
                            json.dumps(chunked, ensure_ascii=False)
                            if not same else "")
    return all_ok


# ------------------------------------------------------------ report writer
def write_report():
    os.makedirs(OUT, exist_ok=True)
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    lines = ["# Replay comparison report", "",
             "- total checks: %d" % total,
             "- passed: %d" % passed,
             "- failed: %d" % (total - passed), "",
             "## Per-session replay outputs", ""]
    for fname in sorted(f for f in os.listdir(FIX) if f.endswith(".json")):
        sess = load(fname)
        res = ra.run(sess)
        with open(os.path.join(OUT, fname), "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2,
                      sort_keys=True)
            fh.write("\n")
        lines.append("### %s (%s)" % (fname, sess.get("session", "")))
        lines.append("")
        lines.append("- version_ranges: `%s`"
                     % json.dumps(res["version_ranges"], ensure_ascii=False))
        lines.append("- diff count: %d" % len(res["diff"]))
        for d in res["diff"]:
            lines.append("  - seq %s %s **%s** -- `%s`"
                         % (d["seq"], d["kind"], d["cause"],
                            json.dumps(d["detail"], ensure_ascii=False)))
        if res["errors"]:
            lines.append("- errors:")
            for e in res["errors"]:
                lines.append("  - `%s`" % json.dumps(e, ensure_ascii=False))
        lines.append("")
    lines += ["## Check results", ""]
    for name, ok, detail in results:
        lines.append("- [%s] %s%s" % (PASS if ok else FAIL, name,
                                      (" -- " + detail) if detail else ""))
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    ok1 = test_static_sessions()
    ok2 = test_injected_version_switch()
    ok3 = test_injected_truncation()
    ok4 = test_chunk_parity()
    write_report()
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    print("\n%d/%d checks passed" % (passed, total))
    sys.exit(0 if all((ok1, ok2, ok3, ok4)) and passed == total else 1)


if __name__ == "__main__":
    main()
