# -*- coding: utf-8 -*-
"""Acceptance tests for the EDL driven WebVTT cutter.

Run: python3 tests/test_acceptance.py
Prints ok/FAIL lines and exits non zero on any failure.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import vttcutter  # noqa: E402
import independent_expander as ie  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
TIMING = re.compile(
    r"((?:\d+:)?\d{1,2}:\d{2}\.\d{3})\s*-->\s*"
    r"((?:\d+:)?\d{1,2}:\d{2}\.\d{3})([ \t].*)?$"
)
MARK = re.compile(r"<((?:\d+:)?\d{1,2}:\d{2}\.\d{3})>")
FAILURES = []


def check(cond, message):
    if not cond:
        FAILURES.append(message)
        print("FAIL:", message)
    else:
        print("ok:  ", message)


def parse_output(path):
    """Parse exported VTT back into cue records (standard parser view)."""
    text = Path(path).read_text(encoding="utf-8")
    check(text.startswith("WEBVTT"), "output starts with WEBVTT header")
    blocks = re.split(r"\n[ \t]*\n", text.strip())
    cues = []
    for block in blocks[1:]:
        rows = block.split("\n")
        if rows[0].strip() in ("STYLE", "REGION"):
            continue
        timing_at = None
        for i, row in enumerate(rows):
            if TIMING.match(row.strip()):
                timing_at = i
                break
        check(timing_at is not None, "every non header block parses")
        ident = rows[timing_at - 1].strip() if timing_at > 0 else None
        m = TIMING.match(rows[timing_at].strip())
        body = "\n".join(rows[timing_at + 1:])
        markers = [ie.ms(ie.ts(x.group(1))) for x in MARK.finditer(body)]
        cues.append(dict(
            id=ident,
            start_ms=ie.ms(ie.ts(m.group(1))),
            end_ms=ie.ms(ie.ts(m.group(2))),
            settings=m.group(3) or "",
            markers=markers,
            body=body,
        ))
    return text, cues


def run_cli(workdir, edl_name):
    edl = workdir / edl_name
    out = workdir / "out.vtt"
    cmap = workdir / "map.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "cut_vtt.py"), str(edl),
         str(out), str(cmap)],
        capture_output=True, text=True)
    return proc, out, cmap


def cue_map_lookup(workdir, cid):
    records = json.loads((workdir / "map.json").read_text(
        encoding="utf-8"))["cues"]
    for rec in records:
        if rec["id"] == cid:
            return rec["source_id"] + "@" + rec["clip"]
    raise KeyError(cid)


def test_expansion_matches_to_millisecond(workdir):
    print("\n== 1. 12 clip expansion compared to the millisecond ==")
    clips, expected = ie.expand(str(workdir / "edl.json"))
    check(len(clips) == 12, "EDL has exactly 12 clips")
    proc, out, cmap_path = run_cli(workdir, "edl.json")
    check(proc.returncode == 0, "CLI exits 0 on the valid EDL")
    text, cues = parse_output(out)
    cue_map = json.loads(cmap_path.read_text(encoding="utf-8"))["cues"]

    check(len(cues) == len(expected),
          "cue count %d matches independent expectation %d"
          % (len(cues), len(expected)))
    check(len(cue_map) == len(expected),
          "cue-map record count matches expectation")

    for got, want in zip(cues, expected):
        prefix = "%s/%s line %s" % (want["clip"], want["source"],
                                    want["line"])
        check(got["start_ms"] == want["start_ms"],
              "%s start %d == expected %d"
              % (prefix, got["start_ms"], want["start_ms"]))
        check(got["end_ms"] == want["end_ms"],
              "%s end %d == expected %d"
              % (prefix, got["end_ms"], want["end_ms"]))
        check(got["markers"] == want["markers"],
              "%s markers %s == expected %s"
              % (prefix, got["markers"], want["markers"]))

    for got, rec, want in zip(cues, cue_map, expected):
        check(rec["id"] == got["id"],
              "map id matches exported id " + str(got["id"]))
        check(rec["start_ms"] == want["start_ms"]
              and rec["end_ms"] == want["end_ms"],
              "map timestamps match expectation for " + str(got["id"]))
        check(rec["line"] == want["line"]
              and rec["source"] == want["source"]
              and rec["clip"] == want["clip"]
              and rec["source_id"] == want["source_id"],
              "map provenance fields match for " + str(got["id"]))
    return clips, expected, text, cues


def test_visibility_three_frames(workdir, clips, expected, cues):
    print("\n== 2. visible cue sets three frames around each cut ==")
    for serial in range(1, len(clips)):
        for offset in (-3, 3):
            play = ie.frame_instant(clips, serial, offset)
            want = ie.visible_at(clips, expected, play)
            want_ids = sorted(r["source_id"] + "@" + r["clip"]
                              for r in want)
            got_ids = sorted(c["id"] for c in cues
                             if c["start_ms"] <= play < c["end_ms"])
            got_identity = sorted(cue_map_lookup(workdir, cid)
                                  for cid in got_ids)
            check(got_identity == want_ids,
                  "cut %d offset %+d frame (t=%dms): visible %s"
                  % (serial, offset, play, got_identity))


def test_reorder_stability(workdir):
    print("\n== 3. swapped repeat clips: unique ids, stable renames, "
          "same bytes ==")
    edl = json.loads((workdir / "edl.json").read_text(encoding="utf-8"))
    clips = edl["clips"]
    i1 = next(i for i, c in enumerate(clips) if c["id"] == "k01")
    i8 = next(i for i, c in enumerate(clips) if c["id"] == "k08")
    swapped = json.loads(json.dumps(edl))
    swapped["clips"][i1], swapped["clips"][i8] = (
        swapped["clips"][i8], swapped["clips"][i1])
    (workdir / "edl_swap.json").write_text(
        json.dumps(swapped, indent=2) + "\n", encoding="utf-8")

    proc_a, out_a, map_a = run_cli(workdir, "edl.json")
    proc_b, out_b, map_b = run_cli(workdir, "edl_swap.json")
    check(proc_a.returncode == 0 and proc_b.returncode == 0,
          "both EDL orders export successfully")
    bytes_a = out_a.read_bytes()
    bytes_b = out_b.read_bytes()
    check(bytes_a == bytes_b,
          "exported VTT bytes identical after clip reorder (%d bytes)"
          % len(bytes_a))
    check(map_a.read_bytes() == map_b.read_bytes(),
          "cue-map bytes identical after clip reorder")

    records = json.loads(map_a.read_text(encoding="utf-8"))["cues"]
    ids = [r["id"] for r in records]
    check(len(ids) == len(set(ids)), "all exported cue ids are unique")
    a1_ids = sorted(r["id"] for r in records
                    if r["source_id"] == "a1")
    check(a1_ids == ["a1", "a1-2", "a1-3", "a1-4", "a1-5"],
          "repeated segment cue gets stable a1 / a1-N ids: "
          + str(a1_ids))
    text = bytes_a.decode("utf-8")
    check("alpha__shared" in text and "beta__shared" in text,
          "colliding style class renamed per source with stable names")
    check("region:alpha__top" in text and "region:beta__top" in text,
          "colliding regions renamed in cue settings")
    check("id:alpha__top" in text and "id:beta__top" in text,
          "colliding regions renamed in REGION blocks")
    check("::cue(.alpha__shared)" in text
          and "::cue(.beta__shared)" in text,
          "STYLE selectors renamed consistently with cue tags")
    check("<c.alpha__shared>" in text and "<c.beta__shared>" in text,
          "cue tags renamed consistently with STYLE declarations")


def test_bad_inputs(workdir):
    print("\n== 4. bad input fixtures locate source file and line ==")
    cases = [
        ("bad_interval.json", "clips[3]", "reversed frame interval"),
        ("bad_missing_source.json", "clips[1]", "missing source reference"),
        ("bad_speed.json", "clips[0]", "speed outside allowed range"),
        ("bad_region.json", "alpha_bad_region.vtt", "missing region"),
        ("bad_marker.json", "alpha_bad_marker.vtt",
         "non increasing marker"),
        ("bad_block.json", "alpha_bad_block.vtt",
         "unsupported text block"),
        ("bad_style.json", "alpha_bad_style.vtt",
         "unsupported style rule"),
        ("bad_json.json", "line 2", "malformed JSON location"),
    ]
    for name, needle, description in cases:
        proc, _, _ = run_cli(workdir, name)
        check(proc.returncode == 2, description + ": exit code 2")
        check(needle in proc.stderr and "cut_vtt:" in proc.stderr,
              description + ": stderr names " + needle
              + " (got: " + proc.stderr.strip() + ")")

    sentinel_vtt = workdir / "keep.vtt"
    sentinel_map = workdir / "keep.json"
    sentinel_vtt.write_text("KEEP VTT\n", encoding="utf-8")
    sentinel_map.write_text("KEEP MAP\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "cut_vtt.py"),
         str(workdir / "bad_interval.json"),
         str(sentinel_vtt), str(sentinel_map)],
        capture_output=True, text=True)
    check(proc.returncode == 2, "failed run still exits 2")
    check(sentinel_vtt.read_text() == "KEEP VTT\n"
          and sentinel_map.read_text() == "KEEP MAP\n",
          "existing outputs left untouched on validation error")


def write_bad_fixtures(workdir):
    good = json.loads((workdir / "edl.json").read_text(encoding="utf-8"))

    bad = json.loads(json.dumps(good))
    bad["clips"][3]["start_frame"] = 200
    bad["clips"][3]["end_frame"] = 100
    (workdir / "bad_interval.json").write_text(json.dumps(bad, indent=2),
                                               encoding="utf-8")

    bad = json.loads(json.dumps(good))
    bad["clips"][1]["source"] = "ghost"
    (workdir / "bad_missing_source.json").write_text(
        json.dumps(bad, indent=2), encoding="utf-8")

    bad = json.loads(json.dumps(good))
    bad["clips"][0]["speed"] = [9, 1]
    (workdir / "bad_speed.json").write_text(json.dumps(bad, indent=2),
                                            encoding="utf-8")

    (workdir / "bad_json.json").write_text(
        '{\n  "sources" :,\n  "clips": []\n}\n',
        encoding="utf-8")

    alpha = (workdir / "alpha.vtt").read_text(encoding="utf-8")
    (workdir / "alpha_bad_region.vtt").write_text(
        alpha.replace("region:top", "region:nope"), encoding="utf-8")
    (workdir / "alpha_bad_marker.vtt").write_text(
        alpha.replace("<00:00:01.500>late", "<00:00:00.250>late"),
        encoding="utf-8")
    (workdir / "alpha_bad_block.vtt").write_text(
        alpha.replace("NOTE\nalpha source notes\n\n", "")
        .replace("STYLE\n", "RANDOM-THING\n", 1),
        encoding="utf-8")
    (workdir / "alpha_bad_style.vtt").write_text(
        alpha.replace("::cue(.hot) { color: red; }",
                      "div.weird { color: red; }"),
        encoding="utf-8")

    for name, vtt in (
        ("bad_region.json", "alpha_bad_region.vtt"),
        ("bad_marker.json", "alpha_bad_marker.vtt"),
        ("bad_block.json", "alpha_bad_block.vtt"),
        ("bad_style.json", "alpha_bad_style.vtt"),
    ):
        bad = json.loads(json.dumps(good))
        bad["sources"]["alpha"] = vtt
        (workdir / name).write_text(json.dumps(bad, indent=2),
                                    encoding="utf-8")


def main():
    workdir = Path(tempfile.mkdtemp(prefix="vttcut_accept_"))
    try:
        for name in ("alpha.vtt", "beta.vtt", "edl.json"):
            shutil.copy(FIX / name, workdir / name)
        write_bad_fixtures(workdir)
        clips, expected, text, cues = (
            test_expansion_matches_to_millisecond(workdir))
        test_visibility_three_frames(workdir, clips, expected, cues)
        test_reorder_stability(workdir)
        test_bad_inputs(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("\n%d failure(s)" % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
