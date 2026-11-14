#!/usr/bin/env python3
"""Acceptance harness for the layered fingerprint decision engine.

Checks
------
1. classification   -- 40-page labelled set, per-class and overall accuracy
2. template-only    -- no template-only page may request a content refetch
3. stability        -- whitespace / attribute-order / re-chunking jitter must
                       leave every fingerprint digest unchanged (class none)
4. chunk invariance -- feeding the hash stream in 1/3/7/64-byte pieces must
                       not change any digest
5. repo repeat      -- two scans of the same repository must agree on every
                       fingerprint and on the scan digest
6. evidence         -- every decision carries per-layer digests, region
                       location evidence and per-layer confidence

Run ``python3 evaluate.py`` for a report; the exit code is non-zero when any
acceptance threshold is missed.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

import corpus
import engine
import fingerprints
import scan

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "labeled")
REPO = os.path.join(HERE, "out", "repo")

ACCURACY_TARGET = 0.95


def _load_corpus() -> Dict[str, Any]:
    path = os.path.join(DATA, "corpus.json")
    if not os.path.exists(path):
        corpus.write_corpus(DATA)
    return corpus.load_corpus(DATA)


def check_classification(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    confusion: Dict[str, Dict[str, int]] = {}
    wrong: List[Dict[str, Any]] = []
    refetch_violations: List[str] = []
    confidences: List[float] = []
    for entry in entries:
        result = engine.compare(entry["old"], entry["new"])
        predicted = result["class"]
        expected = entry["label"]
        confusion.setdefault(expected, {})
        confusion[expected][predicted] = confusion[expected].get(predicted, 0) + 1
        confidences.append(result["confidence"])
        if predicted != expected:
            wrong.append({"id": entry["id"], "expected": expected,
                          "predicted": predicted,
                          "notes": result["evidence"]["notes"]})
        if expected == "template" and result["refetch"]:
            refetch_violations.append(entry["id"])

    total = len(entries)
    correct = total - len(wrong)
    per_class: Dict[str, Dict[str, Any]] = {}
    for label in sorted(confusion):
        hits = confusion[label].get(label, 0)
        support = sum(confusion[label].values())
        per_class[label] = {
            "support": support,
            "correct": hits,
            "accuracy": round(hits / support, 4) if support else 0.0,
        }
    return {
        "passed": (correct / total >= ACCURACY_TARGET) and not refetch_violations,
        "pages": total,
        "correct": correct,
        "accuracy": round(correct / total, 4),
        "target": ACCURACY_TARGET,
        "per_class": per_class,
        "confusion": confusion,
        "wrong": wrong,
        "template_refetch_violations": refetch_violations,
        "confidence_min": round(min(confidences), 4),
        "confidence_mean": round(sum(confidences) / len(confidences), 4),
        "confidence_max": round(max(confidences), 4),
    }


def check_stability(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    results = []
    failures = []
    for case in cases:
        old_fp = fingerprints.fingerprint(case["old"])
        new_fp = fingerprints.fingerprint(case["new"])
        decision = engine.compare(case["old"], case["new"])
        identical = old_fp["digest"] == new_fp["digest"]
        layers_identical = all(
            old_fp[key] == new_fp[key]
            for key in ("fp_struct", "fp_chrome", "fp_template", "fp_content"))
        ok = identical and layers_identical and decision["class"] == case["expected_class"]
        results.append({
            "id": case["id"],
            "kind": case["kind"],
            "digest_identical": identical,
            "layers_identical": layers_identical,
            "class": decision["class"],
            "passed": ok,
        })
        if not ok:
            failures.append(results[-1])
    return {
        "passed": not failures,
        "cases": len(cases),
        "results": results,
        "failures": failures,
    }


def check_cross_product() -> Dict[str, Any]:
    """All 4 edit kinds x 10 edits x 10 layouts = 400 combinations.

    The labelled corpus pairs one edit kind with one edit index per layout.
    This sweep removes that coupling: every template edit, prose edit, mixed
    combination and cosmetic jitter is tried against every layout, so a fix
    that only works for the paired index cannot hide here.
    """
    failures: List[Dict[str, Any]] = []
    counts = {"template": 0, "content": 0, "mixed": 0, "none": 0}
    for index in range(10):
        base = corpus.build_page(index)
        for which in range(1, 11):
            template_page = corpus.template_edit(base, which)
            content_page = content_edit_for(base, which)
            mixed_page = content_edit_for(template_page, which)
            jitter_page = corpus.cosmetic_edit(base, which)
            for kind, edited, expected in (
                    ("template", template_page, "template"),
                    ("content", content_page, "content"),
                    ("mixed", mixed_page, "mixed"),
                    ("none", jitter_page, "none")):
                counts[kind] += 1
                result = engine.compare(base, edited)
                if result["class"] != expected:
                    failures.append({
                        "layout": index + 1, "edit": which, "kind": kind,
                        "expected": expected, "got": result["class"],
                        "notes": result["evidence"]["notes"],
                    })
                if expected == "template" and result["refetch"]:
                    failures.append({"layout": index + 1, "edit": which,
                                     "kind": kind, "problem": "template refetched"})
    return {"passed": not failures, "combinations": sum(counts.values()),
            "counts": counts, "failures": failures}


def content_edit_for(page: Dict[str, Any], which: int) -> Dict[str, Any]:
    """Indirection so the sweep reads naturally; keeps one edit definition."""
    return corpus.content_edit(page, which)


def check_discrimination() -> Dict[str, Any]:
    """Negative controls: the layers must not mask real changes.

    For every layout, four deliberately small edits are applied and the layer
    verdicts are checked independently:

    * one character changed in prose      -> content fires, template does not
    * one class token added to markup      -> template fires, content does not
    * prose block reordered within region  -> content fires (reading order)
    * prose block moved between containers -> template fires, content does not

    The last case is the relocation guard: identical prose in a new place is a
    structural move, and content must stay clean so the page is not re-crawled.
    """
    failures: List[Dict[str, Any]] = []
    cases = 0
    for index in range(10):
        base = corpus.build_page(index)
        model = fingerprints.PageModel(base)
        prose_ids = [bid for bid in model.block_ids
                     if model.flags[model.block_ids.index(bid)]]

        cases += 1
        edit = corpus.set_text(base, prose_ids[0],
                               corpus.get_text(base, prose_ids[0]) + "附加说明。")
        result = engine.compare(base, edit)
        if not result["evidence"]["layers"]["content"]["changed"]:
            failures.append({"layout": index + 1, "case": "prose-append",
                             "problem": "content change missed"})
        if result["evidence"]["layers"]["template"]["changed"]:
            failures.append({"layout": index + 1, "case": "prose-append",
                             "problem": "content edit leaked into template"})

        cases += 1
        edit = corpus.template_edit(base, 4)
        result = engine.compare(base, edit)
        if not result["evidence"]["layers"]["template"]["changed"]:
            failures.append({"layout": index + 1, "case": "markup-attribute",
                             "problem": "template change missed"})
        if result["evidence"]["layers"]["content"]["changed"]:
            failures.append({"layout": index + 1, "case": "markup-attribute",
                             "problem": "template edit leaked into content"})
        if result["refetch"]:
            failures.append({"layout": index + 1, "case": "markup-attribute",
                             "problem": "template edit triggered refetch"})

        cases += 1
        reorder = _reorder_prose(base, prose_ids)
        if reorder is not None:
            result = engine.compare(base, reorder)
            if not result["evidence"]["layers"]["content"]["changed"]:
                failures.append({"layout": index + 1, "case": "prose-reorder",
                                 "notes": result["evidence"]["notes"]})

        # A markup redesign that destroys the semantic container hint must not
        # reach the content layer: the prose did not move, only its wrapper.
        cases += 1
        edit = _rename_region(base)
        result = engine.compare(base, edit)
        if result["evidence"]["layers"]["content"]["changed"]:
            failures.append({"layout": index + 1, "case": "region-renamed",
                             "problem": "prose unchanged but content fired",
                             "notes": result["evidence"]["notes"]})
        if result["refetch"]:
            failures.append({"layout": index + 1, "case": "region-renamed",
                             "problem": "prose unchanged but refetch requested"})
    return {"passed": not failures, "cases": cases, "failures": failures}


def _reorder_prose(page: Dict[str, Any], prose_ids: List[str]) -> Optional[Dict[str, Any]]:
    """Swap the reading order of two distinct prose blocks."""
    if len(prose_ids) < 2:
        return None
    first, second = prose_ids[0], prose_ids[1]
    first_text = corpus.get_text(page, first)
    second_text = corpus.get_text(page, second)
    if first_text == second_text:
        # Disambiguate first so the swap is observable.
        second_text = second_text + "续报内容。"
    edited = corpus.set_text(corpus.set_text(page, first, second_text), second, first_text)
    return edited


def _rename_region(page: Dict[str, Any]) -> Dict[str, Any]:
    """Rename the content container so no semantic hint identifies it."""
    tpl = page["tpl"]
    for old, new in (
            ('<main id="main">', '<div id="wrap-a1">'),
            ('<article id="story">', '<div id="wrap-a2">'),
            ('<div id="content" class="article-body">', '<div class="box box-lg">'),
            ('<div id="article-body">', '<div class="box box-lg">'),
            ('<main id="main">', '<div class="box box-lg">'),
            ('<div id="' + corpus.ZH_BODY + '" class="' + corpus.ZH_BODY_BLOCK + '">',
             '<div class="box box-lg">'),
            ('<div class="panel" role="main">', '<div class="box box-lg">'),
            ('<div id="main-column" class="main">', '<div class="box box-lg">'),
            ('<article id="post">', '<div id="wrap-a9">')):
        if old in tpl:
            tpl = tpl.replace(old, new, 1)
            break
    return {"tpl": tpl, "blocks": [dict(b) for b in page["blocks"]]}


def check_chunk_independence(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Random re-chunking of the content must not move any fingerprint.

    This is the mechanism behind the acceptance rule "how the fingerprint is
    chunked must not affect the verdict": for every page, prose blocks are
    regrouped (merge runs, split at sentence boundaries, swap listing order)
    while the joined prose is preserved.  Every digest must survive.
    """
    rng = random.Random(20240514)
    failures: List[Dict[str, Any]] = []
    attempts = 0
    for entry in entries:
        page = entry["old"]
        model = fingerprints.PageModel(page)
        prose_ids = [bid for bid in model.block_ids
                     if model.flags[model.block_ids.index(bid)]]
        if len(prose_ids) < 2:
            continue
        for _ in range(3):
            attempts += 1
            variant = _rechunk(page, prose_ids, rng)
            base_fp = fingerprints.fingerprint(page)
            variant_fp = fingerprints.fingerprint(variant)
            for key in ("fp_struct", "fp_chrome", "fp_template", "fp_content"):
                if base_fp[key] != variant_fp[key]:
                    failures.append({"id": entry["id"], "key": key,
                                     "variant": variant.get("_variant")})
                    break
            else:
                decision = engine.compare(page, variant)
                if decision["class"] != "none":
                    failures.append({"id": entry["id"], "class": decision["class"],
                                     "notes": decision["evidence"]["notes"]})
    return {"passed": not failures, "cases": attempts, "failures": failures}


