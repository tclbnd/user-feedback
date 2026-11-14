# 多步表单向导确定性回放
Python3.9+ 纯标准库。

## 用法

```
python3 wizard.py flow.json script.json output.json [state.json]
python3 test_wizard.py          # 20 个单元/场景测试
python3 demo_acceptance.py      # 验收点端到端演示, 产物写入 run/
```

## 数据契约

- `flow.json` 定义页面: 每页有指纹 `fp`、字段 `fields`、跳转 `next`。
  - `next` 支持 `{"branch_on": "字段", "rules": {"值": "目标fp", "default": "..."}}`，
    或扁平规则表；目标 `"end"` 表示向导结束。
  - 字段类型: `text` / `select`(可带 `options` 枚举) / `date` / `checkbox`，
    可带 `prefill` 预填值。
- `script.json` 回放计划: `steps[].expect` 为预期页面指纹, `fill` 为填写值,
  `prefill` 为录制时的预填值快照, `goto` 为预期下一页面, `optional:true` 标记可选步骤。
  - 顶层 `resume_from` 声明恢复点; `stop_after` 用于模拟中断(只跑到指定 step)。

## 三大机制

1. **指纹定位(非序号)**: 运行时页面自报 `fp`, 引擎用 `expect` 与实际指纹比对。
   实际 fp ≠ expect 立即终止: `{"aborted_at":k,"expected","actual","reason":"fingerprint_mismatch"}`，
   意外页面不会被写入任何值。分支合并页/可选步骤跳过(`optional` 记 `skipped`)/分支切换
   都由 flow 规则按载荷值驱动, 与步骤序号无关。`goto` 与 flow 求值交叉校验,
   不一致报 `reason:"goto_mismatch"`。
2. **按字段类型填写与验证**:
   - text: 去空白; select: 统一字符串并校验 `options`;
     date: 多格式输入规范化为 ISO `YYYY-MM-DD` 并校验真实日历;
     checkbox: 布尔/`0,1`/`是,否` 等统一为稳定布尔语义。
   - 非法值不进载荷, 产出字段级差异 `field_diff`(含 step/fp/field/kind/expected/actual);
     预填值与脚本快照不一致记 `kind:"prefill_mismatch"`, 回放不中断。
3. **逐步持久化与恢复**: 每页提交成功即原子写入 `state.json`(临时文件 + `os.replace`)，
   内含 flow/script 规范化内容哈希、已完成步骤(含载荷与字段差异)和 `current_fp`。
   恢复时哈希不一致直接拒绝; 一致则从最近一致步骤之后继续, 已提交步骤进入 `skipped`
   且不重填, 运行时直接定位到持久化的 `current_fp`。

## 输出

```json
{
  "submitted": [{"step": 0, "fp": "p1", "payload": {}, "field_diff": []}],
  "skipped": [2],
  "aborted_at": null,
  "expected": null,
  "actual": null,
  "reason": null,
  "field_diff": [],
  "path": ["p1", "p2"],
  "final_payload": {}
}
```

## 演示夹具

- `flow.json`: 开户向导, 11 个页面、3 处分支(客户类型/产品/条款)、2 个分支合并页、
  1 个可选营销页, 覆盖四种字段类型。
- `script.json`: 个人+借记卡主路径(8 步)。
- `fixtures/script_business_loan.json`: 企业+贷款路径, 验证序号错位旧逻辑会填错页面。
- `fixtures/script_skip_promo.json`: 条款不同意时跳过可选营销页。
- `fixtures/script_prefill_changed.json`: 预填值变更, 产出字段级差异。
- `fixtures/script_invalid_values.json`: 非法枚举/日期/复选/未知字段的差异收集。

## 已修复的基线缺陷

旧实现按 `pages[i]` 序号对齐步骤, 分支变化时把值填进错误页面, 且无任何校验与断点恢复;
新实现以指纹定位、类型化校验、原子状态持久化, 见 `test_wizard.py` 中的对照用例。

