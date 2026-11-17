"""Partitioned (grace) hash join for a build side over the memory quota.

Both inputs are split into ``fanout`` files keyed by a level-seeded hash.
Each partition is then joined independently: in memory when its build side
fits, or split again with the level incremented when it does not. Rows with
a NULL key never reach a partition because NULL never matches anything;
outer-join null fills are produced while splitting instead.
"""
import hashlib
import json
import os
import shutil
import tempfile

from hashjoin import InMemoryJoin, key_of


class DepthExceeded(Exception):
    """A partition still overflows work_mem once max-depth is reached."""


def partition_of(level, key, fanout):
    digest = hashlib.sha1(
        (str(level) + ":" + json.dumps(key, ensure_ascii=False)).encode()
    ).hexdigest()
    return int(digest, 16) % fanout


class PartitionedJoin:
    def __init__(self, kind, left_col, right_col, work_mem, fanout,
                 max_depth, emit, tmpdir=None):
        self.kind = kind
        self.left_col = left_col
        self.right_col = right_col
        self.work_mem = work_mem
        self.fanout = fanout
        self.max_depth = max_depth
        self.emit = emit
        self.tmp_parent = tmpdir
        self.tmpdir = None
        self._split_seq = 0
        self.spilled_partitions = 0
        self.max_depth_seen = 0

    def run(self, left_path, right_path):
        """Split both inputs at level 1 and join every partition."""
        self.tmpdir = tempfile.mkdtemp(prefix="hashjoin-", dir=self.tmp_parent)
        try:
            parts, probe_nulls = self._split(
                right_path, left_path, level=1, handle_nulls=True
            )
            self._handle_probe_nulls(probe_nulls)
            self._join_level(parts, level=1)
        finally:
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- partitioning ------------------------------------------------------

    def _new_file(self, tag):
        self._split_seq += 1
        path = os.path.join(self.tmpdir, "%04d-%s.jsonl" % (self._split_seq, tag))
        return open(path, "w", encoding="utf-8")

    def _split(self, build_path, probe_path, level, handle_nulls):
        """Split a build/probe pair into fanout child partitions.

        Returns a list of ``(index, build_path|None, probe_path|None,
        build_count)`` tuples and the list of NULL-keyed probe rows (only
        populated for the root split). Build partitions that stay empty do
        not get a file and do not count as spilled partitions.
        """
        b_files, p_files = {}, {}
        b_counts = [0] * self.fanout
        probe_nulls = []
        try:
            for row in read_rows(build_path):
                key = key_of(row, self.right_col)
                if key is None:
                    # NULL build rows cannot match; right join fills them.
                    if self.kind == "right":
                        self.emit(dict(l=None, r=row))
                    continue
                idx = partition_of(level, key, self.fanout)
                fh = b_files.get(idx)
                if fh is None:
                    fh = self._new_file("b%d-%d" % (level, idx))
                    b_files[idx] = fh
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                b_counts[idx] += 1

            for row in read_rows(probe_path):
                key = key_of(row, self.left_col)
                if key is None:
                    if handle_nulls:
                        probe_nulls.append(row)
                    continue
                idx = partition_of(level, key, self.fanout)
                fh = p_files.get(idx)
                if fh is None:
                    fh = self._new_file("p%d-%d" % (level, idx))
                    p_files[idx] = fh
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        finally:
            for fh in b_files.values():
                fh.close()
            for fh in p_files.values():
                fh.close()

        parts = []
        for idx in range(self.fanout):
            count = b_counts[idx]
            if count:
                self.spilled_partitions += 1
            build_file = b_files[idx].name if idx in b_files else None
            probe_file = p_files[idx].name if idx in p_files else None
            if build_file is None and probe_file is None:
                continue
            parts.append((idx, build_file, probe_file, count))
        if parts:
            self.max_depth_seen = max(self.max_depth_seen, level)
        return parts, probe_nulls

    def _handle_probe_nulls(self, probe_nulls):
        """Outer fills for probe rows whose key is NULL (root split only)."""
        if self.kind == "left":
            for row in probe_nulls:
                self.emit(dict(l=row, r=None))
        elif self.kind == "anti":
            for row in probe_nulls:
                self.emit(row)
        # inner/right/semi: NULL-keyed probe rows never produce output.

    # -- recursive joining -------------------------------------------------

    def _join_level(self, parts, level):
        for _idx, build_path, probe_path, build_count in parts:
            if build_path is None:
                # No build rows in this partition: nothing can match.
                self._probe_only_partition(probe_path)
                continue
            if build_count > self.work_mem:
                if level >= self.max_depth:
                    raise DepthExceeded(
                        "partition at depth %d still holds %d build rows, "
                        "over work_mem=%d" % (level, build_count, self.work_mem)
                    )
                children, nulls = self._split(
                    build_path, probe_path, level + 1, handle_nulls=False
                )
                # Only the root split can see NULL keys; none remain here.
                assert not nulls
                self._join_level(children, level + 1)
            else:
                self._join_in_memory(build_path, probe_path)

    def _probe_only_partition(self, probe_path):
        if probe_path is None:
            return
        if self.kind == "left":
            for row in read_rows(probe_path):
                self.emit(dict(l=row, r=None))
        elif self.kind == "anti":
            for row in read_rows(probe_path):
                self.emit(row)

    def _join_in_memory(self, build_path, probe_path):
        joiner = InMemoryJoin(
            self.kind, self.left_col, self.right_col, self.work_mem, self.emit
        )
        probe = read_rows(probe_path) if probe_path is not None else ()
        joiner.run(read_rows(build_path), probe)


def read_rows(path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)
