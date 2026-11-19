"""Executes a plan against the current table sizes and reports its cost.

Cost is in row visits; the API's latency budget is about 50 000 visits.
"""
from .catalog import CatalogError


def run_plan(p, query, catalog):
    for name in (query["outer"], query["inner"]):
        if name not in catalog.tables:
            raise CatalogError(f"relation {name} does not exist")
    expected = {p.inner_index: (query["inner"], query["join_col"]),
                p.outer_index: (query["outer"], query.get("filter_col"))}
    for idx, key in expected.items():
        if not idx:
            continue
        if idx not in catalog.indexes:
            raise CatalogError(f"could not open relation with OID for index {idx}")
        if catalog.indexes[idx] != key:
            raise CatalogError(f"index {idx} is on {catalog.indexes[idx][0]}.{catalog.indexes[idx][1]}, "
                               f"plan expects {key[0]}.{key[1]}")
    outer = catalog.tables[query["outer"]].rows
    inner = catalog.tables[query["inner"]].rows
    outer_rows = max(1, outer // 100) if p.outer_index else outer
    scan_outer = outer_rows * 2 if p.outer_index else outer
    if p.join == "hash join":
        return scan_outer + inner
    if p.inner_index:
        return scan_outer + outer_rows * 3
    return scan_outer + outer_rows * inner
