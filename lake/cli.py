"""Command line adapter.

Accepts the legacy request schema documented in README.md plus optional engine
configuration, and emits a JSON report containing layer state, watermark,
per-compaction selection/reclamation statistics, replayable decisions, reads
and a full-state digest.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from .model import Config
from .runner import config_preset, replay_workload, run_workload
from .workload import WorkloadSpec, generate_ops


def _config_from_request(req: Dict[str, Any]) -> Tuple[Config, str]:
    if "preset" in req:
        name = req["preset"]
        return config_preset(name), name
    levels = int(req.get("levels", 4))
    caps = req.get("cap") or req.get("file_caps")
    if not caps:
        default_caps = {3: (3, 4, 8), 4: (4, 4, 8, 16), 5: (3, 4, 6, 10, 20)}
        caps = default_caps.get(levels, tuple(4 for _ in range(levels)))
    if len(caps) != levels:
        raise ValueError("cap length %d != levels %d" % (len(caps), levels))
    config = Config(
        levels=levels,
        file_caps=tuple(caps),
        memtable_records=int(req.get("memtable_records", 4)),
        target_file_records=int(req.get("target_file_records", 20)),
        max_concurrent_compactions=int(
            req.get("max_concurrent_compactions", 2)
        ),
        max_compaction_inputs=int(req.get("max_compaction_inputs", 32)),
    )
    return config, "custom"


def _ops_from_request(req: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "ops" in req:
        return req["ops"]
    spec = WorkloadSpec(
        n_ops=int(req.get("n_ops", 240)),
        key_space=int(req.get("key_space", 24)),
        seed=int(req.get("seed", 7)),
    )
    return generate_ops(spec)


def _legacy_levels_view(result) -> List[Dict[str, List[List[Any]]]]:
    # Mirror the baseline output shape {key: [[seq, value]]} for familiarity.
    view = []
    for level_files in result.engine.manifest.levels:
        per_key: Dict[str, List[List[Any]]] = {}
        for f in level_files:
            for r in f.records:
                per_key.setdefault(r.key, []).append([r.seq, r.value])
        view.append(per_key)
    return view


def run_request(req: Dict[str, Any]) -> Dict[str, Any]:
    config, preset = _config_from_request(req)
    ops = _ops_from_request(req)
    seed = int(req.get("seed", 7))
    mode = req.get("mode", "concurrent")
    strict = bool(req.get("strict_version", False))
    result = run_workload(
        ops, config, seed=seed, mode=mode, strict_version=strict
    )
    replay_digest = None
    if mode == "concurrent":
        replayed = replay_workload(
            ops, config, seed=seed, recorded=result.decisions,
            strict_version=strict,
        )
        replay_digest = replayed.engine.manifest.digest()
    report = {
        "preset": preset,
        "mode": mode,
        "seed": seed,
        "levels": _legacy_levels_view(result),
        "watermark": result.engine.manifest.watermark,
        "compactions": result.summary["compactions"],
        "reads": result.read_results,
        "errors": [],
        "digest": result.engine.manifest.digest(),
        "replay_digest": replay_digest,
        "replay_matches": (
            replay_digest == result.engine.manifest.digest()
            if replay_digest is not None
            else None
        ),
        "final_kv": result.summary["final_kv"],
        "reference_kv": result.summary["reference_kv"],
        "kv_match": result.summary["kv_match"],
        "read_mismatches": result.summary["read_mismatches"],
        "level_summary": result.engine.level_summary(),
        "stats": result.summary["stats"],
        "space_amplification": result.summary["space_amplification"],
        "write_amplification": result.summary["write_amplification"],
        "compaction_log": result.compaction_reports,
        "decisions": result.decisions,
    }
    return report


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        raise SystemExit("usage: python3 lsm.py ops.json output.json")
    with open(argv[1], "r", encoding="utf-8") as fh:
        req = json.load(fh)
    report = run_request(req)
    with open(argv[2], "w", encoding="utf-8") as fh:
        json.dump(report, fh, sort_keys=True, indent=1)
    return 0
