#!/usr/bin/env python3
"""Layered change classification and refetch/archive decisions.

Layers
------
===================  =======================================================
layer                compared fields
===================  =======================================================
struct               fp_struct  (DOM outline + non-content block skeleton)
chrome               fp_chrome  (nav/sidebar/footer text + static template
                                  text)
template             fp_template (commitment over struct + chrome)
content              fp_content (normalized text of the located region)
===================  =======================================================

"Template changed" means the template commitment (struct + chrome) moved.
A redesign that only reshapes markup moves the template layer; it can never
move the content layer, because that layer is computed from normalized prose
only.

Class -> decision
-----------------
none      no refetch, no archive
template  no refetch, archive the template layer with its evidence
content   refetch, archive the content layer
mixed     one refetch, archive both layers

Every decision carries per-layer evidence and a confidence built from
(a) how reliable the region location was and (b) how well a block-level diff
corroborates the layer verdict.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from fingerprints import (
    PageModel,
    block_kind,
    digest_records,
    fingerprint,
    norm_content_text,
    norm_text,
)

SCHEMA = "layered-decision/1"

CLASSES = ("none", "template", "content", "mixed")

# A content verdict is only trusted when the located region really is body
# text: below this share of page text the region location is suspect.
MIN_CONTENT_SHARE = 0.15
# A share swing larger than this is suspicious for an ordinary prose edit.
MAX_SHARE_DELTA = 0.40
# Region confidence floor for an unsupported content verdict.
REGION_CONFIDENCE_FLOOR = 0.85

# Baseline confidence per class when there is no corroborating evidence yet.
# These encode how costly a mistake is per class:
#   * "none"     -- a miss only delays an update.
#   * "template" -- a miss means prose changes are never re-crawled, so
#                   the bar is highest.
#   * "content"  -- over-triggering costs a crawl but loses nothing.
#   * "mixed"    -- same cost profile as "content", slightly harder to prove.
CLASS_WEIGHT = {"none": 0.90, "template": 0.72, "content": 0.80, "mixed": 0.76}


class PageError(ValueError):
    """Raised when a page snapshot cannot be fingerprinted."""


def _fp(page: Any, label: str, chunk_size: Optional[int] = None) -> Dict[str, Any]:
    if not isinstance(page, dict):
        raise PageError("%s: page snapshot must be a JSON object" % label)
    try:
        return fingerprint(page, chunk_size)
    except ValueError as exc:
        raise PageError("%s: %s" % (label, exc))


def _texts(page: Dict[str, Any]) -> Dict[str, str]:
    return {
        norm_text(block.get("id", "")): norm_text(block.get("text", ""))
        for block in page.get("blocks", []) or []
    }


def _block_diff(old: Dict[str, Any], new: Dict[str, Any],
                old_fp: Dict[str, Any], new_fp: Dict[str, Any]) -> Dict[str, Any]:
    old_texts = _texts(old)
    new_texts = _texts(new)
    old_kinds = {norm_text(b.get("id", "")): block_kind(b)
                 for b in old.get("blocks", []) or []}
    new_kinds = {norm_text(b.get("id", "")): block_kind(b)
                 for b in new.get("blocks", []) or []}
    added = sorted(set(new_texts) - set(old_texts))
    removed = sorted(set(old_texts) - set(new_texts))
    changed = sorted(key for key in set(old_texts) & set(new_texts)
                     if old_texts[key] != new_texts[key])
    old_content = {entry["id"] for entry in old_fp["blocks"] if entry["content"]}
    new_content = {entry["id"] for entry in new_fp["blocks"] if entry["content"]}

    def in_content(block: str, side: Dict[str, str], kinds: Dict[str, str],
                   region: Set[str]) -> bool:
        # A block counts as content when the locator put it in the region; an
        # unlabelled block inside the region is prose, not chrome.  Declared
        # kind is the fallback for blocks the locator left outside.
        if block in region:
            return True
        return kinds.get(block) == "content"

    return {
        "blocks_added": added,
        "blocks_removed": removed,
        "blocks_changed": changed,
        "changed_content_blocks": [i for i in changed
                                   if in_content(i, new_texts, new_kinds, new_content)],
        "changed_non_content_blocks": [i for i in changed
                                       if not in_content(i, new_texts, new_kinds, new_content)],
        "added_content_blocks": [i for i in added
                                 if in_content(i, new_texts, new_kinds, new_content)],
        "removed_content_blocks": [i for i in removed
                                   if in_content(i, old_texts, old_kinds, old_content)],
        "added_non_content_blocks": [i for i in added
                                     if not in_content(i, new_texts, new_kinds, new_content)],
        "removed_non_content_blocks": [i for i in removed
                                       if not in_content(i, old_texts, old_kinds, old_content)],
        "old_content_ids": sorted(old_content),
        "new_content_ids": sorted(new_content),
        "old_chars": sum(len(t) for t in old_texts.values()),
        "new_chars": sum(len(t) for t in new_texts.values()),
    }


def _digest_content(pieces: Sequence[str]) -> str:
    return digest_records([("content", norm_content_text(" ".join(pieces)))])


def _content_order(page: Dict[str, Any]) -> Dict[str, int]:
    """Map block id -> document-order rank, used to order masked prose."""
    model = PageModel(page)
    ranked = sorted(range(len(model.block_ids)),
                    key=lambda i: (model.order[i], i))
    return {model.block_ids[i]: rank for rank, i in enumerate(ranked)}


def detect_relocation(old: Dict[str, Any], new: Dict[str, Any],
                      old_fp: Dict[str, Any], new_fp: Dict[str, Any]) -> Dict[str, Any]:
    """Separate prose edits from content that only moved between blocks.

    Content texts present on both sides are masked out; if the masked content
    digests match, the two snapshots carry the same prose and only its
    placement moved, which is a structural signal rather than a prose edit.
    """
    old_texts = _texts(old)
    new_texts = _texts(new)
    old_region = {entry["id"] for entry in old_fp["blocks"] if entry["content"]}
    new_region = {entry["id"] for entry in new_fp["blocks"] if entry["content"]}
    old_order = _content_order(old)
    new_order = _content_order(new)

    # A text only counts as relocated when the block that carries it actually
    # changed identity or place relative to its neighbours; a block that sits
    # exactly where it did before is simply unchanged.
    shared: Set[str] = set()
    for text in set(old_texts.values()) & set(new_texts.values()):
        if len(text) < 8:
            continue
        old_ids = sorted(b for b, t in old_texts.items() if t == text and b in old_region)
        new_ids = sorted(b for b, t in new_texts.items() if t == text and b in new_region)
        if not old_ids or not new_ids:
            continue
        if old_ids != new_ids:
            shared.add(text)
            continue
        # Same block ids: relocated only if its reading-order rank moved.
        if (old_order.get(old_ids[0], 10 ** 9) != new_order.get(new_ids[0], 10 ** 9)):
            shared.add(text)

    old_drop = {bid for bid, t in old_texts.items() if t in shared}
    new_drop = {bid for bid, t in new_texts.items() if t in shared}
    old_kept = sorted(old_region - old_drop, key=lambda b: old_order.get(b, 10 ** 9))
    new_kept = sorted(new_region - new_drop, key=lambda b: new_order.get(b, 10 ** 9))
    old_pieces = [old_texts[b] for b in old_kept if old_texts[b]]
    new_pieces = [new_texts[b] for b in new_kept if new_texts[b]]
    old_digest = _digest_content(old_pieces) if old_pieces else None
    new_digest = _digest_content(new_pieces) if new_pieces else None

    # The prose left after masking must match *in order*.  Then check whether
    # the masked-out texts kept their relative order too:
    #   * same order  -> the prose merely moved between blocks (structural)
    #   * order differs -> the article reads differently (a content change)
    old_shared_seq = [old_texts[b] for b in sorted(
        (b for b in old_region if old_texts.get(b) in shared),
        key=lambda b: old_order.get(b, 10 ** 9))]
    new_shared_seq = [new_texts[b] for b in sorted(
        (b for b in new_region if new_texts.get(b) in shared),
        key=lambda b: new_order.get(b, 10 ** 9))]
    residue_ordered = old_pieces == new_pieces
    order_preserved = old_shared_seq == new_shared_seq
    return {
        "relocated_texts": sorted(shared),
        "relocated_chars": sum(len(t) for t in shared),
        "old_masked_digest": old_digest,
        "new_masked_digest": new_digest,
        "masked_residue_equal": bool(old_digest and old_digest == new_digest and residue_ordered),
        "reading_order_preserved": bool(order_preserved),
        "content_equal_after_masking": bool(
            old_digest and old_digest == new_digest and residue_ordered and order_preserved),
        "prose_reordered": bool(shared and not order_preserved),
    }


def _share(fp: Dict[str, Any]) -> float:
    total = fp["region"].get("total_chars") or 0
    if not total:
        return 0.0
    return fp["region"].get("mass_chars", 0) / float(total)


def compare(old: Dict[str, Any], new: Dict[str, Any],
            chunk_size: Optional[int] = None) -> Dict[str, Any]:
    """Compare two snapshots: layered verdict, evidence, refetch decision."""
    old_fp = _fp(old, "old", chunk_size)
    new_fp = _fp(new, "new", chunk_size)
    if chunk_size:
        # Prove that the feed size cannot move any digest.
        if (fingerprint(old, 1)["digest"] != old_fp["digest"]
                or fingerprint(new, 3)["digest"] != new_fp["digest"]):
            raise AssertionError("fingerprint changed with a different chunk feed")

    layers = {
        "struct": old_fp["fp_struct"] != new_fp["fp_struct"],
        "chrome": old_fp["fp_chrome"] != new_fp["fp_chrome"],
        "template": old_fp["fp_template"] != new_fp["fp_template"],
        "content": old_fp["fp_content"] != new_fp["fp_content"],
    }
    notes: List[str] = []
    block_diff = _block_diff(old, new, old_fp, new_fp)
    relocation = detect_relocation(old, new, old_fp, new_fp)

    template_changed = layers["template"]
    content_changed = layers["content"]

    # Guard 1: a content digest moved but no prose block changed -- the region
    # itself moved in the template (a block re-identified, or a region that
    # normalizes to identical text).  The prose is the same, so this is a
    # structural signal.
    if content_changed and relocation["content_equal_after_masking"]:
        content_changed = False
        notes.append("content equal after masking relocated blocks; treated as "
                     "a structural move")

    # Guard 2: normalized page prose is byte-identical, so no prose can have
    # changed.  Guards against a block re-identification the region-level
    # comparison could not see.
    if content_changed and old_fp["page_text"] == new_fp["page_text"]:
        content_changed = False
        notes.append("normalized page text identical; the content digest moved "
                     "only through regrouping")

    # Guard 3: unsupported content verdict on a badly located region.
    region_confidence = min(old_fp["region"]["confidence"], new_fp["region"]["confidence"])
    old_share, new_share = _share(old_fp), _share(new_fp)
    share_delta = abs(new_share - old_share)
    uncertain_region = (old_fp["region"]["ambiguous"] or new_fp["region"]["ambiguous"]
                        or region_confidence < REGION_CONFIDENCE_FLOOR)
    if (content_changed and uncertain_region
            and (min(old_share, new_share) < MIN_CONTENT_SHARE
                 or share_delta > MAX_SHARE_DELTA)):
        content_changed = False
        notes.append("content verdict downgraded: region confidence %.2f, share "
                     "%.3f -> %.3f" % (region_confidence, old_share, new_share))

    if template_changed and not layers["struct"] and not layers["chrome"]:
        template_changed = False
        notes.append("template digest differs without struct or chrome movement")

    if template_changed and content_changed:
        klass = "mixed"
    elif content_changed:
        klass = "content"
    elif template_changed:
        klass = "template"
    else:
        klass = "none"

    confidence, drivers = _confidence(
        klass, layers, block_diff, relocation, region_confidence, share_delta, notes)
    layer_confidence = _layer_confidence(
        layers, block_diff, relocation, region_confidence)
    decision = _decision(klass, layers)

    evidence = {
        "layers": {
            "struct": {"changed": layers["struct"], "old": old_fp["fp_struct"],
                       "new": new_fp["fp_struct"]},
            "chrome": {"changed": layers["chrome"], "old": old_fp["fp_chrome"],
                       "new": new_fp["fp_chrome"]},
            "template": {"changed": layers["template"],
                         "old": old_fp["fp_template"], "new": new_fp["fp_template"]},
            "content": {"changed": layers["content"], "old": old_fp["fp_content"],
                        "new": new_fp["fp_content"]},
        },
        "template_layers": {
            "struct_changed": layers["struct"],
            "chrome_changed": layers["chrome"],
            "static_text_added": sorted(set(new_fp["static_texts"]) - set(old_fp["static_texts"])),
            "static_text_removed": sorted(set(old_fp["static_texts"]) - set(new_fp["static_texts"])),
        },
        "content_region": {
            "old": old_fp["region"],
            "new": new_fp["region"],
            "confidence": round(region_confidence, 4),
            "old_share": round(old_share, 4),
            "new_share": round(new_share, 4),
            "share_delta": round(share_delta, 4),
            "uncertain": bool(uncertain_region),
        },
        "layer_confidence": layer_confidence,
        "blocks": block_diff,
        "relocation": relocation,
        "content_preview": {"old": old_fp["content_text"][:160],
                            "new": new_fp["content_text"][:160]},
        "notes": notes,
    }

    return {
        "schema": SCHEMA,
        "class": klass,
        "refetch": decision["refetch"],
        "archive": decision["archive"],
        "archive_layers": decision["archive_layers"],
        "reason": decision["reason"],
        "confidence": confidence,
        "confidence_drivers": drivers,
        "evidence": evidence,
        "fingerprints": {
            "old": {key: old_fp[key] for key in
                    ("fp_struct", "fp_chrome", "fp_template", "fp_content")},
            "new": {key: new_fp[key] for key in
                    ("fp_struct", "fp_chrome", "fp_template", "fp_content")},
        },
        "fp_struct": new_fp["fp_struct"],
        "fp_chrome": new_fp["fp_chrome"],
        "fp_template": new_fp["fp_template"],
        "fp_content": new_fp["fp_content"],
        "digest": new_fp["digest"],
        "old_digest": old_fp["digest"],
    }


def _confidence(klass: str, layers: Dict[str, bool], block_diff: Dict[str, Any],
                relocation: Dict[str, Any], region_confidence: float,
                share_delta: float, notes: Sequence[str]) -> Tuple[float, Dict[str, Any]]:
    base = CLASS_WEIGHT[klass]
    drivers: Dict[str, Any] = {"base": base}
    support = 0.0

    if klass == "content":
        corroborating = (len(block_diff["changed_content_blocks"])
                         + len(block_diff["added_content_blocks"])
                         + len(block_diff["removed_content_blocks"]))
        if corroborating:
            support += 0.10
        if not layers["struct"]:
            support += 0.06
        drivers["content_block_edits"] = corroborating
        value = min(0.99, base + support) * (0.90 + 0.10 * region_confidence)
    elif klass == "template":
        if layers["struct"]:
            support += 0.06
        if layers["chrome"]:
            support += 0.04
        if relocation["content_equal_after_masking"]:
            support += 0.06
        if (block_diff["changed_non_content_blocks"]
                or block_diff["added_non_content_blocks"]
                or block_diff["removed_non_content_blocks"]):
            support += 0.04
        drivers["non_content_block_edits"] = (
            len(block_diff["changed_non_content_blocks"])
            + len(block_diff["added_non_content_blocks"])
            + len(block_diff["removed_non_content_blocks"]))
        value = min(0.99, base + support) * (0.92 + 0.08 * region_confidence)
    elif klass == "mixed":
        corroborating = (len(block_diff["changed_content_blocks"])
                         + len(block_diff["added_content_blocks"]))
        if corroborating:
            support += 0.10
        if layers["struct"]:
            support += 0.06
        drivers["content_block_edits"] = corroborating
        value = min(0.99, base + support) * (0.90 + 0.10 * region_confidence)
    else:
        agreeing = sum(1 for key in ("struct", "chrome", "content") if not layers[key])
        if agreeing == 3:
            support += 0.08
        drivers["agreeing_views"] = agreeing
        value = min(0.99, base + support) * (0.94 + 0.06 * region_confidence)
        if agreeing < 3:
            value *= 0.92

    if region_confidence < REGION_CONFIDENCE_FLOOR:
        value *= 0.96
    if share_delta > MAX_SHARE_DELTA:
        value *= 0.97
    if notes:
        value *= 0.97
    drivers["support"] = round(support, 4)
    drivers["region_confidence"] = round(region_confidence, 4)
    drivers["share_delta"] = round(share_delta, 4)
    drivers["downgrades"] = len(notes)
    return round(max(0.05, min(0.99, value)), 4), drivers


def _layer_confidence(layers: Dict[str, bool], block_diff: Dict[str, Any],
                      relocation: Dict[str, Any],
                      region_confidence: float) -> Dict[str, Any]:
    """Per-layer confidence: how sure each layer verdict is on its own.

    The template layers depend on the DOM and on non-content block identity,
    which are structural facts and therefore high confidence.  The content
    layer additionally depends on the region location, so its confidence
    tracks the region confidence and any relocation masking that was needed.
    """
    struct_support = 0.0
    if layers["struct"]:
        struct_support = 0.96 if block_diff["changed_non_content_blocks"] \
            or block_diff["added_non_content_blocks"] \
            or block_diff["removed_non_content_blocks"] else 0.90
    else:
        struct_support = 0.97
    chrome_support = 0.95 if layers["chrome"] else 0.97
    template_support = min(0.99, (struct_support + chrome_support) / 2.0)
    content_support = region_confidence
    if relocation["content_equal_after_masking"]:
        content_support = min(0.99, content_support + 0.05)
    return {
        "struct": round(struct_support, 4),
        "chrome": round(chrome_support, 4),
        "template": round(template_support, 4),
        "content": round(content_support, 4),
    }


def _decision(klass: str, layers: Dict[str, bool]) -> Dict[str, Any]:
    if klass == "none":
        return {"refetch": False, "archive": False, "archive_layers": [],
                "reason": "no layer moved; nothing to fetch or archive"}
    if klass == "template":
        return {"refetch": False, "archive": True, "archive_layers": ["template"],
                "reason": "template-only change: content fingerprint unchanged, "
                          "content layer is not re-crawled"}
    if klass == "content":
        archive_layers = ["content"]
        if layers["struct"]:
            archive_layers.append("template")
        return {"refetch": True, "archive": True, "archive_layers": archive_layers,
                "reason": "content text changed: refetch and archive the content layer"}
    return {"refetch": True, "archive": True, "archive_layers": ["template", "content"],
            "reason": "template and content both changed: one refetch refreshes both"}


def decision_record(result: Dict[str, Any]) -> Dict[str, Any]:
    """Compact per-page record used by scan digests and reports."""
    evidence = result["evidence"]
    return {
        "class": result["class"],
        "refetch": bool(result["refetch"]),
        "archive": bool(result["archive"]),
        "archive_layers": list(result["archive_layers"]),
        "confidence": result["confidence"],
        "template_changed": bool(evidence["layers"]["template"]["changed"]),
        "struct_changed": bool(evidence["layers"]["struct"]["changed"]),
        "chrome_changed": bool(evidence["layers"]["chrome"]["changed"]),
        "content_changed": bool(evidence["layers"]["content"]["changed"]),
        "layer_confidence": evidence.get("layer_confidence", {}),
        "region_method": {
            "old": evidence["content_region"]["old"]["method"],
            "new": evidence["content_region"]["new"]["method"],
        },
        "fp_template": result["fp_template"],
        "fp_content": result["fp_content"],
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def records_digest(records: Sequence[Dict[str, Any]]) -> str:
    """Order-independent digest over a whole scan."""
    canonical = sorted(canonical_json(record) for record in records)
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()[:16]


__all__ = [
    "SCHEMA", "CLASSES", "PageError", "compare", "decision_record",
    "records_digest", "canonical_json", "detect_relocation",
]
