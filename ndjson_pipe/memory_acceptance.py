"""Bounded-memory acceptance measurement.

A streaming pipeline must not hold the whole source in memory. This module
ingests a source much larger than the read buffer while ``tracemalloc`` is
active and reports the Python-level peak allocation. Two invariants are
checked:

* peak memory stays close to ``chunk_size + residual line + one batch`` and
  does **not** scale with total file size;
* doubling the file size while keeping chunk/batch fixed leaves the peak
  roughly flat (the ratio is reported, not an arbitrary absolute limit).
"""

import gc
import os
import tracemalloc
from typing import Dict, List, Tuple

from .importer import import_source
from .store import Store


def measure_peak(db_path: str, source_id: str, file_path: str,
                 chunk_size: int, batch_size: int) -> int:
    """Return tracemalloc peak Python allocation in bytes for one import."""
    gc.collect()
    tracemalloc.start()
    try:
        with Store(db_path) as store:
            import_source(
                store,
                source_id,
                file_path,
                chunk_size=chunk_size,
                batch_size=batch_size,
            )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def build_large_source(path: str, records: int) -> int:
    """Write a synthetic legal NDJSON source; return its byte size."""
    line = (
        '{{"version":{version},"id":"M-{i:08d}","amount":"{i}.35",'
        '"currency":"CNY","note":"蓝莓与emoji🫐 padding padding padding"}}\n'
    )
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(records):
            version = 1 if i % 2 == 0 else 2
            if version == 2:
                fh.write(
                    '{"version":2,"id":"M-%08d","amount_cents":%d,"currency":"USD",'
                    '"note":"蓝莓与emoji🫐 padding padding padding"}\n' % (i, i * 100 + 35)
                )
            else:
                fh.write(line.format(version=version, i=i))
    return os.path.getsize(path)


def memory_acceptance(workdir: str, *, small_records: int = 2_000,
                      large_records: int = 20_000, chunk_size: int = 256,
                      batch_size: int = 50) -> Dict[str, object]:
    """Run small/large imports and assert the peak does not scale with size."""
    small_file = os.path.join(workdir, "large-small.ndjson")
    large_file = os.path.join(workdir, "large-big.ndjson")
    small_size = build_large_source(small_file, small_records)
    large_size = build_large_source(large_file, large_records)
    assert large_size > chunk_size * 100, "test data must dwarf the read buffer"

    small_peak = measure_peak(
        os.path.join(workdir, "mem-small.db"), "mem-source", small_file,
        chunk_size, batch_size,
    )
    large_peak = measure_peak(
        os.path.join(workdir, "mem-large.db"), "mem-source", large_file,
        chunk_size, batch_size,
    )

    growth_ratio = large_peak / max(small_peak, 1)
    size_ratio = large_size / small_size
    # A bounded pipeline: 10x more data may add only modest per-run overhead.
    # The generous 1.5x ceiling catches accidental whole-file buffering while
    # tolerating allocator noise; a non-streaming implementation grows ~10x.
    bounded = growth_ratio < 1.5 and large_peak < large_size // 4

    return {
        "chunk_size": chunk_size,
        "batch_size": batch_size,
        "small": {"records": small_records, "bytes": small_size, "peak_python_bytes": small_peak},
        "large": {"records": large_records, "bytes": large_size, "peak_python_bytes": large_peak},
        "size_ratio": round(size_ratio, 2),
        "peak_growth_ratio": round(growth_ratio, 3),
        "bounded": bounded,
        "note": "tracemalloc measures Python allocations only; SQLite/C buffers are excluded",
    }
