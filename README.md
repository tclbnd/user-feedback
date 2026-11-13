# 采集会话确定性回放与差异归因

Python 3.9+，标准库。`python3 replay_audit.py session.json output.json`

session.json 描述一次采集会话的事件日志：

```json
{"session":"s1",
 "code_version":"v1",
 "events":[
   {"seq":0,"kind":"fetch","url":"a","response":"X1","ts":100},
   {"seq":1,"kind":"parse","input_ref":0,"fields":{"title":"T"}},
   {"seq":2,"kind":"emit","record":{"id":"r1","title":"T"}},
   ...],
 "snapshots":{"fetch":{"a":"X1"}},
 "recorded_outputs":[{"id":"r1","title":"T"}]}
```

`code_version` 全局版本；事件可携带 `"code_version":"v2"` 覆盖后续事件使用的处理代码。版本差异：v1 的 `title` 原样保留；v2 将 `title` trim 并小写；v3 额外把空 title 记为 `null`。

回放规则：按 `seq` 顺序执行。`fetch` 从 `snapshots.fetch[url]` 取记录值（**不真正请求**）；`parse` 用事件时生效的版本规则处理 `snapshots` 里 `input_ref` 指向的 fetch 响应 `fields`；`emit` 产出一条记录到 `replayed_outputs`。`ts`/`random` 一律取记录值。`kind` 未知 → `errors` 记录 `{"seq","reason":"unknown-kind"}` 并继续。

输出 output.json：

```json
{"replayed_outputs":[...],
 "diff":[{"seq":n,"kind":"emit-mismatch|emit-extra|emit-missing","cause":"code-change|input-drift|order-sensitive|real-defect","detail":"..."}],
 "errors":[...],"version_ranges":[{"from_seq":0,"to_seq":k,"version":"v1"}...]}
```

归因规则：`emit` 记录与 recorded_outputs 同 id 比对。字段不一致且该字段途经版本切换点之后 → `code-change`；若 `snapshots` 中响应与事件 `response` 记录值不同 → `input-drift`；两者都无 → `real-defect`；recorded 有 replay 无 → `emit-missing`（real-defect），反之 `emit-extra`。会话截断（事件非 0..n 连续）→ `errors` 追加 `{"seq":-1,"reason":"truncated-log","missing":[seq...]}`。

现有实现缺陷：只用 v1 规则（忽略事件级 code_version）、diff 一律标 real-defect、把 replayed 与 recorded 按列表位置而非 id 对齐、不检测截断。

## 修复后的实现要点（replay_audit.py）

- 按 `seq` 排序后做**确定性左折叠**重放；`fetch` 只读 `snapshots.fetch`（不发起请求），
  事件自带 `response` 仅用于漂移比对；`ts`/`random` 不重新生成。
- 版本规则：v1 原样；v2 `strip().lower()`；v3 额外把空 title 记为 `null`。
  事件级 `code_version` 自该事件起生效，并输出完整 `version_ranges`。
- 比对按记录 **id 对齐**。字段级归因优先级：
  `code-change`（字段途经版本切换点之后，证据含切换 seq 与版本区间）
  > `input-drift`（证据含 fetch seq/url 及 recorded 与 snapshot 两份值）
  > `order-sensitive`（同 id 集合同载荷、仅发射顺序不同，且物理日志顺序与 seq 顺序不一致）
  > `real-defect`（含 emit-missing / emit-extra）。
- 完整性：seq 非 0..n 连续 → `truncated-log` 并给出 `missing` 列表；
  重复 seq → `duplicate-seq`；未知 kind → `unknown-kind` 且继续重放。
- 重放是纯 fold，`--chunks N` 将排序后的事件流切成 N 个连续块、串行折叠同一状态，
  结果与整体重放严格一致（自测对多种块大小逐字节断言）。

## 自测与复跑

```bash
python3 selftest/make_fixtures.py     # 生成 10 个本地合成会话到 selftest/fixtures/
python3 selftest/run_selftest.py      # 端到端断言（124 项检查，全部通过）
python3 replay_audit.py selftest/fixtures/d1_code_change.json out.json
python3 replay_audit.py selftest/fixtures/d1_code_change.json out.json --chunks 3
```

夹具矩阵与每条预期归因见 `selftest/README.md`。
