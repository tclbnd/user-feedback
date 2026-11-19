"""Record-by-record batch jobs.

The settlement batch updates many rows in one transaction.  Each record is
wrapped in a savepoint; if the record's statement fails (bad data, lock
timeout, operator cancel of a stuck record) the job rolls back to the
savepoint, logs the record and carries on with the next one, so a single
record never throws away the whole batch.
"""


def settlement_script(table, records, work=1):
    """``records`` is a list of (key, value) pairs."""
    script = [{"cmd": "begin"}]
    for i, (key, value) in enumerate(records):
        name = f"rec{i}"
        script.append({"cmd": "savepoint", "name": name})
        script.append({"cmd": "update", "table": table, "key": key, "value": value, "work": work})
        script.append({"cmd": "rollback_to", "name": name, "on_error": True})
        script.append({"cmd": "release", "name": name})
    script.append({"cmd": "commit"})
    return script


def failed_records(results, records):
    """Keys of the records whose update did not succeed, from a session's results."""
    keys = []
    updates = [r for r in results if r["cmd"] == "update"]
    for (key, _), res in zip(records, updates):
        if res["outcome"] != "ok":
            keys.append(key)
    return keys
