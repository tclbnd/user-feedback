# connpool

Connection pool and request handlers of the account service, plus a small
simulated database server so the whole request path can be replayed offline.

## Layout

- `connpool/server.py` – simulated server; sessions keep their own transaction, SET parameters and prepared statements
- `connpool/pool.py` – the pool (`Pool`, `Borrowed`) and the per-connection statement cache
- `connpool/handlers.py` – request handlers used by the API layer
- `connpool/metrics.py` – latency histogram for the dashboard
- `replay.py` – replays a list of requests through one pool

## Running

    python3 replay.py examples/mixed.json out.json
    python3 -m unittest discover -s tests

A request file looks like

    {"pool_size": 1,
     "seed": [{"key": "profile:alice", "value": "v1"}],
     "requests": [{"call": "update_profile", "user": "alice", "value": "v2", "fail": true},
                  {"call": "plain_read", "key": "profile:alice"}]}

Calls: `get_profile` (user), `update_profile` (user, value, optional `fail`
to make validation fail after the write), `report_time` (optional tenant
`tz`), `tenant_read` (schema, key), `plain_read` (key), `audit_write` (key,
value). A request may carry `kill_before: [pid]` to have the server drop that
idle backend before the request runs (failover, idle timeout on a proxy).

`out.json` has `results` (one per request), the committed `data`,
`roundtrips`, `sessions` (backends opened) and the median round trips per
request. Round trips are what the pool's throughput is measured in.
