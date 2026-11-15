#!/usr/bin/env python3
"""End-to-end acceptance run for canon.py.

Runs the shared reference scenarios through the real command line, exactly as
a caller would:

1. every scenario must cluster exactly as hand-adjudicated, and every
   conflict must be recorded with its arbitration reason;
2. the same scenarios are replayed with shuffled input and with two language
   configurations; cluster summaries and digests must stay identical.

Usage::

    python3 acceptance.py
"""

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

import scenarios

HERE = os.path.dirname(os.path.abspath(__file__))
CANON = os.path.join(HERE, "canon.py")


def run_cli(pages, langs, workdir, tag):
    """Invoke canon.py as a subprocess and return the parsed output."""
    request = {"pages": pages, "langs": langs, "seed": 1}
    in_path = os.path.join(workdir, "in-%s.json" % tag)
    out_path = os.path.join(workdir, "out-%s.json" % tag)
    with open(in_path, "w", encoding="utf-8") as handle:
        json.dump(request, handle)
    proc = subprocess.run(
        [sys.executable, CANON, in_path, out_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "canon.py failed for %s: %s" % (tag, proc.stderr.strip())
        )
    with open(out_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def summarise(result):
    return [cluster["members"] for cluster in result["clusters"]]


def conflict_reasons(result):
    out = {}
    for url, verdict in result["verdicts"].items():
        reasons = sorted(
            part for part in verdict.split(";") if part.startswith("conflict:")
        )
        if reasons:
            out[url] = reasons
    return out


def shuffle_case(pages, rng):
    shuffled = []
    for entry in pages:
        copy = dict(entry)
        copy["hreflang"] = list(entry.get("hreflang") or [])
        rng.shuffle(copy["hreflang"])
        shuffled.append(copy)
    rng.shuffle(shuffled)
    return shuffled


def main():
    workdir = tempfile.mkdtemp(prefix="canon-acceptance-")
    failures = []
    conflicts_seen = 0
    try:
        # ---- pass 1: reference adjudication ------------------------------
        for name, pages, expected_clusters, expected_conflicts in scenarios.CASES:
            result = run_cli(pages, scenarios.LANGUAGES[0], workdir, name)
            got_clusters = summarise(result)
            if got_clusters != expected_clusters:
                failures.append(
                    "%s: clusters %r != expected %r"
                    % (name, got_clusters, expected_clusters)
                )
            got_conflicts = conflict_reasons(result)
            if got_conflicts != expected_conflicts:
                failures.append(
                    "%s: conflicts %r != expected %r"
                    % (name, got_conflicts, expected_conflicts)
                )
            conflicts_seen += sum(len(v) for v in got_conflicts.values())

        # ---- pass 2: shuffled input, two language configs ----------------
        rng = random.Random(31337)
        for name, pages, _clusters, _conflicts in scenarios.CASES:
            baseline = run_cli(pages, scenarios.LANGUAGES[0], workdir, name)
            for index, langs in enumerate(scenarios.LANGUAGES):
                shuffled = shuffle_case(pages, rng)
                result = run_cli(
                    shuffled, langs, workdir, "%s-shuf%d" % (name, index)
                )
                if summarise(result) != summarise(baseline):
                    failures.append(
                        "%s: shuffled/langs=%r changed clusters" % (name, langs)
                    )
                if result["digest"] != baseline["digest"]:
                    failures.append(
                        "%s: shuffled/langs=%r changed digest" % (name, langs)
                    )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if failures:
        sys.stderr.write("ACCEPTANCE FAILED\n")
        for failure in failures:
            sys.stderr.write("  - %s\n" % failure)
        return 1

    print(
        "acceptance ok: %d scenarios, %d recorded conflict reasons, "
        "%d shuffle/language replays"
        % (
            len(scenarios.CASES),
            conflicts_seen,
            len(scenarios.CASES) * len(scenarios.LANGUAGES),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
