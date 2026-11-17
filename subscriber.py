#!/usr/bin/env python3
"""Logical replication subscriber."""
import argparse
import json
import os
import sys

from apply import Applier, Replica
from publisher import ConnectionLost, Publisher


def valid_position(publisher, position):
    """Snap a stored position onto a real commit boundary.

    A position must be the LSN of a ``commit`` message; anything else would
    resume mid-transaction and silently drop that transaction's prefix.  This
    matters when adopting a replica written by an older subscriber that saved
    uncommitted LSNs.
    """
    if position <= 0:
        return 0
    commits = [m["lsn"] for m in publisher.log if m["type"] == "commit"]
    if position in commits:
        return position
    replay = max([lsn for lsn in commits if lsn < position], default=0)
    print(f"sync: warning: stored position {position} is not a commit boundary; "
          f"resuming from {replay}", file=sys.stderr)
    return replay


def sync(replica_dir, publisher_dir, drop_after=None, kill_after=None):
    commits = [0]

    def on_commit():
        # Runs only after replica.json, conflicts.jsonl and position.json all
        # describe the same committed transaction.
        commits[0] += 1
        if kill_after is not None and commits[0] >= kill_after:
            os._exit(137)

    replica = Replica(replica_dir, on_commit)
    publisher = Publisher(publisher_dir)
    position = valid_position(publisher, replica.position)
    if position != replica.position:
        # Record the corrected boundary so a crash during this run cannot
        # resurrect the bad one.
        replica.rewind(position)
    applier = Applier(replica)
    applied = 0

    # If the slot claims more was confirmed than we have applied, the log we
    # need is gone: say so instead of silently applying a suffix.
    if publisher.confirmed > position:
        print(f"sync: warning: slot confirmed_lsn={publisher.confirmed} is ahead of "
              f"replica position {position}; data before it cannot be replayed",
              file=sys.stderr)

    def feedback(lsn):
        if lsn > publisher.confirmed:
            publisher.feedback(lsn)

    try:
        for message in publisher.stream(position, drop_after):
            done = applier.receive(message)
            if done is not None:
                applied += 1
                position = done
                # The transaction's data, conflict records and position are
                # already durable together; only now may we tell the publisher
                # this prefix is done for good.  A drop before this point just
                # means the transaction is delivered again and skipped.
                feedback(position)
    except ConnectionLost:
        raise
    return {"position": position, "applied": applied}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["sync"])
    parser.add_argument("replica")
    parser.add_argument("publisher")
    parser.add_argument("--drop-after", type=int)
    parser.add_argument("--kill-after-commits", type=int)
    args = parser.parse_args()
    try:
        print(json.dumps(sync(args.replica, args.publisher, args.drop_after, args.kill_after_commits)))
    except ConnectionLost as exc:
        print(f"sync: {exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    sys.exit(main())
