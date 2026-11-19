# rlsx

Row-level security for the billing database, modelled on PostgreSQL:
`ALTER TABLE ... ENABLE / FORCE ROW LEVEL SECURITY`, `CREATE POLICY ... AS
PERMISSIVE | RESTRICTIVE FOR <command> TO <roles> USING (...) WITH CHECK
(...)`, table owners, roles with group membership and the BYPASSRLS /
superuser attributes. The behaviour must be the same as PostgreSQL's.

## Layout

- `rlsx/expr.py` – policy expressions
- `rlsx/catalog.py` – tables, roles, policies and policy lookup
- `rlsx/engine.py` – SELECT / INSERT / UPDATE / DELETE under RLS
- `runsec.py` – runs statements as different sessions

## Running

    python3 runsec.py examples/billing.json examples/statements.json out.json
    python3 -m unittest discover -s tests
