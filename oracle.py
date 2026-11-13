#!/usr/bin/env python3
"""Independent reference interpreter used only by the test-suite.

Written directly from the task contract; it deliberately imports nothing from
``ingest.py``.  It answers "given the history of accepted source events plus a
new NDJSON batch, what should the parent rows, child rows and reject rows be?"
by replaying every event one at a time in source order.
"""

import json
import sys


class Problem(Exception):
    def __init__(self, path, reason):
        self.path = path
        self.reason = reason


def parse_event(obj):
    """Return (array_state, [(channel, value), ...]) or raise Problem."""

    if not isinstance(obj, dict):
        raise Problem("/", "event must be a JSON object")
    if not isinstance(obj.get("id"), str) or not obj["id"]:
        raise Problem("/id", "id must be a non-empty string")

    rev = obj.get("rev")
    if not isinstance(rev, int) or isinstance(rev, bool) or rev <= 0:
        raise Problem("/rev", "rev must be a positive integer")
    if rev > 2 ** 63 - 1:
        raise Problem("/rev", "rev is out of range")

    schema = obj.get("schema")
    if schema not in (1, 2):
        raise Problem("/schema", "schema must be 1 or 2")
    if not isinstance(obj.get("device"), str):
        raise Problem("/device", "device must be a string")

    if schema == 1:
        key, channel_key, value_key = "readings", "channel", "value"
    else:
        key, channel_key, value_key = "samples", "ch", "v"

    if key not in obj:
        return "missing", []
    value = obj[key]
    if value is None:
        return "null", []
    if not isinstance(value, list):
        raise Problem("/" + key, "array must be a list or null")
    if not value:
        return "empty", []

    children = []
    for index, item in enumerate(value):
        if item is None:
            raise Problem("/%s/%d" % (key, index), "null element")
        if not isinstance(item, dict):
            raise Problem(
                "/%s/%d" % (key, index), "element must be an object"
            )
        if channel_key not in item:
            raise Problem(
                "/%s/%d/%s" % (key, index, channel_key), "missing channel"
            )
        channel = item[channel_key]
        if not isinstance(channel, str) or not channel:
            raise Problem(
                "/%s/%d/%s" % (key, index, channel_key),
                "channel must be a non-empty string",
            )
        if value_key not in item:
            raise Problem(
                "/%s/%d/%s" % (key, index, value_key), "missing value"
            )
        value_int = item[value_key]
        if not isinstance(value_int, int) or isinstance(value_int, bool):
            raise Problem(
                "/%s/%d/%s" % (key, index, value_key),
                "value must be an integer",
            )
        if not (-(2 ** 63) <= value_int <= 2 ** 63 - 1):
            raise Problem(
                "/%s/%d/%s" % (key, index, value_key),
                "value is out of range",
            )
        children.append((channel, value_int))
    return "present", children


def canonical(event, state, children):
    return json.dumps(
        [event.get("device"), state, [[c, v] for c, v in children]],
        sort_keys=True,
        separators=(",", ":"),
    )


def replay(batches):
    """Replay every batch in order.

    ``batches`` is a list of ``(source_name, [event, ...])``.  Returns
    ``(event_rows, child_rows, reject_rows)`` for the final public state.
    """

    versions = {}
    accepted = {}
    parents = {}
    children = {}
    rejects = {}

    for source_name, events in batches:
        for line, event in enumerate(events, start=1):
            event_id = event.get("id") if isinstance(event, dict) else None
            rev = event.get("rev") if isinstance(event, dict) else None

            try:
                state, kids = parse_event(event)
            except Problem as problem:
                rejects[(source_name, line, event_id, rev, problem.path,
                         problem.reason)] = {
                    "id": event_id, "rev": rev, "source": source_name,
                    "line": line, "path": problem.path, "reason": problem.reason,
                }
                continue

            identity = (event_id, rev)
            payload = canonical(event, state, kids)

            if identity in versions:
                if versions[identity] != payload:
                    key = (source_name, line, event_id, rev, "/",
                           "same id/rev with different payload")
                    rejects[key] = {
                        "id": event_id, "rev": rev, "source": source_name,
                        "line": line, "path": "/",
                        "reason": "same id/rev with different payload",
                    }
                continue

            highest = accepted.get(event_id)
            if highest is not None and rev < highest:
                continue
            if highest is not None and rev == highest:
                key = (source_name, line, event_id, rev, "/",
                       "same id/rev with different payload")
                rejects[key] = {
                    "id": event_id, "rev": rev, "source": source_name,
                    "line": line, "path": "/",
                    "reason": "same id/rev with different payload",
                }
                continue

            versions[identity] = payload
            accepted[event_id] = rev
            parents[event_id] = (rev, event["device"], state)
            children[event_id] = [
                (rev, ordinal, channel, value)
                for ordinal, (channel, value) in enumerate(kids)
            ]

    event_rows = [
        {"id": event_id, "rev": rev, "device": device, "array_state": state}
        for event_id, (rev, device, state) in parents.items()
    ]
    child_rows = []
    for event_id, items in children.items():
        for rev, ordinal, channel, value in items:
            child_rows.append(
                {"id": event_id, "rev": rev, "ordinal": ordinal,
                 "channel": channel, "value": value}
            )

    return event_rows, child_rows, list(rejects.values())


def main(argv):
    history_path, batch_path, source = argv[1], argv[2], argv[3]
    with open(history_path) as handle:
        history = [json.loads(line) for line in handle if line.strip()]
    with open(batch_path) as handle:
        batch = [json.loads(line) for line in handle if line.strip()]
    events, readings, rejects = replay([("history", history), (source, batch)])
    print(json.dumps({"events": events, "readings": readings,
                      "rejects": rejects}, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv)
