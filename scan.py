#!/usr/bin/env python3
"""Snapshot-store scanner with a reproducible scan digest.

A "repository" here is a directory of page snapshot directories::

    <repo>/<page-dir>/<snapshot>.json

``scan`` fingerprints every snapshot, compares consecutive snapshots per page
(snapshot names sort lexicographically), and emits:

* ``pages``   -- per page: fingerprint of the newest snapshot, per-step
                 comparisons, and the latest decision
* ``summary`` -- aggregate counts by class and by action
* ``digest``  -- a scan digest that depends only on fingerprints and
                 decisions, so scanning the same repository twice (in any
                 directory order) always yields the same value

Two runs of the same repository therefore agree by construction; the CLI has a
``--verify`` mode that proves it by scanning twice with shuffled directory
listings and diffing the results.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

import engine
import fingerprints


def list_pages(repo: str) -> List[str]:
    entries = []
    for name in os.listdir(repo):
        path = os.path.join(repo, name)
        if os.path.isdir(path):
            entries.append(name)
    return sorted(entries)


def list_snapshots(page_dir: str) -> List[Tuple[str, str]]:
    snapshots = []
    for name in os.listdir(page_dir):
        if not name.endswith(".json"):
            continue
        snapshots.append((name[:-5], os.path.join(page_dir, name)))
    snapshots.sort()
    return snapshots


def load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def scan_page(page: str, snapshots: List[Tuple[str, str]],
              chunk_size: Optional[int] = None) -> Dict[str, Any]:
    if not snapshots:
        raise engine.PageError("%s: no snapshots" % page)
    steps = []
    latest = None
    for position, (label, path) in enumerate(snapshots):
        snapshot = load_json(path)
        fp = fingerprints.fingerprint(snapshot, chunk_size)
        latest = {"snapshot": label, **{key: fp[key] for key in
                                        ("fp_struct", "fp_chrome", "fp_template",
                                         "fp_content", "digest")}}
        latest["region_method"] = fp["region"]["method"]
        latest["region_confidence"] = fp["region"]["confidence"]
        latest["content_blocks"] = len(fp["region"]["block_ids"])
        if position == 0:
            continue
        previous_label, previous_path = snapshots[position - 1]
        result = engine.compare(load_json(previous_path), snapshot, chunk_size)
        record = engine.decision_record(result)
        record["from"] = previous_label
        record["to"] = label
        record["reason"] = result["reason"]
        record["notes"] = result["evidence"]["notes"]
        steps.append(record)
    return {
        "page": page,
        "snapshots": [label for label, _ in snapshots],
        "latest": latest,
        "steps": steps,
    }


def scan_repo(repo: str, chunk_size: Optional[int] = None,
              shuffle_seed: Optional[int] = None) -> Dict[str, Any]:
    if not os.path.isdir(repo):
        raise engine.PageError("not a directory: %s" % repo)
    pages = list_pages(repo)
    if shuffle_seed is not None:
        # Directory listing order must not influence the scan at all.
        random.Random(shuffle_seed).shuffle(pages)
    scanned = []
    for page in pages:
        scanned.append(scan_page(page, list_snapshots(os.path.join(repo, page)),
                                 chunk_size))
    scanned.sort(key=lambda entry: entry["page"])

    counts: Dict[str, int] = {name: 0 for name in engine.CLASSES}
    refetches = 0
    archives = 0
    step_records: List[Dict[str, Any]] = []
    for entry in scanned:
        for step in entry["steps"]:
            counts[step["class"]] = counts.get(step["class"], 0) + 1
            refetches += 1 if step["refetch"] else 0
            archives += 1 if step["archive"] else 0
            step_records.append({
                "page": entry["page"],
                "from": step["from"],
                "to": step["to"],
                "class": step["class"],
                "refetch": step["refetch"],
                "archive": step["archive"],
                "archive_layers": step["archive_layers"],
                "confidence": step["confidence"],
            })
    fingerprint_records = [{
        "page": entry["page"],
        "snapshots": entry["snapshots"],
        "latest": entry["latest"],
    } for entry in scanned]

    digest = engine.records_digest(fingerprint_records + step_records)
    return {
        "schema": "repo-scan/1",
        "repo": os.path.abspath(repo),
        "pages": scanned,
        "summary": {
            "pages": len(scanned),
            "transitions": len(step_records),
            "classes": counts,
            "refetches": refetches,
            "archives": archives,
            "template_only_pages_without_refetch": sum(
                1 for step in step_records
                if step["class"] == "template" and not step["refetch"]),
        },
        "digest": digest,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Scan a snapshot repository.")
    parser.add_argument("repo", help="repository directory")
    parser.add_argument("output", help="output JSON, or - for stdout")
    parser.add_argument("--chunk", type=int, default=0,
                        help="hash feed size; must not affect any digest")
    parser.add_argument("--verify", action="store_true",
                        help="scan twice with shuffled listings and compare digests")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    try:
        scan = scan_repo(args.repo, args.chunk or None)
    except engine.PageError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 3

    payload: Dict[str, Any] = dict(scan)
    if args.verify:
        second = scan_repo(args.repo, args.chunk or None, shuffle_seed=20240514)
        third = scan_repo(args.repo, args.chunk or None, shuffle_seed=7)
        consistent = scan["digest"] == second["digest"] == third["digest"]
        payload["verify"] = {
            "consistent": consistent,
            "digests": [scan["digest"], second["digest"], third["digest"]],
        }
        if not consistent:
            sys.stderr.write("error: scan digest is not reproducible\n")
            return 4

    text = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      indent=2 if args.pretty else None)
    if args.output == "-":
        sys.stdout.write(text + "\n")
    else:
        directory = os.path.dirname(os.path.abspath(args.output))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