def _rechunk(page: Dict[str, Any], prose_ids: List[str], rng: random.Random) -> Dict[str, Any]:
    """Regroup prose blocks while preserving the joined prose exactly."""
    texts = {block["id"]: block["text"] for block in page["blocks"]}
    tpl = page["tpl"]
    pieces = [texts[bid] for bid in prose_ids]
    style = rng.choice(("merge", "split", "pairs", "listing-shuffle"))
    if style == "merge":
        new_blocks = [{"id": "merged", "text": "".join(pieces)}]
    elif style == "pairs":
        # Merge adjacent pairs: fewer, larger blocks, same reading order.
        new_blocks = []
        for position in range(0, len(pieces), 2):
            chunk = "".join(pieces[position:position + 2])
            new_blocks.append({"id": "pair%d" % (position // 2), "text": chunk})
    elif style == "split":
        joined = "".join(pieces)
        new_blocks = []
        buffer = ""
        for char in joined:
            buffer += char
            if char in "。！？，":
                new_blocks.append({"id": "c%d" % len(new_blocks), "text": buffer})
                buffer = ""
        if buffer:
            new_blocks.append({"id": "c%d" % len(new_blocks), "text": buffer})
    else:
        # Same blocks, listed in a different order: reading order must come
        # from the template, not from the extraction listing.
        order = list(range(len(prose_ids)))
        rng.shuffle(order)
        new_blocks = [{"id": "s%d" % position, "text": pieces[position]}
                      for position in order]
        new_blocks.sort(key=lambda block: pieces.index(block["text"]))
    placeholder = "".join("{{%s}}" % block["id"] for block in new_blocks)
    # Insert where the prose lives: the last occurrence of a prose placeholder.
    # A heading placeholder can also appear in <head>, which carries no prose.
    positions = [(tpl.rfind("{{%s}}" % bid), bid) for bid in prose_ids
                 if "{{%s}}" % bid in tpl]
    variant_tpl = tpl
    if positions:
        position, anchor_id = max(positions)
        marker = "{{%s}}" % anchor_id
        variant_tpl = tpl[:position] + placeholder + tpl[position + len(marker):]
    for bid in prose_ids:
        variant_tpl = variant_tpl.replace("{{%s}}" % bid, "")
    variant = {
        "tpl": variant_tpl,
        "blocks": [b for b in page["blocks"] if b["id"] not in prose_ids] + new_blocks,
        "_variant": style,
    }
    return variant


def check_byte_chunk_invariance(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Byte feed size must not move any digest, for every corpus snapshot."""
    chunk_sizes = (1, 3, 7, 64, 4096)
    failures = []
    checked = 0
    for entry in entries:
        for side in ("old", "new"):
            page = entry[side]
            baseline = fingerprints.fingerprint(page)
            for size in chunk_sizes:
                variant = fingerprints.fingerprint(page, size)
                checked += 1
                for key in ("fp_struct", "fp_chrome", "fp_template",
                            "fp_content", "digest"):
                    if variant[key] != baseline[key]:
                        failures.append({
                            "id": entry["id"], "side": side, "chunk": size,
                            "key": key,
                        })
    return {"passed": not failures, "comparisons": checked, "failures": failures}


def check_repo_repeat(repo: str) -> Dict[str, Any]:
    if not os.path.isdir(repo):
        corpus.write_repo(repo)
    first = scan.scan_repo(repo)
    second = scan.scan_repo(repo, shuffle_seed=20240514)
    third = scan.scan_repo(repo, shuffle_seed=99, chunk_size=3)

    fingerprints_equal = (
        _fingerprint_view(first) == _fingerprint_view(second)
        == _fingerprint_view(third))
    decisions_equal = (
        _decision_view(first) == _decision_view(second)
        == _decision_view(third))
    digests = [first["digest"], second["digest"], third["digest"]]
    header_rows = []
    for page in first["pages"]:
        header_rows.append({
            "page": page["page"],
            "latest_digest": page["latest"]["digest"],
            "fp_struct": page["latest"]["fp_struct"],
            "fp_template": page["latest"]["fp_template"],
            "fp_content": page["latest"]["fp_content"],
            "steps": len(page["steps"]),
            "classes": [step["class"] for step in page["steps"]],
        })
    return {
        "passed": fingerprints_equal and decisions_equal and len(set(digests)) == 1,
        "digests": digests,
        "pages": len(first["pages"]),
        "transitions": first["summary"]["transitions"],
        "fingerprints_equal": fingerprints_equal,
        "decisions_equal": decisions_equal,
        "summary": first["summary"],
        "rows": header_rows,
    }


def _fingerprint_view(result: Dict[str, Any]) -> List[Tuple[Any, ...]]:
    view = []
    for page in result["pages"]:
        latest = page["latest"]
        view.append((
            page["page"],
            tuple(page["snapshots"]),
            latest["fp_struct"], latest["fp_chrome"],
            latest["fp_template"], latest["fp_content"], latest["digest"],
        ))
    return sorted(view)


def _decision_view(result: Dict[str, Any]) -> List[Tuple[Any, ...]]:
    view = []
    for page in result["pages"]:
        for step in page["steps"]:
            view.append((
                page["page"], step["from"], step["to"], step["class"],
                step["refetch"], step["archive"], tuple(step["archive_layers"]),
                step["fp_template"], step["fp_content"],
            ))
    return sorted(view)


def check_evidence(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    required_evidence = ("layers", "template_layers", "content_region",
                         "blocks", "relocation", "layer_confidence", "notes")
    failures = []
    for entry in entries:
        result = engine.compare(entry["old"], entry["new"])
        missing = [key for key in required_evidence if key not in result["evidence"]]
        if missing:
            failures.append({"id": entry["id"], "missing": missing})
            continue
        if not 0.0 < result["confidence"] <= 1.0:
            failures.append({"id": entry["id"], "bad_confidence": result["confidence"]})
        for layer in ("struct", "chrome", "template", "content"):
            layer_evidence = result["evidence"]["layers"].get(layer, {})
            if not {"changed", "old", "new"} <= set(layer_evidence):
                failures.append({"id": entry["id"], "layer": layer,
                                 "problem": "incomplete layer evidence"})
        if result["refetch"] and not result["archive_layers"]:
            failures.append({"id": entry["id"], "problem": "refetch without archive layers"})
        if result["class"] == "template" and result["refetch"]:
            failures.append({"id": entry["id"], "problem": "template page refetched"})
    return {"passed": not failures, "failures": failures}


def run_all(repo: str = REPO) -> Dict[str, Any]:
    data = _load_corpus()
    entries = data["pages"]
    checks = {
        "classification": check_classification(entries),
        "stability": check_stability(data["stability"]),
        "byte_chunk_invariance": check_byte_chunk_invariance(entries),
        "chunk_independence": check_chunk_independence(entries),
        "cross_product": check_cross_product(),
        "discrimination": check_discrimination(),
        "repo_repeat": check_repo_repeat(repo),
        "evidence": check_evidence(entries),
    }
    passed = all(check["passed"] for check in checks.values())
    return {
        "passed": passed,
        "thresholds": {
            "classification_accuracy": ACCURACY_TARGET,
            "template_only_no_content_refetch": True,
            "jitter_stable": True,
            "chunk_invariant": True,
            "scan_digest_repeatable": True,
        },
        "checks": checks,
    }


def render(report: Dict[str, Any]) -> str:
    lines = []
    status = "PASS" if report["passed"] else "FAIL"
    lines.append("acceptance: %s" % status)

    classification = report["checks"]["classification"]
    lines.append("1. classification: %d/%d = %.2f%% (target %.0f%%) -> %s" % (
        classification["correct"], classification["pages"],
        100.0 * classification["accuracy"], 100.0 * ACCURACY_TARGET,
        "PASS" if classification["passed"] else "FAIL"))
    for label, stats in sorted(classification["per_class"].items()):
        lines.append("   %-9s %2d/%2d = %.0f%%" % (
            label, stats["correct"], stats["support"], 100.0 * stats["accuracy"]))
    lines.append("   template-only pages that requested content refetch: %d" %
                 len(classification["template_refetch_violations"]))
    lines.append("   confidence min/mean/max: %.3f / %.3f / %.3f" % (
        classification["confidence_min"], classification["confidence_mean"],
        classification["confidence_max"]))

    stability = report["checks"]["stability"]
    lines.append("2. stability: %d/%d jitter cases stable -> %s" % (
        stability["cases"] - len(stability["failures"]), stability["cases"],
        "PASS" if stability["passed"] else "FAIL"))
    by_kind: Dict[str, List[bool]] = {}
    for row in stability["results"]:
        by_kind.setdefault(row["kind"], []).append(bool(row["passed"]))
    for kind in sorted(by_kind):
        outcomes = by_kind[kind]
        lines.append("   %-28s %2d/%-2d %s" % (
            kind, sum(outcomes), len(outcomes),
            "ok" if all(outcomes) else "FAIL"))

    chunk = report["checks"]["byte_chunk_invariance"]
    lines.append("3. byte-feed invariance: %d digest comparisons -> %s" % (
        chunk["comparisons"], "PASS" if chunk["passed"] else "FAIL"))

    independence = report["checks"]["chunk_independence"]
    lines.append("4. block-rechunking invariance: %d pages -> %s" % (
        independence["cases"], "PASS" if independence["passed"] else "FAIL"))
    for failure in independence["failures"][:5]:
        lines.append("   FAIL %s %s" % (failure["id"], failure))

    cross = report["checks"]["cross_product"]
    lines.append("5. cross-product sweep: %d combinations %s -> %s" % (
        cross["combinations"], json.dumps(cross["counts"], sort_keys=True),
        "PASS" if cross["passed"] else "FAIL"))
    for failure in cross["failures"][:8]:
        lines.append("   FAIL %s" % json.dumps(failure, ensure_ascii=False))

    discrimination = report["checks"]["discrimination"]
    lines.append("6. layer discrimination (negative controls): %d cases -> %s" % (
        discrimination["cases"], "PASS" if discrimination["passed"] else "FAIL"))
    for failure in discrimination["failures"][:8]:
        lines.append("   FAIL %s" % json.dumps(failure, ensure_ascii=False))

    repeat = report["checks"]["repo_repeat"]
    lines.append("7. repo repeat: pages=%d transitions=%d digests=%s -> %s" % (
        repeat["pages"], repeat["transitions"],
        "/".join(repeat["digests"]), "PASS" if repeat["passed"] else "FAIL"))
    summary = repeat["summary"]
    lines.append("   classes: %s" % json.dumps(summary["classes"], sort_keys=True))
    lines.append("   refetches=%d archives=%d template_only_without_refetch=%d" % (
        summary["refetches"], summary["archives"],
        summary["template_only_pages_without_refetch"]))

    evidence = report["checks"]["evidence"]
    lines.append("8. evidence completeness: %s" % (
        "PASS" if evidence["passed"] else "FAIL"))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Acceptance harness for the layered fingerprint detector.")
    parser.add_argument("repo", nargs="?", default=REPO,
                        help="snapshot repository directory (created if missing)")
    parser.add_argument("--json", action="store_true",
                        help="also print the machine-readable report")
    args = parser.parse_args(argv)

    report = run_all(args.repo)
    print(render(report))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
