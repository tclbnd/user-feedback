#!/usr/bin/env python3
"""Human-readable demo of layer selection and reclamation per compaction."""
from __future__ import annotations

from lake.runner import config_preset, run_workload
from lake.workload import WorkloadSpec, generate_ops


def main() -> int:
    ops = generate_ops(WorkloadSpec(seed=7))
    result = run_workload(
        ops, config_preset("compact4"), seed=11, mode="concurrent"
    )
    print("== 逐次压实选层与回收统计 ==")
    header = (
        "idx  Ls->Lt  inputs -> outputs  recIn recOut dup tomb "
        "reclaimedBytes  watermark"
    )
    print(header)
    for idx, rep in enumerate(result.compaction_reports):
        print(
            "%3d  %d->%-3d  %s -> %s  %5d %5d %3d %4d  %13d  %9d"
            % (
                idx,
                rep["source_level"],
                rep["target_level"],
                rep["input_files"],
                rep["output_files"],
                rep["records_input"],
                rep["records_output"],
                rep["duplicates_dropped"],
                rep["tombstones_dropped"],
                rep["bytes_reclaimed_now"],
                rep["watermark"],
            )
        )
    s = result.summary
    print()
    print("== 摘要 ==")
    for key in (
        "compactions",
        "aborts",
        "watermark",
        "physical_bytes",
        "live_logical_bytes",
        "space_amplification",
        "write_amplification",
        "read_mismatches",
        "kv_match",
    ):
        print("%-22s %s" % (key, s[key]))
    print("stats", s["stats"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
