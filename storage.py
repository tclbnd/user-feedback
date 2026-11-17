"""Data file of fixed pages, the write-ahead log, and redo recovery."""
import json
import os
import struct

PAGE, SLOTS, WIDTH = 64, 4, 13


def encode(lsn, slots):
    body = b"".join(bytes([len(v.encode())]) + v.encode().ljust(WIDTH, b"\0") for v in slots)
    return struct.pack(">Q", lsn) + body


def decode(raw):
    raw = raw.ljust(PAGE, b"\0")
    slots = [raw[8 + i * 14 + 1:8 + i * 14 + 1 + raw[8 + i * 14]].decode() for i in range(SLOTS)]
    return struct.unpack(">Q", raw[:8])[0], slots


class Disk:
    def __init__(self, path):
        self.path = path
        if not os.path.exists(path):
            open(path, "wb").close()

    def read(self, pid):
        with open(self.path, "rb") as fh:
            fh.seek(pid * PAGE)
            return decode(fh.read(PAGE))

    def write(self, pid, lsn, slots):
        with open(self.path, "r+b") as fh:
            fh.seek(pid * PAGE)
            fh.write(encode(lsn, slots))

    def count(self):
        return os.path.getsize(self.path) // PAGE


class Wal:
    """Records are buffered in memory; `flush(upto)` makes every record with lsn <= upto durable."""

    def __init__(self, path):
        self.path, self.buffer = path, []
        records = self.durable()
        self.flushed = records[-1]["lsn"] if records else 0
        self.next_lsn = self.flushed + 1

    def append(self, record):
        record = {"lsn": self.next_lsn, **record}
        self.next_lsn += 1
        self.buffer.append(record)
        return record["lsn"]

    def flush(self, upto=None):
        upto = self.next_lsn if upto is None else upto
        if upto <= self.flushed:
            return
        out = [r for r in self.buffer if r["lsn"] < upto]
        self.buffer = [r for r in self.buffer if r["lsn"] >= upto]
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in out)
            fh.flush()
            os.fsync(fh.fileno())
        self.flushed = max(self.flushed, max([r["lsn"] for r in out], default=self.flushed))

    def durable(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.endswith("\n")]


def recover(directory):
    disk, wal = Disk(os.path.join(directory, "data.db")), Wal(os.path.join(directory, "wal.log"))
    records = wal.durable()
    checkpoints = [r["redo_lsn"] for r in records if r.get("type") == "checkpoint"]
    start = checkpoints[-1] if checkpoints else 1
    redone = 0
    for r in records:
        if r["lsn"] < start or "page" not in r:
            continue
        lsn, slots = disk.read(r["page"])
        if lsn < r["lsn"]:
            slots[r["slot"]] = r["value"]
            disk.write(r["page"], r["lsn"], slots)
            redone += 1
    return {"redo_lsn": start, "redone": redone}
