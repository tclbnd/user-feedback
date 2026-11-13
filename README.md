# 多版本离线表单采集

Python 3.9+，仅用标准库，无网络。

## 夹具与运行

```bash
python3 generate.py            # 在当前目录生成 24 个任务的合成 HTML 快照与 jobs.json
python3 forms.py ROOT collect jobs.json
python3 forms.py ROOT inspect [--verbose]
python3 -m unittest test_forms -v
```

jobs 含 `{id,snapshots}`，相对路径按渲染时间先后排列。form 的 `data-template`
为 v1/v2/v3（未知版本隔离为 UNKNOWN_TEMPLATE），`data-ready="true"` 才可提取，
选择首个 ready 快照；全部未就绪进入 TIMEOUT。

## 字段定位（不按位置猜值）

字段由 **模板版本 + 字段组（fieldset/legend）+ 可访问性标签 + 表单上下文**
共同定位：

- 标签来源按 `aria-label` → `label[for]` → 包裹式 `label` 的顺序解析，无有效
  标签的控件不参与定位；隐藏控件（含 hidden 祖先或 `type=hidden`）与 disabled
  控件忽略。
- 标签别名必须在模板注册表（`BUILTIN_TEMPLATES`，可用 `--templates reg.json`
  扩展）中显式登记，匹配时归一化空白与大小写；未登记的标签不做模糊兜底。
- 同标签不同字段组互不混淆：Buyer 组的 Name/Full name 是 buyer，Contact 组的
  同名字段是 contact。Routing 组的 Route/Route code 是 route，仅在 form
  `data-routing="on"` 时必需，否则输出 null（条件字段）。
- 一个必填字段出现多个有效候选 → AMBIGUOUS_FIELD；必填字段无候选 →
  MISSING_FIELD；模板未登记 → UNKNOWN_TEMPLATE。三类记录全部隔离进 review，
  结果表误提取为零，并附完整诊断（候选/已见控件的 dom_id、dom_path、组、标签）。

## 状态机与断点恢复

每个任务保存稳定 `task_key`（由 id 派生的哈希）、采集器版本、模板注册表哈希、
输入指纹（快照字节）与每字段来源，状态在
`PENDING → EXTRACTING → RETRYING/WAITING → DONE/REVIEW` 间迁移，持久化于
`ROOT/.collect-state.json`；每次写入走临时文件 + `os.replace` 原子替换。

- `--crash during-extract`：首个任务字段解析中途以退出码 97 终止；重跑时识别
  残留的 EXTRACTING 状态，记录 `interrupted` 尝试后断点恢复。
- `--crash after-save`：首个任务完整持久化后、回报调用方前以 97 终止；重跑按
  指纹跳过，不产生重复结果。
- 瞬时解析/IO 错误进入 RETRYING 并重试（`--max-attempts`，默认 3，退避
  `--backoff`）；TIMEOUT 进入 WAITING 后按同样预算回放。重试耗尽归
  EXTRACT_ERROR 并标记 `needs_manual`。
- 公开视图 `ROOT/view.json` 只在任务到达终态后原子刷新，任何时刻都不含半成品；
  每个任务键在结果表中至多一份。相同输入重复采集只新增 skip 事件、不新增数据；
  输入指纹变化才会重采。
- `--only ID` 针对单条重新采集（保留历史尝试痕迹并记录 manual_recollect 事件），
  标准输出只回显该任务，落盘完整视图不变。
- 测试钩子：`ZQ_RETRY_FAULT=<task-id>:<k>` 让指定任务前 k 次尝试注入瞬时故障；
  `ZQ_FAULT=during-extract|after-save` 等效 `--crash`。

## 输出

`view.json` 含 `rows` 与 `review`。

- row：`{id,template,fields,sources,field_sources,snapshot,task_key,
  collector_version,registry_hash,fingerprint}`。fields 只含 buyer、contact、
  route；sources 对应 DOM ID（无来源为 null），field_sources 进一步给出
  dom_path、字段组、标签与标签类型、快照文件名，来源可逐字段回溯。
- review：`{id,reason,file,classification,attempts,needs_manual,...诊断}`，
  reason ∈ UNKNOWN_TEMPLATE / AMBIGUOUS_FIELD / MISSING_FIELD / TIMEOUT /
  EXTRACT_ERROR。
- 状态文件的 events 流记录每次尝试开始、重试调度、等待、断点恢复、跳过、单条
  重采与终态时间，供审计与人工复查。`inspect` 返回已完成的公开视图，
  `--verbose` 附状态摘要与事件数。

