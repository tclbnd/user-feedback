#!/usr/bin/env python3
"""Logical replication subscriber."""
import argparse
import json
import os
import sys

from apply import Applier, Replica, write_json
from publisher import ConnectionLost, Publisher

SAVE_EVERY = 4


def load_position(replica_dir):
    path = os.path.join(replica_dir, "position.json")
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["commit_lsn"]


def sync(replica_dir, publisher_dir, drop_after=None, kill_after=None):
    commits = [0]

    def on_commit():
        commits[0] += 1
        if kill_after is not None and commits[0] >= kill_after:
            os._exit(137)

    replica = Replica(replica_dir, on_commit)
    publisher = Publisher(publisher_dir)
    applier = Applier(replica)
    position = received = load_position(replica_dir)
    applied = 0

    def save(lsn):
        write_json(os.path.join(replica_dir, "position.json"), {"commit_lsn": lsn})
        publisher.feedback(lsn)

    try:
        for message in publisher.stream(position, drop_after):
            received = message["lsn"]
            done = applier.receive(message)
            if done is not None:
                applied += 1
                position = done
                if applied % SAVE_EVERY == 0:
                    save(position)
    finally:
        save(received)
    return {"position": received, "applied": applied}


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
