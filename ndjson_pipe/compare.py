"""Comparison helpers between a live Store and the batch reference outcome."""

from typing import List, Tuple

from .reference import ExpectedOutcome
from .store import Store


def compare_store_to_expected(store: Store, source_id: str, expected: ExpectedOutcome) -> List[str]:
    """Return a list of human-readable differences (empty list = identical).

    Row content is compared as sets keyed by the full row tuple: physical
    storage order is irrelevant, while every field -- including byte offsets,
    schema version lineage and content hashes -- must match exactly.
    """
    diffs: List[str] = []

    actual_records = set(store.dump_records(source_id))
    actual_rejections = set(store.dump_rejections(source_id))
    actual_conflicts = set(store.dump_conflicts(source_id))
    actual_checkpoint = store.get_checkpoint(source_id)

    if actual_records != set(expected.records):
        diffs.append(_table_diff("records", expected.records, list(actual_records)))
    if actual_rejections != set(expected.rejections):
        diffs.append(_table_diff("rejections", expected.rejections, list(actual_rejections)))
    if actual_conflicts != set(expected.conflicts):
        diffs.append(_table_diff("conflicts", expected.conflicts, list(actual_conflicts)))
    if actual_checkpoint != expected.checkpoint:
        diffs.append(
            f"checkpoint differs: expected={expected.checkpoint} actual={actual_checkpoint}"
        )
    return diffs


def _table_diff(name: str, expected: List[Tuple], actual: List[Tuple]) -> str:
    expected_set, actual_set = set(expected), set(actual)
    missing = sorted(expected_set - actual_set)
    unexpected = sorted(actual_set - expected_set)
    parts = [f"table {name!r} differs (expected {len(expected)} rows, got {len(actual)})"]
    for row in missing[:10]:
        parts.append(f"  missing:    {row}")
    for row in unexpected[:10]:
        parts.append(f"  unexpected: {row}")
    return "\n".join(parts)


def compare_stores(source_id: str, store_a: Store, store_b: Store) -> List[str]:
    """Compare two databases' full state for one source (crash-run verification)."""
    diffs: List[str] = []
    for table, dumper in (
        ("records", Store.dump_records),
        ("rejections", Store.dump_rejections),
        ("conflicts", Store.dump_conflicts),
    ):
        rows_a, rows_b = set(dumper(store_a, source_id)), set(dumper(store_b, source_id))
        if rows_a != rows_b:
            diffs.append(_table_diff(table, list(rows_a), list(rows_b)))
    cp_a, cp_b = store_a.get_checkpoint(source_id), store_b.get_checkpoint(source_id)
    if cp_a != cp_b:
        diffs.append(f"checkpoint differs: baseline={cp_a} crash-run={cp_b}")
    return diffs
