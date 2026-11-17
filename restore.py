"""Point-in-time restore: newest usable base backup plus the archived log in commit order."""
import json
import os
import shutil

import wal


class RestoreError(Exception):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = {"error": message, **details}


def load_backups(cluster):
    root = os.path.join(cluster, "backups")
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        path = os.path.join(root, name)
        try:
            with open(os.path.join(path, "manifest.json"), encoding="utf-8") as fh:
                manifest = json.load(fh)
        except (OSError, ValueError):
            # A backup without a usable manifest cannot be proven finished, so
            # it is never a candidate.
            continue
        if isinstance(manifest, dict):
            out.append({**manifest, "name": name, "path": path})
    return out


def _finished(backup):
    if not all(isinstance(backup.get(field), int) and not isinstance(backup.get(field), bool)
               for field in ("start_lsn", "start_time", "redo_lsn", "end_lsn", "end_time")):
        return False
    # A coherent backup starts before it ends.  `redo_lsn` is at or after
    # `start_lsn` (the BEGIN of the oldest transaction open at that point).
    return (backup["start_lsn"] <= backup["end_lsn"]
            and backup["start_time"] <= backup["end_time"]
            and backup["redo_lsn"] <= backup["end_lsn"] + 1)


def cut_point(records, target):
    """Highest LSN that belongs to the restore (0 when nothing does)."""
    kind, value = target
    if kind == "time":
        # Include exactly the transactions that committed at or before the
        # target time.  A transaction still open at the target is left out:
        # its commit record (and therefore its writes) is outside the cut.
        return max([lsn for lsn, rec in records.items() if rec["time"] <= value], default=0)
    commits = sorted(lsn for lsn, rec in records.items()
                     if rec["type"] == "COMMIT" and rec["tx"] == value)
    if not commits:
        raise RestoreError("transaction not found", tx=value)
    return commits[0] if kind == "tx" else commits[0] - 1


def choose_backup(backups, target, cut):
    kind, value = target
    finished = [b for b in backups if _finished(b)]
    if kind == "time":
        # The base data was copied before the target: its last commit (at
        # end_time) is at or before the target time.
        usable = [b for b in finished if b["end_time"] <= value]
        key = lambda b: (b["end_time"], b["end_lsn"])  # noqa: E731
    else:
        # Transaction targets are cut by LSN, so the backup must not have
        # copied past the cut either.
        usable = [b for b in finished if b["end_lsn"] <= cut]
        key = lambda b: (b["end_lsn"], b["end_time"])  # noqa: E731
    if not usable:
        raise RestoreError("no usable backup")
    # The newest usable backup is the one that leaves the least to replay.
    return max(usable, key=key)


def _tail_gap(scan, backups):
    """Gap implied when a finished backup names records past the archive end.

    A backup's manifest records the last LSN it copied.  If the archive stops
    earlier than that (with no other sign of a gap), the tail of the log was
    lost even though no surviving segment name advertises it.
    """
    if scan.gap is not None or not backups:
        return None
    ends = [b["end_lsn"] for b in backups if _finished(b)]
    if not ends:
        return None
    needed = max(ends)
    last = max(scan.records) if scan.records else 0
    if last >= needed:
        return None
    latest = scan.records[last]["time"] if scan.records else None
    return wal.Gap(last + 1, latest)


def _latest_time(records, before_lsn):
    return max((rec["time"] for lsn, rec in records.items() if lsn < before_lsn), default=None)


def _log_gap(scan, missing_from):
    """Report the first untrustworthy position and the last recoverable time."""
    latest = _latest_time(scan.records, missing_from)
    raise RestoreError("log gap", missing_from=missing_from,
                       latest_recoverable_time=latest)


def _check_target_gap(scan, gap, target):
    """Refuse targets whose committed data may sit behind the gap."""
    if gap is None:
        return
    kind, value = target
    if kind == "time":
        latest = gap.latest_recoverable_time
        # Times increase strictly, so every commit at or before the last
        # record before the gap is present; anything later may be hidden.
        if latest is None or value > latest:
            _log_gap(scan, gap.missing_from)
    elif not any(rec["type"] == "COMMIT" and rec["tx"] == value
                 for rec in scan.records.values()):
        # Report the gap instead of a misleading "transaction not found".
        _log_gap(scan, gap.missing_from)


def _check_replay_range(scan, backup, cut):
    """Every record the replay reads must be present exactly once."""
    for lsn in range(backup["redo_lsn"], cut + 1):
        if lsn not in scan.records:
            _log_gap(scan, lsn)


