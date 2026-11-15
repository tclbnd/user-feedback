#!/usr/bin/env python3
"""Print the acceptance matrix: two layer shapes x multiple random seeds."""
from __future__ import annotations

import json

from lake.runner import config_preset, replay_workload, run_workload
from lake.workload import WorkloadSpec, generate_ops

CONFIGS = ["compact4", "deep5"]
SEEDS = [7, 42, 123]
SA_LIMIT = 1.2
SUMMARY_KEYS = (
    "state_digest",
    "final_kv",
    "watermark",
    "level_file_counts",
    "physical_bytes",
    "live_logical_bytes",
    "space_amplification",
    "write_amplification",
    "compactions",
    "aborts",
)


def comparable(summary):
    return {k: summary[k] for k in SUMMARY_KEYS}


def main() -> int:
    rows = []
    failures = []
    for name in CONFIGS:
        for seed in SEEDS:
            ops = generate_ops(WorkloadSpec(seed=seed))
            config = config_preset(name)
            serial = run_workload(ops, config, seed=seed, mode="serial")
            concurrent = run_workload(
                ops, config, seed=seed, mode="concurrent"
            )
            replayed = replay_workload(
                ops, config, seed=seed, recorded=concurrent.decisions
            )
            rerun = run_workload(
                ops, config, seed=seed, mode="concurrent"
            )
            checks = {
                "reads_match_serial":
                    concurrent.read_results == serial.read_results,
                "kv_match_truth": concurrent.summary["kv_match"],
                "sa_under_limit":
                    concurrent.summary["space_amplification"] <= SA_LIMIT
                    and serial.summary["space_amplification"] <= SA_LIMIT,
                "replay_digest_match":
                    replayed.engine.manifest.digest()
                    == concurrent.engine.manifest.digest(),
                "rerun_summary_match":
                    comparable(rerun.summary) == comparable(concurrent.summary),
                "no_aborts": concurrent.summary["aborts"] == 0,
            }
            row = {
                "config": name,
                "seed": seed,
                "summary": comparable(concurrent.summary),
                "checks": checks,
            }
            rows.append(row)
            for check, ok in checks.items():
                if not ok:
                    failures.append((name, seed, check))
    print(json.dumps({"rows": rows, "failures": failures}, indent=2,
                     sort_keys=True))
    if failures:
        print("ACCEPTANCE FAILED: %r" % failures)
        return 1
    print("ALL ACCEPTANCE CHECKS PASSED (%d cells)" % len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
