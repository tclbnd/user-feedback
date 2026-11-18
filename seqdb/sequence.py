"""Cached sequences.

Each sequence hands out values from an in-memory block of ``cache`` numbers
so that taking a number does not touch the disk.  The block boundary is kept
in the sequence's metadata file ``seq.<name>``.
"""
import json

CHECKPOINT_EVERY = 50


class Sequence:
    def __init__(self, disk, name, cache=20, start=1):
        self.disk = disk
        self.name = name
        self.cache = cache
        self.start = start
        self.ops = 0
        meta = self._load()
        self.reserved = meta.get("reserved", start - 1)
        self.current = self.reserved

    @property
    def path(self):
        return f"seq.{self.name}"

    def _load(self):
        raw = self.disk.read(self.path)
        return json.loads(raw) if raw else {}

    def _store(self):
        self.disk.write(self.path, json.dumps({"reserved": self.reserved, "cache": self.cache}))

    def nextval(self):
        if self.current >= self.reserved:
            self.reserved = self.current + self.cache
            self._store()
        self.current += 1
        self.ops += 1
        if self.ops % CHECKPOINT_EVERY == 0:
            self.checkpoint()
        return self.current

    def checkpoint(self):
        self._store()
        self.disk.sync(self.path)

    def snapshot(self):
        return self.current

    def restore(self, value):
        self.current = value
