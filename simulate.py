"""Run concurrent sessions with timeouts and cancel requests.

    python3 simulate.py scenario.json result.json

scenario.json:

    {"data": {"accounts": {"1": 100}},
     "sessions": {"s1": {"statement_timeout": 5, "script": [{"cmd": "update", "table": "accounts", "key": "1",
                                                              "value": 90, "work": 10}]}},
     "cancel": [{"tick": 3, "session": "s1"}]}

Commands: begin, commit, rollback, disconnect, update (table, key, value,
work ticks), lock_table (table, mode, work ticks), savepoint / rollback_to /
release (name).  ``"on_error": true`` runs a command only when the
transaction is in the failed state.  ``python3 batch.py`` builds the
scripts of the record-by-record batch jobs.
"""
import json
import sys

from cancel.monitor import blocked_sessions, lock_report
from cancel.scheduler import run


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    out = run(spec)
    out["report"] = lock_report(out["locks"], out["waiting"])
    out["blocked"] = blocked_sessions(out["waiting"])
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
