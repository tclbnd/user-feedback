# tenantdb

Row-level security for the multi-tenant query layer. Every table has a
`tenant` column; a session may only ever see rows of its own tenant. The
policy, view and function rules are described in `rls/catalog.py`.

## Layout

- `rls/catalog.py` – tables, views, RLS policies, user functions (leakproof or not)
- `rls/planner.py` – expands views and attaches RLS quals for the session
- `rls/executor.py` – runs the rewritten plan
- `rls/tenants.py` – admin console helpers
- `rls/server.py` – the query service loop: one catalog, sessions of many tenants
- `runquery.py` – runs one plan for one tenant, or a sequence of sessions

## Running

    python3 runquery.py examples/db.json examples/q_leaky.json out.json
    python3 -m unittest discover -s tests

The output has the visible row ids (or the query error), the arguments
non-leakproof functions were called with (`leak_log`), the number of rows
read from tables and the number of subquery executions. Nothing in the
output may depend on another tenant's rows.