def replay(tables, records, backup, cut):
    start = backup["start_lsn"]
    writes, commits = {}, {}
    for lsn in sorted(records):
        if lsn < backup["redo_lsn"] or lsn > cut:
            continue
        rec = records[lsn]
        tx = rec["tx"]
        if rec["type"] in ("PUT", "DEL"):
            writes.setdefault(tx, []).append(rec)
        elif rec["type"] == "COMMIT":
            commits[tx] = lsn
        elif rec["type"] == "ABORT":
            writes.pop(tx, None)
            commits.pop(tx, None)
    # Writes take effect at COMMIT, in commit order.  Commits at or before
    # start_lsn are already contained in the base backup.  Re-applying commits
    # that the fuzzy backup may already hold is harmless: the whole commit
    # range is replayed in the same order.
    for tx, _lsn in sorted(commits.items(), key=lambda item: item[1]):
        if _lsn <= start:
            continue
        for write in writes.get(tx, ()):
            rows = tables.setdefault(write["table"], {})
            if write["type"] == "PUT":
                rows[write["key"]] = write["value"]
            else:
                rows.pop(write["key"], None)
    return {name: rows for name, rows in sorted(tables.items()) if rows}


def verify(tables, records, backup, cut):
    """Re-derive the log-determined rows and compare them with a copy.

    Commits at or before `start_lsn` are already in the base backup.  For
    every key touched by a later commit in replay range the log alone says
    what the final value must be, so the rebuilt copy can be checked before
    it replaces the live one.
    """
    writes, commits = {}, {}
    for lsn in sorted(records):
        rec = records[lsn]
        if not backup["redo_lsn"] <= lsn <= cut:
            continue
        if rec["type"] in ("PUT", "DEL"):
            # Writes of a transaction open at start_lsn may sit before it.
            writes.setdefault(rec["tx"], []).append(rec)
        elif rec["type"] == "COMMIT":
            if lsn > backup["start_lsn"]:
                commits[rec["tx"]] = lsn
        elif rec["type"] == "ABORT":
            writes.pop(rec["tx"], None)
            commits.pop(rec["tx"], None)
    expected = {}
    for tx, _lsn in sorted(commits.items(), key=lambda item: item[1]):
        for write in writes.get(tx, ()):
            expected[(write["table"], write["key"])] = (
                write["value"] if write["type"] == "PUT" else None)
    for (table, key), value in expected.items():
        rows = tables.get(table, {})
        if value is None:
            if key in rows:
                raise RestoreError("restore check failed", table=table, key=key)
        elif rows.get(key) != value:
            raise RestoreError("restore check failed", table=table, key=key)


def install(out, tables, check=None):
    """Build <out>.restoring, read it back, then replace <out> as a whole."""
    temp = out + ".restoring"
    previous = out + ".previous"
    shutil.rmtree(temp, ignore_errors=True)
    os.makedirs(temp)
    moved_previous = False
    try:
        with open(os.path.join(temp, "data.json"), "w", encoding="utf-8") as fh:
            json.dump({"tables": tables}, fh, indent=1, sort_keys=True)
        # Read the temporary copy back and check it holds exactly what was
        # reconstructed before it is allowed to replace the live copy.
        with open(os.path.join(temp, "data.json"), encoding="utf-8") as fh:
            checked = json.load(fh).get("tables")
        if not isinstance(checked, dict) or checked != tables:
            raise RestoreError("restore check failed")
        for name, rows in checked.items():
            if not isinstance(rows, dict):
                raise RestoreError("restore check failed", table=name)
        if check is not None:
            check(checked)
        shutil.rmtree(previous, ignore_errors=True)
        if os.path.exists(out):
            os.replace(out, previous)
            moved_previous = True
        os.replace(temp, out)
    except Exception as exc:
        # Never leave a half-built copy behind, and put back whatever was
        # there before if the swap did not happen.
        shutil.rmtree(temp, ignore_errors=True)
        if moved_previous and not os.path.exists(out):
            try:
                os.replace(previous, out)
            except OSError:
                pass
        if isinstance(exc, OSError):
            raise RestoreError("install failed") from None
        raise
    shutil.rmtree(previous, ignore_errors=True)


def _scan(records):
    if isinstance(records, wal.Scan):
        return records
    # Callers that hand over a plain mapping get no gap information; the
    # replay-range check still notices missing LSNs.
    return wal.Scan(dict(records), None)


def restore(cluster, out, target, records):
    backups = load_backups(cluster)
    scan = _scan(records)
    # The archive's own gap wins: it is the first position that cannot be
    # trusted.  Only when the archive looks complete do we check whether a
    # finished backup knows about records the archive no longer holds.
    gap = scan.gap or _tail_gap(scan, backups)
    _check_target_gap(scan, gap, target)
    cut = cut_point(scan.records, target)
    backup = choose_backup(backups, target, cut)
    _check_replay_range(scan, backup, cut)
    with open(os.path.join(backup["path"], "data.json"), encoding="utf-8") as fh:
        tables = json.load(fh)["tables"]
    tables = replay(tables, scan.records, backup, cut)
    install(out, tables, check=lambda rebuilt: verify(rebuilt, scan.records, backup, cut))
    return {"backup": backup["name"], "cut_lsn": cut}
