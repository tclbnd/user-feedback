"""MERGE statement execution.

    MERGE INTO target t USING source s ON t.<on_target> = s.<on_source>
      WHEN MATCHED [AND cond] THEN UPDATE SET ... | DELETE
      WHEN NOT MATCHED [AND cond] THEN INSERT (...)

Clauses of each kind are tried in the order written.
"""
from .expr import truth, value
from .table import ConstraintError


class MergeError(Exception):
    pass


def _apply_set(assignments, src, tgt):
    return {col: value(term, src, tgt) for col, term in assignments.items()}


def merge(target, source_rows, stmt, log=None, on_change=None):
    """Apply ``stmt``; ``on_change(change, old_row, new_row)`` is called after every change."""
    on_t, on_s = stmt["on"]
    matched = [c for c in stmt["when"] if c["kind"] == "matched"]
    not_matched = [c for c in stmt["when"] if c["kind"] == "not_matched"]
    counts = {"inserted": 0, "updated": 0, "deleted": 0, "skipped": 0}

    for src in source_rows:
        hits = target.find(on_t, src.get(on_s))
        try:
            if hits:
                rowid = hits[0]
                tgt = target.rows[rowid]
                clause = None
                for candidate in matched:
                    if truth(candidate.get("cond"), src, tgt) is False:
                        continue
                    clause = candidate
                    break
                if clause is None:
                    counts["skipped"] += 1
                    continue
                if clause["action"] == "delete":
                    target.delete(rowid)
                    counts["deleted"] += 1
                    if on_change:
                        on_change("delete", tgt, None)
                else:
                    target.update(rowid, _apply_set(clause["set"], src, tgt))
                    counts["updated"] += 1
                    if on_change:
                        on_change("update", tgt, target.rows[rowid])
            else:
                for clause in not_matched:
                    if truth(clause.get("cond"), src, None):
                        new_id = target.insert(_apply_set(clause["values"], src, None))
                        counts["inserted"] += 1
                        if on_change:
                            on_change("insert", None, target.rows[new_id])
                        break
                else:
                    counts["skipped"] += 1
        except ConstraintError as exc:
            if log is not None:
                log.append({"source": src, "error": str(exc)})
            counts["skipped"] += 1
    return counts
