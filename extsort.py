"""External sort operator.

Rows are buffered up to ``work_mem`` rows, sorted and spilled to temporary
run files.  Runs are merged with a bounded fan-in, taking extra passes when
there are more runs than ``fan_in``.  A LIMIT no larger than ``work_mem`` is
served from a bounded heap and never spills.
"""
import heapq
import json
import os
import tempfile

from keys import _Reverse, row_key


class MemoryExceeded(Exception):
    pass


def _write_row(fh, row):
    fh.write(json.dumps(row, ensure_ascii=False))
    fh.write("\n")


class SortOperator:
    def __init__(self, keys, work_mem=10000, fan_in=8, limit=None, tmpdir=None):
        if work_mem < 1:
            raise ValueError("work-mem must be at least 1 row")
        if not 2 <= fan_in <= work_mem:
            raise ValueError("fan-in must be between 2 and work_mem")
        if limit is not None and limit < 0:
            raise ValueError("limit must not be negative")
        self.keys = keys
        self.work_mem = work_mem
        self.fan_in = fan_in
        self.limit = limit
        self.tmpdir = tmpdir
        self.key = row_key(keys)
        self.held = 0
        self.stats = {"rows": 0, "runs": 0, "merge_passes": 0}
        self._temp_files = []

    # -- memory accounting -------------------------------------------------
    def hold(self, n=1):
        self.held += n
        if self.held > self.work_mem:
            raise MemoryExceeded(
                "holding %d rows, work_mem is %d" % (self.held, self.work_mem)
            )

    def release(self, n=1):
        self.held -= n
        if self.held < 0:
            self.held = 0

    # -- temporary files ---------------------------------------------------
    def _new_temp(self):
        try:
            fd, path = tempfile.mkstemp(
                dir=self.tmpdir, prefix="sort-", suffix=".jsonl"
            )
        except OSError as exc:
            raise ValueError("cannot create temporary file: %s" % exc) from None
        os.close(fd)
        self._temp_files.append(path)
        return path

    def _drop(self, paths):
        for path in paths:
            if path in self._temp_files:
                self._temp_files.remove(path)
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def _cleanup(self):
        """Remove every temporary file, whatever happened."""
        self._drop(list(self._temp_files))
        self._temp_files = []
        self.held = 0

    # -- entry point -------------------------------------------------------
    def execute(self, rows):
        try:
            if self.limit is not None and self.limit <= self.work_mem:
                yield from self._top_n(rows)
            else:
                yield from self._external(rows)
        finally:
            self._cleanup()

    # -- LIMIT within the quota: bounded heap, no spill --------------------
    def _top_n(self, rows):
        limit = self.limit
        heap = []
        seq = 0
        for row in rows:
            self.stats["rows"] += 1
            seq += 1
            if limit == 0:
                continue
            key = self.key(row)
            # ``_Reverse`` turns the min-heap into a max-heap on the key, and
            # ``-seq`` makes the *latest* of several equal-key rows the root.
            # The root is therefore the worst retained row: the first one
            # evicted, which keeps the remaining equal-key rows in input order.
            item = (_Reverse(key), -seq, row)
            if len(heap) < limit:
                self.hold(1)
                heapq.heappush(heap, item)
            elif key < heap[0][0].inner:
                # Strictly better key than the worst retained row.  Equal keys
                # never evict, so a later row cannot displace an earlier one.
                heapq.heapreplace(heap, item)
        self.release(len(heap))
        # Best first, equal keys in input order.
        for item in sorted(heap, key=lambda it: (it[0].inner, -it[1])):
            yield item[2]

    # -- external sort -----------------------------------------------------
    def _external(self, rows):
        runs = []
        buffer = []
        for row in rows:
            self.stats["rows"] += 1
            if len(buffer) == self.work_mem:
                runs.append(self._spill(buffer))
                self.release(len(buffer))
                buffer = []
            self.hold(1)
            buffer.append(row)
        if not runs:
            # Everything fit: sort in memory, nothing is spilled.
            buffer.sort(key=self.key)
            self.release(len(buffer))
            if self.limit is not None:
                buffer = buffer[: self.limit]
            yield from buffer
            return
        if buffer:
            runs.append(self._spill(buffer))
            self.release(len(buffer))

        # Merge passes until the remaining runs fit in one fan-in.
        while len(runs) > self.fan_in:
            runs = self._merge_pass(runs)
            self.stats["merge_passes"] += 1
        self.stats["merge_passes"] += 1

        emitted = 0
        merged = self._merge(runs)
        try:
            for row in merged:
                yield row
                emitted += 1
                if self.limit is not None and emitted >= self.limit:
                    break
        finally:
            merged.close()

    def _spill(self, buffer):
        buffer.sort(key=self.key)
        path = self._new_temp()
        with open(path, "w", encoding="utf-8") as out:
            for row in buffer:
                _write_row(out, row)
        self.stats["runs"] += 1
        return path

    def _merge_pass(self, runs):
        """Merge consecutive groups of ``fan_in`` runs into new runs."""
        merged = []
        for start in range(0, len(runs), self.fan_in):
            group = runs[start : start + self.fan_in]
            if len(group) == 1:
                merged.append(group[0])
            else:
                merged.append(self._merge_to_run(group))
        return merged

    def _merge_to_run(self, group):
        path = self._new_temp()
        rows = self._merge(group)
        try:
            with open(path, "w", encoding="utf-8") as out:
                for row in rows:
                    _write_row(out, row)
        finally:
            rows.close()
        self._drop(group)
        return path

    @staticmethod
    def _read_row(fh):
        for line in fh:
            if line.strip():
                return json.loads(line)
        return None

    def _merge(self, runs):
        """Stable k-way merge of run files, streaming one row per run.

        Each heap entry carries ``(key, run_index, seq, row)``.  ``run_index``
        breaks ties between different runs (earlier runs hold earlier input
        rows) and ``seq`` breaks ties inside one run (rows are read in order),
        so the merge is stable without ever comparing the row payloads.
        """
        streams = []
        heap = []
        seq = 0
        try:
            for index, path in enumerate(runs):
                fh = open(path, encoding="utf-8")
                streams.append(fh)
                row = self._read_row(fh)
                if row is not None:
                    self.hold(1)
                    heapq.heappush(heap, (self.key(row), index, seq, row))
                    seq += 1
            while heap:
                _, index, _, row = heapq.heappop(heap)
                self.release(1)
                yield row
                nxt = self._read_row(streams[index])
                if nxt is not None:
                    self.hold(1)
                    heapq.heappush(heap, (self.key(nxt), index, seq, nxt))
                    seq += 1
        finally:
            for fh in streams:
                fh.close()

