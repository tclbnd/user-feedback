"""Replay a sequence of service requests through the pool.

    python3 replay.py requests.json result.json

The result has one entry per request (its return value or error), the final
committed data, and the server round trips spent.
"""
import json
import sys

from connpool import handlers
from connpool.metrics import Histogram
from connpool.pool import Pool
from connpool.server import Server

CALLS = {
    "get_profile": lambda p, r: handlers.get_profile(p, r["user"]),
    "update_profile": lambda p, r: handlers.update_profile(p, r["user"], r["value"], r.get("fail", False)),
    "report_time": lambda p, r: handlers.report_time(p, r.get("tz")),
    "tenant_read": lambda p, r: handlers.tenant_read(p, r["schema"], r["key"]),
    "plain_read": lambda p, r: handlers.plain_read(p, r["key"]),
    "audit_write": lambda p, r: handlers.audit_write(p, r["key"], r["value"]),
}


def run(spec):
    server = Server()
    for item in spec.get("seed", []):
        server.data[(item.get("schema", "public"), item["key"])] = item["value"]
    pool = Pool(server, spec.get("pool_size", 1))
    results, latency = [], Histogram()
    for req in spec["requests"]:
        before = server.roundtrips
        for kill in req.get("kill_before", []):
            for conn in list(pool.idle):
                if conn.session.pid == kill:
                    conn.session.broken = True
        try:
            results.append({"ok": True, "value": CALLS[req["call"]](pool, req)})
        except Exception as exc:  # the web framework turns any error into a 500
            results.append({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        latency.observe(server.roundtrips - before)
    data = [{"schema": s, "key": k, "value": v} for (s, k), v in sorted(server.data.items())]
    return {"results": results, "data": data, "roundtrips": server.roundtrips,
            "sessions": server.sessions_opened, "p50_roundtrips": latency.quantile(0.5)}


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(run(spec), f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
