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

## 修复后实现（当前 `replay_audit.py`）

上述四个缺陷已全部修复，重放引擎为纯标准库、无网络/无时钟/无随机源：

1. **确定性重放**：事件一律按 `seq` 排序后执行；`fetch` 只读 `snapshots.fetch`（无快照时回退事件记录值并报 `missing-snapshot`）；`ts`/`random` 事件只透传不生成；未知 `kind` 记 `unknown-kind` 后继续。
2. **版本语义**：事件级 `code_version` 覆盖后续所有事件，`normalize_title` 按 v1/v2/v3 规则处理；`version_ranges` 输出实际生效的连续版本区间，其中 `switched:true` 标记经显式切换进入的区间。
3. **按 id 对齐 + 字段级归因**：`emit` 记录与 `recorded_outputs` 按 `id` 对齐（不再按位置），每个不一致字段独立归因，主因按优先级
   `input-drift > code-change > order-sensitive > real-defect` 选取，证据写入 `detail.fields`：
   - `input-drift`：给出 `fetch_seq`、事件 `recorded_response` 与 `snapshot_value` 对照；
   - `code-change`：给出 `active_version`、`switch_at_seq`、`base_version`，定位到版本区间；
   - `order-sensitive`：基于**原始日志顺序**计算逆序对，给出上游 parse 参与的 `inversion` 区间；
   - `real-defect`：快照与版本均相同仍不一致（含 `emit-missing`/`emit-extra`）。
4. **截断检测**：事件不构成连续 `0..n` 时报 `{"seq":-1,"reason":"truncated-log","missing":[[起,止]...]}`，缺失段压缩为区间；悬空引用另报 `missing-ref`。
5. **分块重放一致性**：`replay_events(events, snapshots, version, state)` 以显式 state 串联，`run_chunked(session, chunk_size)` 在块大小 1/2/3/5/999 下与整体重放结果逐字段相等（`run_tests.py` 自动校验）。

### 用法

```bash
python3 make_fixtures.py            # 生成 8 个会话到 fixtures/
python3 run_tests.py                # 62 项验收检查 + 写 outputs/report.md
python3 replay_audit.py session.json output.json [chunk_size]
```

### 夹具与验收对照

| 会话 | 注入差异 | 期望归因 / 错误 |
| --- | --- | --- |
| S0_clean | 无 | diff 为空集、errors 为空 |
| S1_code_change | v1→v2 切换后 title 规则变化 | `code-change` @ seq5，区间 v2[3..5] |
| S2_input_drift | 快照响应与事件记录值不同 | `input-drift` @ seq2，双值对照证据 |
| S3_order_sensitive | 日志顺序与 seq 依赖顺序冲突 | `order-sensitive` @ seq5，逆序对 [1,4] |
| S4_real_defect | 错误字段 + 多 emit + 缺 emit | 3 条 `real-defect`（mismatch/extra/missing） |
| S5_code_change_v3 | v1→v3 切换，空 title 变 null | `code-change` @ seq5，区间 v3[3..5] |
| S6_truncated | seq 2..3 缺失 | `truncated-log` missing [[2,3]] + `missing-ref` |
| S7_unknown_kind | 未知事件类型 | `unknown-kind` @ seq1，重放继续、diff 为空 |

另外 `run_tests.py` 动态注入一次版本切换（验证归因到版本区间、切换前事件不受影响）与两种日志截断（验证缺失段定位），并对每个会话做 5 种块大小的分块/整体等价性校验。
