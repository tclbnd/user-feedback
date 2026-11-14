#!/usr/bin/env python3
"""One-shot walkthrough: build fixtures, scan, verify, evaluate.

    python3 run_all.py

Produces, under ``out/``:

  out/repo/         snapshot repository (41 pages, 64 transitions)
  out/scan.json     full scan report with per-page decisions and the digest
  out/decision.json one worked example (template-only redesign)
  out/eval.json     machine-readable acceptance report

Exit code is 0 only when every acceptance check passes.
"""

from __future__ import annotations

import json
import os
import sys

import corpus
import diff
import engine
import evaluate
import scan

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
REPO = os.path.join(OUT, "repo")
DATA = os.path.join(HERE, "data", "labeled")


def _write(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=1)


def main() -> int:
    print("1. fixtures")
    print("   corpus:", json.dumps(corpus.write_corpus(DATA)))
    print("   repo:  ", json.dumps(corpus.write_repo(REPO)))

    print("2. scan (twice, shuffled listings and a different hash feed)")
    report = scan.scan_repo(REPO)
    _write(os.path.join(OUT, "scan.json"), report)
    second = scan.scan_repo(REPO, shuffle_seed=11)
    third = scan.scan_repo(REPO, chunk_size=7)
    consistent = report["digest"] == second["digest"] == third["digest"]
    print("   digest %s (repeatable: %s)" % (report["digest"], consistent))
    print("   classes:", json.dumps(report["summary"]["classes"], sort_keys=True))
    print("   refetches=%d archives=%d" % (
        report["summary"]["refetches"], report["summary"]["archives"]))
    if not consistent:
        print("ERROR: scan digest is not reproducible", file=sys.stderr)
        return 1

    print("3. worked example (template-only redesign, must not refetch)")
    base = corpus.build_page(0)
    redesigned = corpus.template_edit(base, 2)
    example = engine.compare(base, redesigned)
    _write(os.path.join(OUT, "decision.json"), example)
    print("   class=%s refetch=%s archive=%s confidence=%.3f" % (
        example["class"], example["refetch"], example["archive"],
        example["confidence"]))
    print("   layers:", json.dumps(
        {key: value["changed"] for key, value in example["evidence"]["layers"].items()},
        sort_keys=True))

    print("4. acceptance")
    verdict = evaluate.run_all(REPO)
    _write(os.path.join(OUT, "eval.json"), verdict)
    print(evaluate.render(verdict))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
