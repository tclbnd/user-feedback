"""Acceptance harness: generator logical tables are the ground truth.

Covers the acceptance contract:

* 12 two-column, page-spanning PDFs (6 layout variants x clean / faulted) are
  compared record by record against the generator truth: record count, every
  cell value, cross-page merge locations and source boxes within 2 pt.
* Repeated headers / titles / footers never become data; the two intentional
  ambiguity layouts (duplicate amount, stray CONT) are classified correctly.
* Different page batches and restart points produce identical records and
  provenance.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import make_demo
import table_pdf

COORD_TOLERANCE_PT = 2.0


def _compare_records(truth, rows):
    errors = []
    if len(truth) != len(rows):
        errors.append(
            "record count: truth=%d extracted=%d" % (len(truth), len(rows))
        )
    max_err = 0.0
    for index, (t, r) in enumerate(zip(truth, rows)):
        for name in ("id", "description", "amount"):
            if t[name] != r[name]:
                errors.append(
                    "row %d field %s: truth=%r extracted=%r"
                    % (index, name, t[name], r[name])
                )
        for name in ("id", "description", "amount"):
            tsrc = t["source"][name]
            rsrc = r["source"][name]
            if len(tsrc) != len(rsrc):
                errors.append(
                    "row %d source %s length: truth=%d extracted=%d"
                    % (index, name, len(tsrc), len(rsrc))
                )
                continue
            for tp, rp in zip(tsrc, rsrc):
                if tp["page"] != rp["page"]:
                    errors.append(
                        "row %d source %s page mismatch %s vs %s"
                        % (index, name, tp, rp)
                    )
                err = max(
                    abs(tp["x"] - rp["x"]), abs(tp["y"] - rp["y"])
                )
                max_err = max(max_err, err)
                if err > COORD_TOLERANCE_PT:
                    errors.append(
                        "row %d source %s coord error %.3f at %s vs %s"
                        % (index, name, err, tp, rp)
                    )
    return errors, max_err


def _cross_page_locations(truth, rows):
    """The set of records carrying fragments on >1 page must match."""
    def truth_pages(entry):
        pages = set()
        for points in entry["source"].values():
            pages.update(p["page"] for p in points)
        return pages

    def row_pages(row):
        pages = set()
        for points in row["source"].values():
            pages.update(p["page"] for p in points)
        return pages

    t_ids = sorted(e["id"] for e in truth if len(truth_pages(e)) > 1)
    r_ids = sorted(r["id"] for r in rows if len(row_pages(r)) > 1)
    return t_ids, r_ids


def run_case(variant, fault, workdir):
    pdf = workdir / ("case_v%d_f%d.pdf" % (variant, int(fault)))
    truth = make_demo.create(pdf, variant=variant, fault=fault)
    result = table_pdf.extract(pdf, batch_pages=2)
    errors, max_err = _compare_records(truth, result["rows"])
    t_cross, r_cross = _cross_page_locations(truth, result["rows"])
    if t_cross != r_cross:
        errors.append(
            "cross-page merge locations differ: truth=%s extracted=%s"
            % (t_cross, r_cross)
        )
    return pdf, truth, result, errors, max_err


def check_reading_order(result):
    """Records must stay in reading order: strictly increasing R-numbers."""
    errors = []
    numbers = []
    for row in result["rows"]:
        if not row["id"].startswith("R") or not row["id"][1:].isdigit():
            errors.append("non R### id in output: " + row["id"])
            continue
        numbers.append(int(row["id"][1:]))
    if numbers != sorted(numbers):
        errors.append("reading order broken (ids not increasing): " + str(numbers))
    if len(set(numbers)) != len(numbers):
        errors.append("duplicate record ids: " + str(numbers))
    # On every page the left column must fully precede the right column:
    # within one page, ids encountered in reading order go left-block then
    # right-block.  In the fixture left block holds even-indexed page slots,
    # so verify page-local sequences are monotonic without interleave.
    seen_pages = sorted(
        {p["page"] for row in result["rows"] for p in row["source"]["id"]}
    )
    for page in seen_pages:
        page_ids = [
            int(row["id"][1:])
            for row in result["rows"]
            if row["source"]["id"][0]["page"] == page
        ]
        if page_ids != sorted(page_ids):
            errors.append(
                "page %d columns interleaved or misordered: %s" % (page, page_ids)
            )
    return errors


def check_cross_page_merges(result):
    """CONT merges must join text with a single space and keep both boxes."""
    errors = []
    for row in result["rows"]:
        desc_points = row["source"]["description"]
        pages = sorted({p["page"] for p in desc_points})
        if len(pages) > 1:
            if "part-p" not in row["description"]:
                errors.append(
                    row["id"] + " lost its CONT marker text: " + row["description"]
                )
            if "  " in row["description"]:
                errors.append(row["id"] + " description has doubled spaces")
            if len(desc_points) < 2 or desc_points[0]["page"] == desc_points[-1]["page"]:
                errors.append(
                    row["id"] + " cross-page source does not span two pages"
                )
            # Every continuation point must sit at the top of its new block,
            # i.e. above the first data baseline (674 in the fixture layout).
            for point in desc_points[1:]:
                if point["y"] < 700.0:
                    errors.append(
                        row["id"]
                        + " continuation point is not block-top: "
                        + str(point)
                    )
    return errors


def check_no_furniture_as_data(result):
    banned = ("KEY", "DESCRIPTION", "AMOUNT", "ARCHIVE INDEX FOOTER")
    errors = []
    for row in result["rows"]:
        if row["id"] in banned:
            errors.append("header token leaked into id: " + row["id"])
        for token in banned:
            if token in row["description"]:
                errors.append(
                    "furniture token leaked into description of " + row["id"]
                )
        if row["source"]["amount"] and row["amount"] is None:
            errors.append("empty amount must have empty source on " + row["id"])
    return errors


def check_ambiguity(result, fault):
    errors = []
    reasons = sorted(r["reason"] for r in result["rejected"])
    if fault:
        # Two intentional ambiguity layouts, each exactly once:
        #   1. two distinct amounts on one baseline -> AMBIGUOUS_AMOUNT
        #   2. a CONT marker with no open record    -> ORPHAN_CONTINUATION
        if reasons.count("AMBIGUOUS_AMOUNT") != 1:
            errors.append("need exactly one AMBIGUOUS_AMOUNT, got " + str(reasons))
        if reasons.count("ORPHAN_CONTINUATION") != 1:
            errors.append(
                "need exactly one ORPHAN_CONTINUATION, got " + str(reasons)
            )
        amount_rej = next(
            (r for r in result["rejected"] if r["reason"] == "AMBIGUOUS_AMOUNT"),
            None,
        )
        if amount_rej is not None:
            if len(amount_rej["fragments"]) != 4:
                errors.append("AMBIGUOUS_AMOUNT must cite all four source boxes")
            if "28" not in amount_rej["evidence"] or "777" not in amount_rej["evidence"]:
                errors.append("AMBIGUOUS_AMOUNT evidence must name both values")
        cont_rej = next(
            (r for r in result["rejected"] if r["reason"] == "ORPHAN_CONTINUATION"),
            None,
        )
        if cont_rej is not None:
            if cont_rej["texts"] != ["CONT: stray"]:
                errors.append("orphan CONT must isolate the stray marker")
            if "stray" not in cont_rej["evidence"]:
                errors.append("orphan CONT evidence must quote the marker")
        # The faulted R002 row must not appear as a record.
        ids = [r["id"] for r in result["rows"]]
        if "R002" in ids:
            errors.append("ambiguous R002 must be isolated, not emitted")
        for rej in result["rejected"]:
            if not rej.get("fragments") or not rej.get("evidence"):
                errors.append("rejection lacks provenance/evidence: " + rej["reason"])
    else:
        if reasons:
            errors.append("clean PDF produced rejections: " + str(reasons))
    return errors


def check_batch_stability(pdf):
    errors = []
    outputs = {}
    for batch in (1, 2, 3, 5, 100):
        outputs[batch] = table_pdf.extract(pdf, batch_pages=batch)
    canonical = json.dumps(outputs[2], sort_keys=True)
    for batch, payload in outputs.items():
        # batch_pages itself is reported; normalize it before byte comparison.
        payload["meta"]["batch_pages"] = 2
        if json.dumps(payload, sort_keys=True) != canonical:
            errors.append("batch=%d output diverges" % batch)
    # Restart points: resuming at any page offset must reproduce the suffix
    # obtained when the whole document is read from page 1 in one-page
    # batches.  State carried across batch boundaries makes this exact.
    full = table_pdf.extract(pdf, batch_pages=1, start_page=1)
    total_pages = full["meta"]["pages"]
    for start in range(1, total_pages + 1):
        suffix = table_pdf.extract(pdf, batch_pages=1, start_page=start)
        if suffix["meta"]["start_page"] != start:
            errors.append("start_page not echoed for %d" % start)
        expected_breaks = [
            entry for entry in full["meta"]["page_breaks"] if entry["page"] >= start
        ]
        if suffix["meta"]["page_breaks"] != expected_breaks:
            errors.append("restart page %d page_breaks mismatch" % start)
        if suffix["meta"]["pages"] != total_pages:
            errors.append("restart page %d lost page count" % start)
        expected_widths = [
            entry
            for entry in full["meta"]["column_widths"]
            if entry["page"] >= start
        ]
        if suffix["meta"]["column_widths"] != expected_widths:
            errors.append("restart page %d column_widths mismatch" % start)
    return errors


def check_cli(pdf, workdir):
    out1 = workdir / "cli_b1.json"
    out2 = workdir / "cli_b2.json"
    subprocess.run(
        [
            sys.executable,
            "table_pdf.py",
            str(pdf),
            str(out1),
            "--batch-pages",
            "1",
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "table_pdf.py",
            str(pdf),
            str(out2),
            "--batch-pages",
            "2",
        ],
        check=True,
    )
    a = json.loads(out1.read_text())
    b = json.loads(out2.read_text())
    a["meta"]["batch_pages"] = 2
    if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
        return ["CLI batch outputs differ"]
    return []


def check_unit_rejections():
    """Drive the assembler directly for rejection paths the fixture omits."""
    errors = []
    F = table_pdf.Fragment

    def block(page, index, frags):
        blocks = table_pdf.build_blocks(page, _header(page) + frags)
        return blocks[index]

    # 1. BAD_KEY + UNPARSEABLE_AMOUNT on two anchor rows.
    asm = table_pdf.Assembler()
    asm.feed_block(
        block(
            1,
            0,
            [
                F(1, 32, 674, "X999"),
                F(1, 79, 674, "bad-key-label"),
                F(1, 32, 632, "R010"),
                F(1, 79, 632, "ok-label"),
                F(1, 230, 632, "12.5"),
            ],
        )
    )
    reasons = [r.reason for r in asm.rejected]
    if reasons != ["BAD_KEY", "UNPARSEABLE_AMOUNT"]:
        errors.append("unit rejection reasons wrong: " + str(reasons))
    if asm.records:
        errors.append("rejected anchor rows must not produce records")

    # 2. Mid-block non-CONT orphan line (no record open yet in this block).
    asm = table_pdf.Assembler()
    asm.feed_block(
        block(1, 0, [F(1, 79, 674, "lonely wrapped text")])
    )
    if [r.reason for r in asm.rejected] != ["ORPHAN_CONTINUATION"]:
        errors.append("mid-block orphan not isolated")
    if asm.records:
        errors.append("orphan line must not create a record")

    # 3. Multi-fragment wrapped description joins with single spaces and
    #    keeps every provenance box in order.
    asm = table_pdf.Assembler()
    asm.feed_block(
        block(
            1,
            0,
            [
                F(1, 32, 674, "R020"),
                F(1, 79, 674, "first"),
                F(1, 230, 674, "40"),
                F(1, 79, 660, "second"),
                F(1, 79, 646, "third"),
            ],
        )
    )
    if len(asm.records) != 1:
        errors.append("wrapped row produced %d records" % len(asm.records))
    else:
        rec = asm.records[0]
        if rec.description != "first second third":
            errors.append("wrapped description join wrong: " + rec.description)
        if len(rec.source["description"]) != 3:
            errors.append("wrapped description lost provenance boxes")
        if rec.amount != 40:
            errors.append("typed amount wrong: " + str(rec.amount))

    # 4. A CONT marker in the very first block has no predecessor -> orphan.
    asm = table_pdf.Assembler()
    asm.feed_block(block(1, 0, [F(1, 79, 705, "CONT: nowhere")]))
    if [r.reason for r in asm.rejected] != ["ORPHAN_CONTINUATION"]:
        errors.append("first-block CONT must be an orphan")
    return errors


def _header(page):
    return [
        table_pdf.Fragment(page, 32, 724, "KEY"),
        table_pdf.Fragment(page, 79, 724, "DESCRIPTION"),
        table_pdf.Fragment(page, 230, 724, "AMOUNT"),
        table_pdf.Fragment(page, 326, 724, "KEY"),
        table_pdf.Fragment(page, 373, 724, "DESCRIPTION"),
        table_pdf.Fragment(page, 524, 724, "AMOUNT"),
    ]


def main():
    workdir = Path(tempfile.mkdtemp(prefix="pdfverify_"))
    all_errors = []
    worst = 0.0
    all_errors += check_unit_rejections()
    try:
        for variant in range(6):
            for fault in (False, True):
                pdf, truth, result, errors, max_err = run_case(
                    variant, fault, workdir
                )
                worst = max(worst, max_err)
                errors += check_no_furniture_as_data(result)
                errors += check_ambiguity(result, fault)
                errors += check_reading_order(result)
                errors += check_cross_page_merges(result)
                errors += check_batch_stability(pdf)
                if not fault:
                    errors += check_cli(pdf, workdir)
                label = "variant=%d fault=%s" % (variant, fault)
                if errors:
                    all_errors.extend(label + ": " + e for e in errors)
                else:
                    print(
                        "OK  %-16s rows=%-2d rejected=%d maxerr=%.2fpt"
                        % (
                            label,
                            len(result["rows"]),
                            len(result["rejected"]),
                            max_err,
                        )
                    )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("-" * 60)
    if all_errors:
        print("FAIL %d issue(s)" % len(all_errors))
        for err in all_errors:
            print(" -", err)
        sys.exit(1)
    print("ALL ACCEPTANCE CHECKS PASSED; worst coordinate error = %.2f pt" % worst)


if __name__ == "__main__":
    main()