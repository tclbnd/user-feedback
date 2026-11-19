"""The nightly sync for large CRM extracts.

The full extract has a few hundred thousand rows.  Holding the undo
snapshot of the whole dimension for one statement made the loader run out
of memory, so the job applies the extract in chunks; each chunk is one
MERGE in its own transaction.
"""
from .audit import summarize
from .dimsync import DAILY_MERGE
from .history import HistoryWriter
from .merge import merge
from .session import Session

CHUNK_ROWS = 500


def run_nightly(table, extract, history=None, stmt=DAILY_MERGE, chunk_rows=CHUNK_ROWS):
    tables = {"dim_customer": table}
    on_change = HistoryWriter(history) if history is not None else None
    totals = {"inserted": 0, "updated": 0, "deleted": 0, "skipped": 0}
    errors = []
    for start in range(0, len(extract), chunk_rows):
        chunk = extract[start:start + chunk_rows]
        session = Session(tables)
        session.begin()
        outcome = session.run(merge, table, chunk, stmt, errors, on_change)
        if not outcome["ok"]:
            session.rollback()
            outcome["row_errors"] = errors
            outcome["failed_chunk"] = start // chunk_rows
            return dict(outcome, audit=summarize(extract, outcome))
        session.commit()
        for k, v in outcome["result"].items():
            totals[k] += v
    outcome = {"ok": True, "result": totals, "row_errors": errors}
    return dict(outcome, audit=summarize(extract, outcome))
