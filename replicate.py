"""Local replica over an exported snapshot and a synthetic change feed.

One generation applies a batched snapshot plus the transactional change
log that follows it.  A second generation can be staged in the background
with ``apply --replace`` and is published atomically once its seal arrives
and its baseline is still ahead of the visible position.  Progress is
checkpointed after every event, so the process may die at any of the
advertised crash boundaries and resume from the same input prefix.
"""
import argparse
import fcntl
import json
import os
import sys
from pathlib import Path

CRASH_STAGES = (
    "after-stage",
    "before-publish",
    "before-commit",
    "after-commit",
    "after-publish",
)


class ResnapshotRequired(Exception):
    """The source can no longer extend the published generation."""


class StaleSnapshot(Exception):
    """The replacement snapshot fell behind the published position."""


def crash_exit():
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(97)


def open_source(path):
    return json.loads(path.read_text())


def load_state(db):
    if db.exists():
        return json.loads(db.read_text())
    return {"current": None, "rebuild": None}


def save_state(db, state):
    tmp = db.with_name(db.name + ".tmp")
    with open(tmp, "w") as handle:
        json.dump(state, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, db)
    try:
        dirfd = os.open(str(db.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dirfd)
    except OSError:
        pass
    finally:
        os.close(dirfd)


def apply_ops(tables, ops):
    for item in ops:
        table = tables.setdefault(item["table"], {})
        key = item["key"]
        if item["action"] == "set":
            table[key] = item["value"]
        elif item["action"] == "delete":
            table.pop(key, None)
        elif item["action"] == "move":
            value = table.pop(key)
            table[item["new_key"]] = {**value, **item.get("patch", {})}
        else:
            raise ValueError("unknown action")


def op_stats(ops):
    stats = {}
    for item in ops:
        stats[item["table"]] = stats.get(item["table"], 0) + 1
    return stats


def new_generation(source):
    return {
        "snapshot_id": source["snapshot_id"],
        "snapshot_lsn": source["snapshot_lsn"],
        "staged": {},
        "published": False,
        "tables": {},
        "buffer": {},
        "audit": [],
        "visible_lsn": None,
        "applied_events": 0,
    }


def public_view(state, diagnostics=False):
    current = state["current"]
    view = {
        "published": False,
        "tables": {},
        "audit": [],
        "visible_lsn": None,
        "applied_events": 0,
        "snapshot_id": None,
    }
    if current is not None:
        view["applied_events"] = current["applied_events"]
        if current["published"]:
            view.update({
                "published": True,
                "tables": current["tables"],
                "audit": current["audit"],
                "visible_lsn": current["visible_lsn"],
                "snapshot_id": current["snapshot_id"],
            })
    rebuild = state["rebuild"]
    if diagnostics and rebuild is not None:
        view["rebuild"] = {
            "snapshot_id": rebuild["snapshot_id"],
            "applied_events": rebuild["applied_events"],
            "staged_chunks": len(rebuild["staged"]),
        }
    return view


def inspect(db):
    return public_view(load_state(db))


class Applier:
    """Consumes the event prefix of one source into one generation."""

    def __init__(self, db, state, source, crash):
        self.db = db
        self.state = state
        self.source = source
        self.crash = crash

    def run(self, gen, limit, is_rebuild):
        events = self.source["events"]
        if limit is None or limit > len(events):
            limit = len(events)
        while gen["applied_events"] < limit:
            self.step(gen, events[gen["applied_events"]], is_rebuild)

    def advance(self, gen):
        gen["applied_events"] += 1
        save_state(self.db, self.state)

    def step(self, gen, event, is_rebuild):
        kind = event["kind"]
        if kind == "stage":
            self.step_stage(gen, event)
        elif kind == "seal":
            self.step_seal(gen, is_rebuild)
        elif kind == "begin":
            gen["buffer"][event["xid"]] = {"ops": [], "lsn": None}
            self.advance(gen)
        elif kind == "op":
            xid = event["xid"]
            if xid not in gen["buffer"]:
                raise ValueError("op without open transaction")
            gen["buffer"][xid]["ops"].append(event)
            self.advance(gen)
        elif kind == "abort":
            gen["buffer"].pop(event["xid"], None)
            self.advance(gen)
        elif kind == "commit":
            self.step_commit(gen, event)
        elif kind in ("disconnect", "reconnect"):
            self.advance(gen)
        else:
            raise ValueError("unknown event kind")

    def step_stage(self, gen, event):
        if gen["published"]:
            self.advance(gen)
            return
        index = event["chunk"]
        chunk = self.source["chunks"][index]
        gen["staged"][str(index)] = {"table": chunk["table"], "rows": chunk["rows"]}
        gen["applied_events"] += 1
        save_state(self.db, self.state)
        if self.crash == "after-stage":
            crash_exit()

    def step_seal(self, gen, is_rebuild):
        if gen["published"]:
            self.advance(gen)
            return
        if len(gen["staged"]) < len(self.source["chunks"]):
            raise ValueError("seal before all chunks staged")
        if is_rebuild:
            current = self.state["current"]
            ahead = (
                current is not None
                and current["published"]
                and gen["snapshot_lsn"] > current["visible_lsn"]
            )
            if not ahead:
                raise StaleSnapshot(
                    "snapshot_lsn %s is not ahead of visible lsn %s"
                    % (gen["snapshot_lsn"], current and current["visible_lsn"])
                )
        if self.crash == "before-publish":
            crash_exit()
        self.publish(gen)
        gen["applied_events"] += 1
        if is_rebuild:
            # Atomic switch: visible pointer and log position move together.
            self.state["current"] = gen
            self.state["rebuild"] = None
            save_state(self.db, self.state)
            if self.crash == "after-publish":
                crash_exit()
        else:
            save_state(self.db, self.state)

    def publish(self, gen):
        tables = {}
        for index in range(len(self.source["chunks"])):
            staged = gen["staged"][str(index)]
            tables.setdefault(staged["table"], {}).update(staged["rows"])
        gen["tables"] = tables
        gen["published"] = True
        gen["visible_lsn"] = gen["snapshot_lsn"]
        buffer = gen["buffer"]
        ready = sorted(
            (buf["lsn"], xid)
            for xid, buf in buffer.items()
            if buf["lsn"] is not None
        )
        # Transactions still open may commit after the seal; keep them.
        gen["buffer"] = {
            xid: buf for xid, buf in buffer.items() if buf["lsn"] is None
        }
        for lsn, xid in ready:
            ops = buffer[xid]["ops"]
            apply_ops(gen["tables"], ops)
            gen["audit"].append({"xid": xid, "lsn": lsn, "tables": op_stats(ops)})
            gen["visible_lsn"] = lsn

    def step_commit(self, gen, event):
        xid = event["xid"]
        if xid not in gen["buffer"]:
            raise ValueError("commit without open transaction")
        lsn = event["lsn"]
        buf = gen["buffer"].pop(xid)
        done = {(entry["xid"], entry["lsn"]) for entry in gen["audit"]}
        if (xid, lsn) in done or lsn <= gen["snapshot_lsn"]:
            # Full replay of an applied transaction, or already covered by
            # the snapshot baseline: consume without re-applying.
            self.advance(gen)
            return
        if not gen["published"]:
            buf["lsn"] = lsn
            gen["buffer"][xid] = buf
            self.advance(gen)
            return
        if self.crash == "before-commit":
            crash_exit()
        apply_ops(gen["tables"], buf["ops"])
        gen["audit"].append({"xid": xid, "lsn": lsn, "tables": op_stats(buf["ops"])})
        gen["visible_lsn"] = lsn
        gen["applied_events"] += 1
        save_state(self.db, self.state)
        if self.crash == "after-commit":
            crash_exit()


def apply(source, db, limit, crash, replace):
    state = load_state(db)
    current = state["current"]
    rebuild = state["rebuild"]
    snapshot_id = source["snapshot_id"]

    if current is not None and snapshot_id == current["snapshot_id"]:
        # Replay of the live generation uses its original progress,
        # whether or not --replace was passed.
        if current["published"] and source["available_from"] > current["visible_lsn"]:
            raise ResnapshotRequired(
                "source retains logs from %s but replica is at %s"
                % (source["available_from"], current["visible_lsn"])
            )
        target, is_rebuild = current, False
    elif rebuild is not None and snapshot_id == rebuild["snapshot_id"] and replace:
        target, is_rebuild = rebuild, True
    elif current is None:
        target = new_generation(source)
        state["current"] = target
        save_state(db, state)
        is_rebuild = False
    elif replace and current["published"] and rebuild is None:
        if not source["snapshot_lsn"] > current["visible_lsn"]:
            raise StaleSnapshot(
                "snapshot_lsn %s is not ahead of visible lsn %s"
                % (source["snapshot_lsn"], current["visible_lsn"])
            )
        target = new_generation(source)
        state["rebuild"] = target
        save_state(db, state)
        is_rebuild = True
    elif replace and rebuild is not None:
        raise ValueError("rebuild_in_progress: another snapshot is still staging")
    else:
        raise ResnapshotRequired(
            "source snapshot %r does not match published %r"
            % (snapshot_id, current["snapshot_id"])
        )

    Applier(db, state, source, crash).run(target, limit, is_rebuild)
    return public_view(state, diagnostics=True)


def cmd_apply(args):
    source = open_source(args.source)
    lock_path = args.db.with_name(args.db.name + ".lock")
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return apply(source, args.db, args.limit, args.crash, args.replace)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("apply")
    sync.add_argument("--source", type=Path, required=True)
    sync.add_argument("--db", type=Path, required=True)
    sync.add_argument("--limit", type=int)
    sync.add_argument("--replace", action="store_true")
    sync.add_argument("--crash", choices=list(CRASH_STAGES))
    view = commands.add_parser("inspect")
    view.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "apply":
            result = cmd_apply(args)
        else:
            result = inspect(args.db)
        print(json.dumps(result, sort_keys=True))
    except ResnapshotRequired as error:
        parser.exit(2, "resnapshot_required: %s\n" % error)
    except StaleSnapshot as error:
        parser.exit(2, "stale_snapshot: %s\n" % error)
    except (ValueError, KeyError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
