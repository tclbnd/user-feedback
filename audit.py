#!/usr/bin/env python3
"""Initial local graph auditor."""

import hashlib
import json
import sys
from collections import deque
from pathlib import Path

from edge_store import EdgeStore


def digest_state(site):
    value = 0
    entities = [("roots", "roots", sorted(set(site["roots"])))]
    for kind in ("page", "resource", "redirect"):
        for key, record in site.get(kind + "s", {}).items():
            if kind == "page":
                record = {field: sorted(set(record.get(field, ())))
                          for field in ("nav", "body", "res", "anchors")}
            entities.append((kind, key, record))
    for entity in entities:
        encoded = json.dumps(entity, sort_keys=True, separators=(",", ":")).encode()
        value ^= int.from_bytes(hashlib.sha256(encoded).digest(), "big")
    return f"{value:064x}"


def run(site):
    pages = site["pages"]
    roots = sorted(set(site["roots"]) & pages.keys())
    edges = EdgeStore()
    for source, page in pages.items():
        for target in page.get("nav", ()):
            if target in pages:
                edges.add(source, "nav", target)
    reached = set(roots)
    pending = deque(roots)
    while pending:
        for _, target in edges.outgoing(pending.popleft()):
            if target not in reached:
                reached.add(target)
                pending.append(target)
    orphans = sorted(page for page in pages if page not in reached and not edges.incoming(page))
    return {
        "index": edges.export(), "issues": [f"orphan:{page}" for page in orphans],
        "paths": {f"orphan:{page}": [page] for page in orphans},
        "batches": [], "digest": digest_state(site),
    }


def main():
    source, target = map(Path, sys.argv[1:])
    site = json.loads(source.read_text(encoding="utf-8"))
    report = run(site)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
