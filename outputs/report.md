# Replay comparison report

- total checks: 62
- passed: 62
- failed: 0

## Per-session replay outputs

### S0_clean.json (S0-clean)

- version_ranges: `[{"from_seq": 0, "to_seq": 5, "version": "v1", "switched": false}]`
- diff count: 0

### S1_code_change.json (S1-code-change)

- version_ranges: `[{"from_seq": 0, "to_seq": 2, "version": "v1", "switched": false}, {"from_seq": 3, "to_seq": 5, "version": "v2", "switched": true}]`
- diff count: 1
  - seq 5 emit-mismatch **code-change** -- `{"id": "r2", "field_causes": ["code-change"], "fields": {"title": {"field": "title", "active_version": "v2", "switch_at_seq": 3, "base_version": "v1", "evidence": "field processed under v2 after version switch at seq 3", "replayed": "bank statement", "recorded": " Bank Statement "}}}`

### S2_input_drift.json (S2-input-drift)

- version_ranges: `[{"from_seq": 0, "to_seq": 2, "version": "v1", "switched": false}]`
- diff count: 1
  - seq 2 emit-mismatch **input-drift** -- `{"id": "d1", "field_causes": ["input-drift", "input-drift"], "fields": {"title": {"field": "title", "fetch_seq": 0, "recorded_response": {"title": "rate-A", "value": 100}, "snapshot_value": {"title": "rate-B", "value": 200}, "replayed": "rate-B", "recorded": "rate-A"}, "value": {"field": "value", "fetch_seq": 0, "recorded_response": {"title": "rate-A", "value": 100}, "snapshot_value": {"title": "rate-B", "value": 200}, "replayed": 200, "recorded": 100}}}`

### S3_order_sensitive.json (S3-order-sensitive)

- version_ranges: `[{"from_seq": 0, "to_seq": 5, "version": "v1", "switched": false}]`
- diff count: 1
  - seq 5 emit-mismatch **order-sensitive** -- `{"id": "o2", "field_causes": ["order-sensitive"], "fields": {"title": {"field": "title", "inversion": [1, 4], "evidence": "upstream parse seq 4 participates in ordering pair [1, 4]", "replayed": "Second", "recorded": null}}}`

### S4_real_defect.json (S4-real-defect)

- version_ranges: `[{"from_seq": 0, "to_seq": 3, "version": "v1", "switched": false}]`
- diff count: 3
  - seq -1 emit-missing **real-defect** -- `{"id": "x3", "evidence": "recorded output has no matching emit event"}`
  - seq 2 emit-mismatch **real-defect** -- `{"id": "x1", "field_causes": ["real-defect"], "fields": {"amount": {"field": "amount", "active_version": "v1", "evidence": "deterministic replay under identical snapshot and version still disagrees", "replayed": 50, "recorded": 999}}}`
  - seq 3 emit-extra **real-defect** -- `{"id": "x2", "evidence": "emitted record absent from recorded_outputs"}`

### S5_code_change_v3.json (S5-code-change-v3)

- version_ranges: `[{"from_seq": 0, "to_seq": 2, "version": "v1", "switched": false}, {"from_seq": 3, "to_seq": 5, "version": "v3", "switched": true}]`
- diff count: 1
  - seq 5 emit-mismatch **code-change** -- `{"id": "n2", "field_causes": ["code-change"], "fields": {"title": {"field": "title", "active_version": "v3", "switch_at_seq": 3, "base_version": "v1", "evidence": "field processed under v3 after version switch at seq 3", "replayed": null, "recorded": "   "}}}`

### S6_truncated.json (S6-truncated)

- version_ranges: `[{"from_seq": 0, "to_seq": 5, "version": "v1", "switched": false}]`
- diff count: 0
- errors:
  - `{"seq": 4, "reason": "missing-ref", "detail": "input_ref 3 does not resolve to a fetch event"}`
  - `{"seq": -1, "reason": "truncated-log", "detail": "event log is not a contiguous 0..n sequence", "missing": [[2, 3]]}`

### S7_unknown_kind.json (S7-unknown-kind)

