# 自测说明（合成输入，无网络）

## 运行方式

在仓库根目录执行：

```bash
python3 selftest/make_fixtures.py     # 生成 selftest/fixtures/*.json
python3 selftest/run_selftest.py      # 端到端断言（内部会再生成一次夹具）
```

单条会话也可以直接走与生产一致的 CLI（向后兼容的两参数形式）：

```bash
python3 replay_audit.py selftest/fixtures/d1_code_change.json out.json
# 分块重放（结果必须与整体重放完全一致）
python3 replay_audit.py selftest/fixtures/d1_code_change.json out.json --chunks 3
```

## 夹具矩阵（10 个合成会话）

| 夹具 | 注入差异 / 场景 | 期望 diff cause |
| --- | --- | --- |
| `s_clean_v1` | 无差异基线（v1 原样保留） | 空集 |
| `d1_code_change` | parse 前注入 v1→v2 切换，recorded 仍为 v1 形状 | `code-change`（定位到切换点 seq 与版本区间） |
| `d2_input_drift` | snapshot 响应与事件 recorded response 不一致 | `input-drift`（定位到 fetch seq/url 与两份值） |
| `d3_order_sensitive` | 物理日志顺序与 seq 顺序不一致，载荷全同仅顺序不同 | `order-sensitive` |
| `d4_real_defect` | 无版本切换、无漂移，recorded 字段被损坏 | `real-defect` |
| `d5_missing_extra` | recorded 缺一条 + replay 多一条 | 两条 `real-defect`（emit-missing / emit-extra） |
| `s_clean_v3` | 无差异基线（v2 trim/lower + v3 空 title→null） | 空集 |
| `x_truncated` | seq 0..5 中缺失 3、4 | `errors` 含 `truncated-log` 且 `missing=[3,4]` |
| `x_unknown_kind` | 未知事件类型 | `errors` 含 `unknown-kind`，重放继续 |
| `s_clean_multi_version` | 一次 v1→v2 切换，无差异 | 空集，`version_ranges` 两段 |

另外 `run_selftest.py` 还内联覆盖：

- **归因优先级**：同一字段同时满足 code-change 与 input-drift 时，按
  code-change > input-drift > order-sensitive > real-defect 取最高优先级，
  且证据中写出切换点 `(v1->v2)` 与所属版本区间；
- **分块等价性**：对每个会话以 chunks=2、3、事件数、事件数+3 重放，
  并通过 CLI `--chunks 3` 复跑，结果与整体重放逐字节一致；
- **确定性**：库 API 与 CLI 输出一致；
- 空事件流、重复 seq 的日志完整性错误。

## 临时文件

运行期产物写到系统临时目录（`$TMPDIR` 下的 `replay-audit-*`），
仓库内只保留可复跑的夹具与脚本。
