# mergeq

MERGE support of the warehouse loader. The nightly job syncs the
`dim_customer` dimension from the CRM extract with one MERGE statement
(`mergeq/dimsync.py`).

## Layout

- `mergeq/table.py` – tables with primary key and unique columns
- `mergeq/expr.py` – conditions and SET expressions (`src.col`, `tgt.col`, three-valued logic)
- `mergeq/merge.py` – MERGE execution
- `mergeq/session.py` – transactions and statement execution
- `mergeq/dimsync.py` – the daily customer sync and a small tier report
- `mergeq/history.py` – type 2 history of the customer dimension (`dim_customer_history`)
- `mergeq/nightly.py` – the nightly sync of the full extract, applied in chunks
- `mergeq/audit.py` – load audit summary checked by the DWH monitoring
- `runmerge.py` – runs one MERGE from a JSON job file
- `nightly.py` – runs the nightly sync from a JSON job file

## Running

    python3 runmerge.py examples/daily.json out.json
    python3 -m unittest discover -s tests

WHEN clauses: `{"kind": "matched", "cond": ..., "action": "update", "set": {...}}`,
`{"kind": "matched", "action": "delete"}`,
`{"kind": "not_matched", "cond": ..., "action": "insert", "values": {...}}`.
Conditions: `{"cmp": [op, a, b]}`, `{"and": [...]}`, `{"or": [...]}`,
`{"not": c}`, `{"is_null": term}`.

The output has `outcome` (`ok`, counts or `error`) and the target rows after
the transaction is committed or rolled back.