- version_ranges: `[{"from_seq": 0, "to_seq": 3, "version": "v1", "switched": false}]`
- diff count: 0
- errors:
  - `{"seq": 1, "reason": "unknown-kind", "detail": "'telemetry'"}`

## Check results

- [PASS] S1_code_change.json diffs attributed to code-change at [5] -- [{"seq": 5, "kind": "emit-mismatch", "cause": "code-change", "detail": {"id": "r2", "field_causes": ["code-change"], "fields": {"title": {"field": "title", "active_version": "v2", "switch_at_seq": 3, "base_version": "v1", "evidence": "field processed under v2 after version switch at seq 3", "replayed": "bank statement", "recorded": " Bank Statement "}}}}]
- [PASS] S1_code_change.json every diff has evidence
- [PASS] S2_input_drift.json diffs attributed to input-drift at [2] -- [{"seq": 2, "kind": "emit-mismatch", "cause": "input-drift", "detail": {"id": "d1", "field_causes": ["input-drift", "input-drift"], "fields": {"title": {"field": "title", "fetch_seq": 0, "recorded_response": {"title": "rate-A", "value": 100}, "snapshot_value": {"title": "rate-B", "value": 200}, "replayed": "rate-B", "recorded": "rate-A"}, "value": {"field": "value", "fetch_seq": 0, "recorded_response": {"title": "rate-A", "value": 100}, "snapshot_value": {"title": "rate-B", "value": 200}, "replayed": 200, "recorded": 100}}}}]
- [PASS] S2_input_drift.json every diff has evidence
- [PASS] S3_order_sensitive.json diffs attributed to order-sensitive at [5] -- [{"seq": 5, "kind": "emit-mismatch", "cause": "order-sensitive", "detail": {"id": "o2", "field_causes": ["order-sensitive"], "fields": {"title": {"field": "title", "inversion": [1, 4], "evidence": "upstream parse seq 4 participates in ordering pair [1, 4]", "replayed": "Second", "recorded": null}}}}]
- [PASS] S3_order_sensitive.json every diff has evidence
- [PASS] S4_real_defect.json diffs attributed to real-defect at [-1, 2, 3] -- [{"seq": -1, "kind": "emit-missing", "cause": "real-defect", "detail": {"id": "x3", "evidence": "recorded output has no matching emit event"}}, {"seq": 2, "kind": "emit-mismatch", "cause": "real-defect", "detail": {"id": "x1", "field_causes": ["real-defect"], "fields": {"amount": {"field": "amount", "active_version": "v1", "evidence": "deterministic replay under identical snapshot and version still disagrees", "replayed": 50, "recorded": 999}}}}, {"seq": 3, "kind": "emit-extra", "cause": "real-defect", "detail": {"id": "x2", "evidence": "emitted record absent from recorded_outputs"}}]
- [PASS] S4_real_defect.json every diff has evidence
- [PASS] S5_code_change_v3.json diffs attributed to code-change at [5] -- [{"seq": 5, "kind": "emit-mismatch", "cause": "code-change", "detail": {"id": "n2", "field_causes": ["code-change"], "fields": {"title": {"field": "title", "active_version": "v3", "switch_at_seq": 3, "base_version": "v1", "evidence": "field processed under v3 after version switch at seq 3", "replayed": null, "recorded": "   "}}}}]
- [PASS] S5_code_change_v3.json every diff has evidence
- [PASS] S0 clean session -> empty diff set -- []
- [PASS] S0 clean session -> no errors -- []
- [PASS] S7 unknown-kind error recorded at seq 1
- [PASS] S7 replay continues -> empty diff set
- [PASS] S6 truncated-log located with missing segment [[2,3]] -- {"seq": -1, "reason": "truncated-log", "detail": "event log is not a contiguous 0..n sequence", "missing": [[2, 3]]}
- [PASS] S6 dangling parse reported as missing-ref
- [PASS] injected version switch -> code-change on affected emit -- {"seq": 5, "kind": "emit-mismatch", "cause": "code-change", "detail": {"id": "c-r2", "field_causes": ["code-change"], "fields": {"title": {"field": "title", "active_version": "v2", "switch_at_seq": 3, "base_version": "v1", "evidence": "field processed under v2 after version switch at seq 3", "replayed": "raw title", "recorded": "  Raw Title  "}}}}
- [PASS] injected switch attributed to version interval v2[3..5] -- [{"from_seq": 0, "to_seq": 2, "version": "v1"}, {"from_seq": 3, "to_seq": 5, "version": "v2"}]
- [PASS] switch evidence names switch_at_seq=3 and active v2 -- {"field": "title", "active_version": "v2", "switch_at_seq": 3, "base_version": "v1", "evidence": "field processed under v2 after version switch at seq 3", "replayed": "raw title", "recorded": "  Raw Title  "}
- [PASS] pre-switch emit (seq 2) unaffected
- [PASS] injected tail-side truncation locates missing segment [[3,3]] -- {"seq": -1, "reason": "truncated-log", "detail": "event log is not a contiguous 0..n sequence", "missing": [[3, 3]]}
- [PASS] injected middle truncation locates missing segment [[2,3]] -- {"seq": -1, "reason": "truncated-log", "detail": "event log is not a contiguous 0..n sequence", "missing": [[2, 3]]}
- [PASS] S0_clean.json chunk size 1 == whole replay
- [PASS] S0_clean.json chunk size 2 == whole replay
- [PASS] S0_clean.json chunk size 3 == whole replay
- [PASS] S0_clean.json chunk size 5 == whole replay
- [PASS] S0_clean.json chunk size 999 == whole replay
- [PASS] S1_code_change.json chunk size 1 == whole replay
- [PASS] S1_code_change.json chunk size 2 == whole replay
- [PASS] S1_code_change.json chunk size 3 == whole replay
- [PASS] S1_code_change.json chunk size 5 == whole replay
- [PASS] S1_code_change.json chunk size 999 == whole replay
- [PASS] S2_input_drift.json chunk size 1 == whole replay
- [PASS] S2_input_drift.json chunk size 2 == whole replay
- [PASS] S2_input_drift.json chunk size 3 == whole replay
- [PASS] S2_input_drift.json chunk size 5 == whole replay
- [PASS] S2_input_drift.json chunk size 999 == whole replay
- [PASS] S3_order_sensitive.json chunk size 1 == whole replay
- [PASS] S3_order_sensitive.json chunk size 2 == whole replay
- [PASS] S3_order_sensitive.json chunk size 3 == whole replay
- [PASS] S3_order_sensitive.json chunk size 5 == whole replay
- [PASS] S3_order_sensitive.json chunk size 999 == whole replay
- [PASS] S4_real_defect.json chunk size 1 == whole replay
- [PASS] S4_real_defect.json chunk size 2 == whole replay
- [PASS] S4_real_defect.json chunk size 3 == whole replay
- [PASS] S4_real_defect.json chunk size 5 == whole replay
- [PASS] S4_real_defect.json chunk size 999 == whole replay
- [PASS] S5_code_change_v3.json chunk size 1 == whole replay
- [PASS] S5_code_change_v3.json chunk size 2 == whole replay
- [PASS] S5_code_change_v3.json chunk size 3 == whole replay
- [PASS] S5_code_change_v3.json chunk size 5 == whole replay
- [PASS] S5_code_change_v3.json chunk size 999 == whole replay
- [PASS] S6_truncated.json chunk size 1 == whole replay
- [PASS] S6_truncated.json chunk size 2 == whole replay
- [PASS] S6_truncated.json chunk size 3 == whole replay
- [PASS] S6_truncated.json chunk size 5 == whole replay
- [PASS] S6_truncated.json chunk size 999 == whole replay
- [PASS] S7_unknown_kind.json chunk size 1 == whole replay
- [PASS] S7_unknown_kind.json chunk size 2 == whole replay
- [PASS] S7_unknown_kind.json chunk size 3 == whole replay
- [PASS] S7_unknown_kind.json chunk size 5 == whole replay
- [PASS] S7_unknown_kind.json chunk size 999 == whole replay
