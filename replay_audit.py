#!/usr/bin/env python3
"""Deterministic session replay + diff attribution."""
import json, sys

def parse_title(t, version):       # BUG: ignores version semantics
    return t

def run(session):
    out, errs, diffs = [], [], []
    version = session["code_version"]
    parsed = {}
    for e in session["events"]:
        k = e["kind"]
        if k == "parse":
            snap = session["snapshots"]["fetch"].get(e.get("url_hint") or "")
            inp = session["events"][e["input_ref"]]
            fields = dict(inp.get("fields") or {})
            if "title" in fields:
                fields["title"] = parse_title(fields["title"], version)
            parsed[e["seq"]] = fields
        elif k == "emit":
            rec = dict(e["record"])
            ref = e.get("fields_ref")
            if ref is not None and ref in parsed:
                rec.update(parsed[ref])
            out.append(rec)
        elif k == "fetch":
            pass
        else:
            errs.append({"seq": e["seq"], "reason": "unknown-kind"})
    recorded = session.get("recorded_outputs", [])
    for i, rec in enumerate(recorded):       # positional compare
        if i < len(out) and out[i] != rec:
            diffs.append({"seq": i, "kind": "emit-mismatch",
                          "cause": "real-defect", "detail": repr((out[i], rec))})
        if i >= len(out):
            diffs.append({"seq": i, "kind": "emit-missing", "cause": "real-defect", "detail": rec["id"]})
    for i in range(len(recorded), len(out)):
        diffs.append({"seq": i, "kind": "emit-extra", "cause": "real-defect", "detail": out[i]["id"]})
    return {"replayed_outputs": out, "diff": diffs, "errors": errs,
            "version_ranges": [{"from_seq": 0, "to_seq": session["events"][-1]["seq"], "version": version}]}

def main():
    s = json.load(open(sys.argv[1]))
    json.dump(run(s), open(sys.argv[2], "w"))

if __name__ == "__main__":
    main()
