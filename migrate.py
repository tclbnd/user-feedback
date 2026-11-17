"""Shadow-table online schema change.

Phases (each injectable via --fail-at):

  setup    create the shadow table and change log, install the capture trigger
  copy     copy existing rows in primary-key order, one chunk per clock tick
  replay   drain the change log outside the lock, one batch per clock tick
  cutover  take the short table lock and flush the final (at most one) batch
  swap     atomically rename source -> old, shadow -> source, then drop the
           trigger / change log / old table

Every business write that lands while the copy runs is captured by the trigger
into the change log with a monotonic ``seq``. Replay walks the log in seq order
and applies each entry against the shadow, so delete-then-reinsert of the same
key and primary-key updates converge on exactly the source row. A failure in
any phase triggers :meth:`Migration.rollback`, which removes every OSC object
and leaves the original table (schema and rows) exactly as business traffic
left it.
"""

PHASES = ("copy", "replay", "cutover", "swap")

# Object states used to make rollback idempotent and order independent.
S_SETUP = "setup"
S_COPIED = "copied"
S_LOCKED = "locked"
S_RENAMED = "renamed"  # source -> old done, shadow -> source not yet done
S_SWAPPED = "swapped"  # both renames done
S_CLEAN = "clean"


class Failure(Exception):
    """Raised by an injected fault; the CLI turns it into a rolled-back run."""


class Migration:
    def __init__(self, db, alter, chunk=4, fail_at=None):
        self.db, self.alter, self.chunk, self.fail_at = db, alter, chunk, fail_at
        name = self.table = alter["table"]
        self.shadow = "_osc_new_" + name
        self.log = "_osc_log_" + name
        self.trigger = "_osc_trg_" + name
        self.old = "_osc_old_" + name
        self.summary = {"table": name, "copied": 0, "replayed": 0}
        self.state = None
        self.position = 0  # highest change-log seq already replayed

    # ------------------------------------------------------------------ utils
    def inject(self, phase):
        if self.fail_at == phase:
            raise Failure("injected failure during " + phase)

    def target_schema(self):
        src = self.db.tables[self.table]
        drop = set(self.alter.get("drop", [])) - {src["pk"]}
        columns = [c for c in src["columns"] if c not in drop]
        defaults = {c: v for c, v in src["defaults"].items() if c not in drop}
        for col in self.alter.get("add", []):
            columns.append(col["name"])
            defaults[col["name"]] = col.get("default")
        return src["pk"], columns, defaults

    def backlog(self, position):
        log = self.db.tables[self.log]["rows"]
        return [log[s] for s in sorted(log) if s > position]

    def apply_entry(self, entry):
        """Apply one change-log entry to the shadow table.

        Sequential application (instead of folding a whole batch) is required
        so that a delete followed by a re-insert, or an update that moves the
        primary key, converge to the exact current source row.
        """
        shadow = self.db.tables[self.shadow]
        if entry["op"] == "delete":
            shadow["rows"].pop(entry["pk"], None)
            return
        # insert / update carry the full post-change row.
        row = self.db.put(self.shadow, entry["row"])
        if entry["op"] == "update" and entry.get("old_pk") is not None \
                and entry["old_pk"] != row[shadow["pk"]]:
            # Primary key moved: drop the stale copy kept under the old key.
            shadow["rows"].pop(entry["old_pk"], None)

    def replay_batch(self, entries):
        if not entries:
            return
        for entry in entries:
            self.apply_entry(entry)
        self.position = entries[-1]["seq"]
        self.summary["replayed"] += len(entries)

    def drain(self, locked):
        """Replay log batches.

        Unlocked phase: stop once at most one batch remains so the lock only
        ever covers that final batch. Locked (final) phase: flush everything.
        """
        while True:
            pending = self.backlog(self.position)
            if not pending:
                return
            if not locked and len(pending) <= self.chunk:
                return
            self.replay_batch(pending[:self.chunk])
            self.inject("replay")
            self.db.tick()

    # ------------------------------------------------------------------ phases
    def setup(self):
        db = self.db
        db.create(self.shadow, *self.target_schema())
        db.create(self.log, "seq", ["seq", "op", "pk", "old_pk", "row"])
        # Trigger must exist before any row is copied so no write is missed.
        db.triggers.append({"name": self.trigger, "table": self.table,
                            "log": self.log})
        self.state = S_SETUP

    def copy(self):
        db = self.db
        # Primary-key cursor, not an offset: inserts during the copy grow the
        # source table, so offset pagination would skip rows. Walk keys forward.
        high_water = None
        while True:
            rows, high_water = db.chunk_after(self.table, high_water, self.chunk)
            if not rows:
                break
            for row in rows:
                db.put(self.shadow, row)
            self.summary["copied"] += len(rows)
            self.inject("copy")
            db.tick()
        self.state = S_COPIED

    def cutover(self):
        db = self.db
        # 1) unlocked catch-up: leave at most one batch outstanding.
        self.drain(locked=False)
        # 2) short lock: only the final batch + swap happen underneath it.
        db.lock(self.table)
        self.state = S_LOCKED
        self.inject("cutover")
        self.drain(locked=True)

        # 3) atomic catalog swap: source -> old, shadow -> source.
        db.rename(self.table, self.old)
        self.state = S_RENAMED  # a fault here means only the first rename ran
        self.inject("swap")
        db.rename(self.shadow, self.table)
        self.state = S_SWAPPED

        # 4) remove capture plumbing and the retired source table.
        db.triggers[:] = [t for t in db.triggers if t["name"] != self.trigger]
        db.tables.pop(self.log, None)
        db.tables.pop(self.old, None)
        self.state = S_CLEAN

        # 5) release the lock; queued business writes now run against the new
        #    table carrying the original name.
        db.unlock(self.table)

    def run(self):
        self.setup()
        self.copy()
        self.cutover()

    # ---------------------------------------------------------------- rollback
    def rollback(self):
        """Best-effort, idempotent teardown. Never raises.

        Invariant: before the swap the original table is untouched by the tool
        (only shadow/log/trigger objects exist), so dropping them restores the
        pre-migration state exactly. After a partial swap the renames are
        reversed before the same cleanup.
        """
        db = self.db
        try:
            if self.state == S_SWAPPED:
                # Both renames completed: undo them in reverse order.
                if self.table in db.tables and self.old in db.tables:
                    db.rename(self.table, self.shadow)
                    db.rename(self.old, self.table)
            elif self.state == S_RENAMED:
                # Only source -> old happened; move it back to source.
                if self.old in db.tables and self.table not in db.tables:
                    db.rename(self.old, self.table)
            if self.table in db.locks:
                db.unlock(self.table)
            db.triggers[:] = [t for t in db.triggers if t["name"] != self.trigger]
            for leftover in (self.shadow, self.log, self.old):
                db.tables.pop(leftover, None)
            self.state = None
        except Exception:  # pragma: no cover - rollback must never throw
            self.state = None
