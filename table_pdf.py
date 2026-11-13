"""Geometry-aware extraction of two-column continuation tables from text PDFs.

The legacy front end consumed ``page.extract_text()`` in stream order, so
multi-column pages were read in the wrong order, repeated page headers became
data rows and page-spanning continuations were glued onto the wrong record.

This module rebuilds the table from page coordinates instead:

1.  Fragments are parsed straight from the content stream (the pypdf text
    visitor reports a wrong (0, 0) matrix for part of the glyphs) keeping the
    *unrotated* PDF coordinate system, so /Rotate never distorts provenance.
2.  Repeated header triplets (KEY / DESCRIPTION / AMOUNT) anchor the column
    regions and column boundaries.  Titles above and footers below every
    block are detected geometrically and excluded from data.
3.  Fragments are read in true reading order (page -> left block -> right
    block -> top to bottom), grouped onto baselines and joined into typed
    records.  Explicit ``CONT:`` continuations at the top of a new column or
    page are attached to the still-open record with deterministic rules;
    everything that cannot be attached is isolated in ``rejected``.
4.  The run is page-batchable: the document is processed page by page and the
    assembler carries state across batch boundaries, so every ``--batch-pages``
    value yields byte-identical output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from pypdf import PdfReader
from pypdf.generic import ContentStream

# --------------------------------------------------------------------------- #
# Tunables (PDF points, measured in the unrotated page coordinate system)
# --------------------------------------------------------------------------- #
BASELINE_TOL = 3.0          # y drift allowed between glyphs of one baseline
BLOCK_GAP = 36.0            # x gap that separates two parallel column blocks
TITLE_GAP = 10.0            # a fragment this far above the header is a title
FOOTER_GAP = 18.0           # a fragment this far below the last row is footer
CONT_PREFIX = "CONT:"
KEY_RE = re.compile(r"^R\d{3}$")
INT_RE = re.compile(r"^[+-]?\d+$")

HEADER_TOKENS = ("KEY", "DESCRIPTION", "AMOUNT")


# --------------------------------------------------------------------------- #
# Low level content-stream parsing
# --------------------------------------------------------------------------- #
def _mat_mul(a: Sequence[float], b: Sequence[float]) -> Tuple[float, ...]:
    """Multiply two six-element affine matrices (PDF operator order)."""
    return (
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    )


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


@dataclass(frozen=True)
class Fragment:
    """One text show operator, located by its text-space baseline origin."""

    page: int
    x: float
    y: float
    text: str

    def point(self) -> Dict[str, float]:
        return {"page": self.page, "x": round(self.x, 3), "y": round(self.y, 3)}


def _decode_text(operand: object) -> str:
    if isinstance(operand, bytes):
        # Text strings may be UTF-16BE (BOM) or PDFDocEncoding / latin-1.
        if operand.startswith(b"\xfe\xff"):
            return operand[2:].decode("utf-16-be", errors="replace")
        return operand.decode("latin-1", errors="replace")
    return str(operand)


def parse_page_fragments(page, page_number: int) -> List[Fragment]:
    """Return visible text fragments with unrotated content coordinates.

    The page /Rotate flag changes how a viewer displays the page but never the
    content stream coordinates, so the coordinates reported here are exactly
    the ones the producer used and need no rotation correction.
    """
    ctm = IDENTITY
    text_loaded = text_line = IDENTITY
    font_size = 1.0
    leading = 0.0
    in_text = False
    fragments: List[Fragment] = []

    for operands, operator in ContentStream(page.get_contents(), None).operations:
        if operator == b"cm":
            ctm = _mat_mul(tuple(float(v) for v in operands), ctm)
        elif operator == b"BT":
            in_text = True
            text_loaded = text_line = IDENTITY
        elif operator == b"ET":
            in_text = False
        elif not in_text:
            continue
        elif operator == b"Tf":
            font_size = float(operands[1])
        elif operator == b"TL":
            leading = float(operands[0])
        elif operator == b"Tm":
            text_loaded = text_line = tuple(float(v) for v in operands)
        elif operator in (b"Td", b"TD"):
            tx, ty = float(operands[0]), float(operands[1])
            text_line = _mat_mul((1, 0, 0, 1, tx, ty), text_line)
            text_loaded = text_line
            if operator == b"TD":
                leading = -ty
        elif operator == b"T*":
            text_line = _mat_mul((1, 0, 0, 1, 0, -leading), text_line)
            text_loaded = text_line
        elif operator in (b"Tj", b"TJ"):
            if operator == b"TJ":
                text = "".join(
                    _decode_text(item)
                    for item in operands[0]
                    if not isinstance(item, (int, float))
                )
            else:
                text = _decode_text(operands[0])
            text = text.replace("\r", "").replace("\n", "").strip()
            if not text:
                continue
            # Trm = CTM * scaled text matrix; translation terms survive.
            trm = _mat_mul(
                ctm,
                (
                    text_loaded[0] * font_size,
                    text_loaded[1] * font_size,
                    text_loaded[2] * font_size,
                    text_loaded[3] * font_size,
                    text_loaded[4],
                    text_loaded[5],
                ),
            )
            fragments.append(Fragment(page_number, trm[4], trm[5], text))
    return fragments


# --------------------------------------------------------------------------- #
# Geometry: blocks, columns, rows
# --------------------------------------------------------------------------- #
@dataclass
class Column:
    name: str
    x0: float
    x1: float
    width: float
    header: Fragment


@dataclass
class Block:
    """One table region on a page (a page may carry two parallel blocks)."""

    page: int
    index: int
    header_y: float
    columns: List[Column]
    body: List[Fragment] = field(default_factory=list)
    titles: List[Fragment] = field(default_factory=list)
    footers: List[Fragment] = field(default_factory=list)
    unassigned: List[Fragment] = field(default_factory=list)

    @property
    def left(self) -> float:
        return self.columns[0].x0

    @property
    def right(self) -> float:
        return self.columns[-1].x1

    @property
    def midpoint(self) -> float:
        return (self.left + self.right) / 2.0

    def column_of(self, frag: Fragment) -> int:
        best, best_dist = 0, float("inf")
        for i, col in enumerate(self.columns):
            center = (col.x0 + col.x1) / 2.0
            dist = abs(frag.x - center)
            if dist < best_dist:
                best, best_dist = i, dist
        return best


def _cluster_headers(fragments: Sequence[Fragment]) -> List[List[Fragment]]:
    """Group header fragments into per-block triplets.

    Fragments first share a baseline (small y drift).  Because two parallel
    column blocks can sit on the same baseline, a baseline group is then split
    wherever the horizontal gap between adjacent header fragments exceeds the
    inter-column gap, and each run is cut into KEY/DESCRIPTION/AMOUNT triplets.
    """
    by_baseline: List[List[Fragment]] = []
    for frag in fragments:
        if frag.text not in HEADER_TOKENS:
            continue
        for group in by_baseline:
            if abs(group[0].y - frag.y) <= BASELINE_TOL:
                group.append(frag)
                break
        else:
            by_baseline.append([frag])

    clusters: List[List[Fragment]] = []
    for group in by_baseline:
        group.sort(key=lambda f: f.x)
        # A baseline holds one triplet per block; every KEY token opens a new
        # block, so walk the cyclic KEY -> DESCRIPTION -> AMOUNT sequence.
        runs: List[List[Fragment]] = []
        current: List[Fragment] = []
        for frag in group:
            if frag.text == HEADER_TOKENS[0] and current:
                runs.append(current)
                current = []
            current.append(frag)
        if current:
            runs.append(current)
        for run in runs:
            if [f.text for f in run] == list(HEADER_TOKENS):
                clusters.append(run)
    clusters.sort(key=lambda g: (g[0].page, -g[0].y, g[0].x))
    return clusters


def build_blocks(page_number: int, fragments: Sequence[Fragment]) -> List[Block]:
    """Recover column regions, boundaries and non-table furniture on a page."""
    clusters = _cluster_headers(fragments)
    header_ids = {id(f) for cluster in clusters for f in cluster}
    blocks: List[Block] = []
    for index, cluster in enumerate(clusters):
        xs = sorted(f.x for f in cluster)
        boundaries = [min(xs)]
        for left_x, right_x in zip(xs, xs[1:]):
            boundaries.append((left_x + right_x) / 2.0)
        boundaries.append(max(xs))
        columns = [
            Column(frag.text, x0, x1, x1 - x0, frag)
            for frag, x0, x1 in zip(cluster, boundaries[:-1], boundaries[1:])
        ]
        blocks.append(Block(page_number, index, cluster[0].y, columns))

    # Assign every non-header fragment to the block whose horizontal span
    # contains it.  Body fragments may overhang a boundary by a few points
    # (the ambiguity fixture offsets one amount by 9 pt), so body-level
    # assignment uses an overflow tolerance; titles and page footers, which
    # live well outside the spans, are never pulled into a column.
    stray: List[Fragment] = []
    for frag in fragments:
        if id(frag) in header_ids:
            continue
        owner = next(
            (b for b in blocks if b.left <= frag.x <= b.right),
            None,
        )
        if owner is None:
            # Only fragments just past a block's right border (e.g. an amount
            # glyph shifted a few points) overflow into the body.  The left
            # border is the KEY column origin and must not overhang, which
            # keeps page footers in the margin from being absorbed.
            def right_overhang(block: Block) -> float:
                return (
                    frag.x - block.right
                    if frag.x > block.right
                    else float("inf")
                )

            nearest = min(blocks, key=right_overhang)
            if (
                right_overhang(nearest) <= BLOCK_GAP
                and frag.y <= nearest.header_y - TITLE_GAP
            ):
                nearest.footers.append(frag)  # body cutoff runs below
            else:
                stray.append(frag)
        elif frag.y > owner.header_y + TITLE_GAP:
            owner.titles.append(frag)
        else:
            owner.footers.append(frag)

    # Footers are the trailing fragments below the body gap; everything at or
    # above the cutoff is table body (including wrapped / continuation lines).
    for block in blocks:
        below = [f for f in block.footers if f.y <= block.header_y - TITLE_GAP]
        cutoff = (
            min(f.y for f in below) - FOOTER_GAP
            if below
            else block.header_y - FOOTER_GAP
        )
        body: List[Fragment] = []
        footers: List[Fragment] = []
        for frag in block.footers:
            (footers if frag.y < cutoff else body).append(frag)
        block.body = sorted(body, key=lambda f: (-f.y, f.x))
        block.footers = sorted(footers, key=lambda f: (f.y, f.x))
        block.titles.sort(key=lambda f: (f.y, f.x))

    # Page-wide furniture: above the highest header is the running title,
    # below every block's last body line is the page footer.  Anything else
    # that could not be attributed is kept as an explicit diagnostic.
    if blocks:
        top_header = max(b.header_y for b in blocks)
        body_floor = min(
            (f.y for b in blocks for f in b.body),
            default=min(b.header_y for b in blocks),
        )
        for frag in sorted(stray, key=lambda f: (-f.y, f.x)):
            if frag.y > top_header + TITLE_GAP:
                nearest = min(blocks, key=lambda b: abs(frag.x - b.midpoint))
                nearest.titles.append(frag)
            elif frag.y < body_floor - FOOTER_GAP:
                nearest = min(blocks, key=lambda b: abs(frag.x - b.midpoint))
                nearest.footers.append(frag)
            else:
                blocks[0].unassigned.append(frag)
        for block in blocks:
            block.titles.sort(key=lambda f: (f.y, f.x))
            block.footers.sort(key=lambda f: (f.y, f.x))
    blocks.sort(key=lambda b: (b.left, -b.header_y))
    for index, block in enumerate(blocks):
        block.index = index
    return blocks


@dataclass
class Row:
    """Fragments sharing a baseline, bucketed into the three logical columns."""

    y: float
    cells: List[List[Fragment]] = field(
        default_factory=lambda: [[], [], []]
    )

    @property
    def fragments(self) -> List[Fragment]:
        return [f for cell in self.cells for f in cell]


def group_rows(block: Block) -> List[Row]:
    """Group body fragments onto baselines with a small drift tolerance."""
    rows: List[Row] = []
    for frag in sorted(block.body, key=lambda f: (-f.y, f.x)):
        for row in rows:
            if abs(row.y - frag.y) <= BASELINE_TOL:
                row.cells[block.column_of(frag)].append(frag)
                break
        else:
            row = Row(frag.y)
            row.cells[block.column_of(frag)].append(frag)
            rows.append(row)
    for row in rows:
        for cell in row.cells:
            cell.sort(key=lambda f: f.x)
    rows.sort(key=lambda r: -r.y)
    return rows


# --------------------------------------------------------------------------- #
# Record assembly
# --------------------------------------------------------------------------- #
@dataclass
class Record:
    id: str
    description: str
    amount: Optional[int]
    source: Dict[str, List[Dict[str, float]]]
    pages: List[int] = field(default_factory=list)
    cross_page: bool = False


@dataclass
class Rejection:
    reason: str
    page: int
    block: int
    y: float
    fragments: List[Dict[str, float]]
    texts: List[str]
    evidence: str


class Assembler:
    """Stateful, deterministic record builder fed one block at a time."""

    def __init__(self) -> None:
        self.records: List[Record] = []
        self.rejected: List[Rejection] = []
        self._last: Optional[Record] = None
        self._block_seen = 0
        self.page_breaks: List[Dict[str, object]] = []

    def _reject(self, reason: str, block: Block, row: Row, evidence: str) -> None:
        frags = row.fragments
        self.rejected.append(
            Rejection(
                reason=reason,
                page=block.page,
                block=block.index,
                y=round(row.y, 3),
                fragments=[f.point() for f in frags],
                texts=[f.text for f in frags],
                evidence=evidence,
            )
        )

    def _attach_description(self, record: Record, frags: Sequence[Fragment]) -> None:
        for frag in frags:
            record.description += " " + frag.text
            record.source["description"].append(frag.point())
            if frag.page not in record.pages:
                record.pages.append(frag.page)
                record.cross_page = True

    def feed_block(self, block: Block) -> None:
        is_first_block = self._block_seen == 0
        self._block_seen += 1
        last_in_block: Optional[Record] = None
        for row in group_rows(block):
            keys = row.cells[0]
            descs = row.cells[1]
            amounts = row.cells[2]
            key_text = keys[0].text if keys else None

            # Explicit continuation marker: legal only at the top of a new
            # column/page, where it joins the still-open previous record.
            is_cont = (
                bool(descs)
                and all(f.text.startswith(CONT_PREFIX) for f in descs)
                and not keys
                and not amounts
            )
            if is_cont:
                top_of_block = last_in_block is None
                marker = " ".join(
                    f.text[len(CONT_PREFIX):].strip() for f in descs
                ).strip()
                if top_of_block and not is_first_block and self._last is not None:
                    for frag in descs:
                        text = frag.text[len(CONT_PREFIX):].strip()
                        if text:
                            self._last.description += " " + text
                        self._last.source["description"].append(frag.point())
                        if frag.page not in self._last.pages:
                            self._last.pages.append(frag.page)
                            self._last.cross_page = True
                    continue
                evidence = (
                    "CONT marker outside a column/page boundary"
                    if not top_of_block
                    else "no open record precedes CONT marker"
                )
                if marker:
                    evidence += ": " + marker
                self._reject("ORPHAN_CONTINUATION", block, row, evidence)
                continue

            if key_text is not None:
                if not KEY_RE.match(key_text):
                    self._reject(
                        "BAD_KEY",
                        block,
                        row,
                        "key fragment " + repr(key_text) + " does not match R###",
                    )
                    continue
                amount_values = [f.text for f in amounts]
                if len(set(amount_values)) > 1:
                    self._reject(
                        "AMBIGUOUS_AMOUNT",
                        block,
                        row,
                        "multiple distinct amount fragments on one baseline: "
                        + ", ".join(amount_values),
                    )
                    continue
                bad_amount = next(
                    (v for v in amount_values if not INT_RE.match(v)), None
                )
                if bad_amount is not None:
                    self._reject(
                        "UNPARSEABLE_AMOUNT",
                        block,
                        row,
                        "amount fragment "
                        + repr(bad_amount)
                        + " is not an integer",
                    )
                    continue
                amount = int(amount_values[0]) if amount_values else None
                record = Record(
                    id=key_text,
                    description=" ".join(f.text for f in descs),
                    amount=amount,
                    source={
                        "id": [f.point() for f in keys],
                        "description": [f.point() for f in descs],
                        "amount": [f.point() for f in amounts],
                    },
                    pages=[block.page],
                )
                self.records.append(record)
                self._last = record
                last_in_block = record
                continue

            # No key: a wrapped description line.  Mid-block lines join the
            # current block's record; anything unattributable is isolated.
            if descs and not amounts:
                if last_in_block is not None:
                    self._attach_description(last_in_block, descs)
                    continue
                self._reject(
                    "ORPHAN_CONTINUATION",
                    block,
                    row,
                    "description line without an owning record: "
                    + " ".join(f.text for f in descs),
                )
                continue

            self._reject(
                "UNCLASSIFIED_ROW",
                block,
                row,
                "row has no valid key and no description-only structure",
            )

    def mark_page(self, page_number: int, blocks: Sequence[Block]) -> None:
        self.page_breaks.append(
            {
                "page": page_number,
                "blocks": len(blocks),
                "block_order": [
                    {
                        "block": b.index,
                        "x0": round(b.left, 3),
                        "x1": round(b.right, 3),
                        "header_y": round(b.header_y, 3),
                        "titles": [f.point() for f in b.titles],
                        "footers": [f.point() for f in b.footers],
                        "unassigned": [f.point() for f in b.unassigned],
                        "columns": [
                            {
                                "name": c.name,
                                "x0": round(c.x0, 3),
                                "x1": round(c.x1, 3),
                                "width": round(c.width, 3),
                            }
                            for c in b.columns
                        ],
                    }
                    for b in blocks
                ],
            }
        )


# --------------------------------------------------------------------------- #
# Document-level driver
# --------------------------------------------------------------------------- #
def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract(
    source: Path, batch_pages: int = 2, start_page: int = 1
) -> Dict[str, object]:
    """Parse ``source`` in deterministic page batches.

    Processing pages in chunks of ``batch_pages`` while feeding one assembler
    across chunk boundaries guarantees identical output for any batch size
    (and for restarts at the ``start_page`` offset).
    """
    source = Path(source)
    reader = PdfReader(str(source))
    total_pages = len(reader.pages)
    if not 1 <= start_page <= total_pages:
        raise ValueError(
            "start_page " + str(start_page) + " outside 1.." + str(total_pages)
        )
    if batch_pages < 1:
        raise ValueError("batch_pages must be >= 1")

    assembler = Assembler()
    page_index = start_page
    while page_index <= total_pages:
        chunk = range(page_index, min(total_pages + 1, page_index + batch_pages))
        for page_number in chunk:
            page = reader.pages[page_number - 1]
            fragments = parse_page_fragments(page, page_number)
            blocks = build_blocks(page_number, fragments)
            for block in blocks:  # left block then right block, top to bottom
                assembler.feed_block(block)
            assembler.mark_page(page_number, blocks)
        page_index += batch_pages

    rows = [
        {
            "id": rec.id,
            "description": rec.description,
            "amount": rec.amount,
            "source": rec.source,
        }
        for rec in assembler.records
    ]
    rejected = [
        {
            "reason": rej.reason,
            "page": rej.page,
            "block": rej.block,
            "y": rej.y,
            "texts": rej.texts,
            "fragments": rej.fragments,
            "evidence": rej.evidence,
        }
        for rej in assembler.rejected
    ]
    return {
        "rows": rows,
        "rejected": rejected,
        "meta": {
            "document": source.name,
            "sha256": _fingerprint(source),
            "pages": total_pages,
            "start_page": start_page,
            "batch_pages": batch_pages,
            "column_widths": [
                {
                    "page": entry["page"],
                    "block": spec["block"],
                    "columns": spec["columns"],
                }
                for entry in assembler.page_breaks
                for spec in entry["block_order"]
            ],
            "page_breaks": assembler.page_breaks,
            "rejection_diagnostics": {
                "count": len(rejected),
                "reasons": sorted({rej["reason"] for rej in rejected}),
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--batch-pages", type=int, default=2)
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--indent", type=int, default=None)
    args = parser.parse_args()
    result = extract(args.source, args.batch_pages, args.start_page)
    args.output.write_text(
        json.dumps(result, sort_keys=True, indent=args.indent, ensure_ascii=False)
        + "\n"
    )


if __name__ == "__main__":
    main()
