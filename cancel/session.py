"""Client sessions: transactions and statement execution.

A statement runs as a series of steps, one per scheduler tick.  An UPDATE
takes an IX lock on the table and an X lock on the row, then works for a
number of ticks and finally writes.  Outside BEGIN ... COMMIT every
statement runs in its own implicit transaction.

Inside a transaction block, SAVEPOINT / ROLLBACK TO SAVEPOINT / RELEASE
SAVEPOINT work as in PostgreSQL: rolling back to a savepoint undoes the
writes made after it, also leaves the failed state, and keeps the savepoint
so it can be used again.  The batch jobs wrap every record in a savepoint
and roll back to it when the record fails (``"on_error": true`` runs a
command only when the transaction is in the failed state, and skips it
otherwise).
"""


class Session:
    def __init__(self, sid, db, locks, script, timeout=None):
        self.sid = sid
        self.db = db
        self.locks = locks
        self.script = list(script)
        self.timeout = timeout
        self.state = "idle"            # idle | in_txn | failed | closed
        self.explicit = False
        self.pending = {}
        self.stmt = None
        self.steps = []
        self.waiting = None
        self.started = 0
        self.results = []
        self.savepoints = []           # [(name, pending copy, locks held at that point)]

    # statement lifecycle --------------------------------------------------
    def next_statement(self, tick):
        if not self.script or self.state == "closed":
            return False
        cmd = self.script.pop(0)
        self.stmt, self.started = cmd, tick
        kind = cmd["cmd"]
        if cmd.get("on_error") and self.state != "failed":
            self._done("skipped")
        elif kind in ("savepoint", "rollback_to", "release"):
            self._savepoint_cmd(kind, cmd["name"])
        elif kind == "begin":
            self.state, self.explicit = "in_txn", True
            self._done("ok")
        elif kind == "commit":
            self.commit()
        elif kind == "rollback":
            self.rollback()
            self._done("ok")
        elif kind == "disconnect":
            self.disconnect()
            self._done("ok")
        elif self.state == "failed":
            self._done("error: current transaction is aborted")
        else:
            if self.state == "idle":
                self.state = "in_txn"
            self.steps = self._plan(cmd)
        return True

    def _plan(self, cmd):
        table = cmd["table"]
        if cmd["cmd"] == "lock_table":
            return [("lock", ("table", table), cmd.get("mode", "X"))] + [("work",)] * cmd.get("work", 0)
        steps = [("lock", ("table", table), "IX"), ("lock", ("row", table, cmd["key"]), "X")]
        steps += [("work",)] * cmd.get("work", 0)
        steps.append(("write", table, cmd["key"], cmd["value"]))
        return steps

    def step(self, tick):
        if self.waiting is not None:
            return
        while self.steps:
            action = self.steps.pop(0)
            if action[0] == "lock":
                if not self.locks.acquire(self.sid, action[1], action[2]):
                    self.waiting = action[1]
                    return
                continue
            if action[0] == "work":
                return
            if action[0] == "write":
                self.pending[(action[1], action[2])] = action[3]
        if self.stmt is not None:
            self._done("ok")
            if not self.explicit:
                self.commit(record=False)

    def lock_granted(self, res):
        if self.waiting == res:
            self.waiting = None

    def busy(self):
        return self.stmt is not None

    def _done(self, outcome):
        if self.stmt is not None:
            self.results.append({"cmd": self.stmt["cmd"], "outcome": outcome})
        self.stmt, self.steps = None, []

    # savepoints -------------------------------------------------------------
    def _find(self, name):
        for i in range(len(self.savepoints) - 1, -1, -1):
            if self.savepoints[i][0] == name:
                return i
        return None

    def _savepoint_cmd(self, kind, name):
        if not self.explicit:
            self._done(f"error: {kind.upper()} can only be used in transaction blocks")
            return
        if kind == "savepoint":
            if self.state == "failed":
                self._done("error: current transaction is aborted")
                return
            self.savepoints.append((name, dict(self.pending), set(self.locks.held_by(self.sid))))
            self._done("ok")
            return
        i = self._find(name)
        if i is None:
            self._done(f"error: savepoint {name} does not exist")
            if self.state == "in_txn":
                self.state = "failed"
            return
        if kind == "release":
            if self.state == "failed":
                self._done("error: current transaction is aborted")
                return
            del self.savepoints[i:]
            self._done("ok")
            return
        _, pending, held = self.savepoints[i]
        del self.savepoints[i + 1:]
        self.pending = dict(pending)
        # Locks acquired after the savepoint belong to the subtransaction and
        # are dropped with it; earlier locks stay with the outer transaction.
        self.locks.release_resources(
            self.sid, [r for r in self.locks.held_by(self.sid) if r not in held])
        self.state = "in_txn"
        self._done("ok")

    # transaction end -------------------------------------------------------
    def commit(self, record=True):
        failed = self.state == "failed"
        if not failed:
            for (table, key), value in self.pending.items():
                self.db.setdefault(table, {})[key] = value
        self.pending = {}
        self.savepoints = []
        self.locks.release(self.sid)
        self.state, self.explicit = "idle", False
        if record:
            self._done("rolled back" if failed else "ok")

    def rollback(self):
        self.pending = {}
        self.savepoints = []
        self.locks.release(self.sid)
        self.state, self.explicit = "idle", False

    # interruption ----------------------------------------------------------
    def interrupt(self, reason):
        """Statement cancelled by the operator or hit statement_timeout.

        The interrupted statement always fails.  Waiting for a lock counts as
        part of the statement, so a queued request is withdrawn first; it must
        never be granted later to a session that has moved on.  Outside an
        explicit transaction the whole implicit transaction is aborted, which
        drops every lock it took.  Inside a transaction block the transaction
        enters the failed state: later statements are rejected until
        ROLLBACK (or a savepoint rollback) ends the failure, and a COMMIT in
        that state rolls the whole transaction back.  Locks already granted
        to the transaction stay held until it ends, as in PostgreSQL.
        """
        if self.stmt is None:
            return
        if self.waiting is not None:
            self.locks.cancel_wait(self.sid, self.waiting)
            self.waiting = None
        self._done(reason)
        if not self.explicit:
            self._abort_implicit()
        else:
            self.state = "failed"

    def _abort_implicit(self):
        self.pending = {}
        self.savepoints = []
        self.locks.release(self.sid)
        self.state, self.explicit = "idle", False

    def disconnect(self):
        """Client gone: abort any transaction and free everything."""
        if self.waiting is not None:
            self.locks.cancel_wait(self.sid, self.waiting)
            self.waiting = None
        self.pending = {}
        self.savepoints = []
        self.locks.cancel_wait(self.sid)
        self.locks.release(self.sid)
        self.state = "closed"
        self.explicit = False
        self.stmt, self.steps = None, []
        self.script = []
