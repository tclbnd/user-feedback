# 双时间维度关联

Python 3.9+，无第三方依赖。`bitemporal.py` 是唯一实现，`tests/` 是验收与性质测试。

```
python3 bitemporal.py apply     STATE events.json [--details]
python3 bitemporal.py query     STATE --asof S        # 按系统时间重算报表
python3 bitemporal.py publish   STATE --asof S | --batch N [--latest]  # 已发布报表原件
python3 bitemporal.py dimensions STATE --asof S --at T                  # 两个时间维度查维表
python3 bitemporal.py fact      STATE ID --asof S                       # 单笔事实的版本溯源
python3 bitemporal.py unmatched STATE --asof S                         # 无法匹配的理由
python3 bitemporal.py history   STATE [--asof S]                       # 显式双区间维表
python3 bitemporal.py log       STATE                                  # 变更明细/发布摘要
python3 bitemporal.py verify    STATE                                  # 校验当前视图与历史报表
```

运行验收：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`

## 时间模型

**业务时间**：事实取一个瞬时 `at`；维表行取半开区间 `[from, to)`，`to=null` 表示无上限。相邻区间 `[0,10)` 与 `[10,20)` 不重叠，`at=10` 只落在后者。业务边界处的取值为验收项之一。

**系统时间**：`system` 是非负整数，表示该事件何时被获知。`asof=S` 的报表只含 `system <= S` 的事件（含该时刻）。每行的有效系统区间从自身 `system` 开始，被**更晚**的、覆盖同一业务瞬时的修订截断——但事件本身从不原地改写，所以旧版本始终可查。

**稳定顺序**：`version` 是全局唯一的非空事件标识，`(system, version)` 构成全序。乱序到达、跨分区重放、重复投递都落在同一顺序上。同一系统时间、覆盖同一业务瞬时的多个维表版本冲突时，`version` 字典序较大者胜；落选行保留在 `history` 中并标记 `shadowed`，不丢信息。

**修订算子**（都是普通事件，无需特殊分支）：
- 拆分：对子区间再发一条 `upsert`，原行在两侧保留，仅中段被截断；
- 撤销：`op=remove` 遮蔽该区间，查询输出 `null` + `reason=removed`；
- 回收：撤销之后再发更晚版本的 `upsert`，同一区间重新可用；
- 缺口：没有任何行覆盖，输出 `null` + `reason=no_dimension`。

`history` 输出的是**精确**双区间表：每一行的矩形内，该版本就是当时报表会选中的答案。部分重叠会真的把旧行切成多行；同一系统时间的冲突行系统区间为空 `[S, S)`。

## 增量重算

`apply` 返回 `accepted`（本批新增的唯一事件数）与 `affected`（本批实际重新求关联的 fact id，升序），`--details` 额外给出逐 key 的 `before/after` 变更明细。影响范围只由本批决定，不跨批累积：

- 事实自身胜出版本变化（含删除、恢复、entity/at 变更）的 id；
- 本批新增维表事件所覆盖的实体 × 业务区间内、当前仍有效的事实的 id。

未被命中的行原样保留在物化视图里，从不重算。`tests/` 用手工推导的范围逐批核对，并用随机场景断言「实际变化的 key 集合 ⊆ affected」，且 `changes` 恰等于真正变化的 key。

## 历史报表可复现性

这是补录与历史报表的关键分离，两个接口分别对应两种需求：

- `query --asof S` **从不可变事件日志重算**。补录一条系统时间很早的修正会改变今天对同一 `S` 的重算结果——这是应有行为。
- `publish --asof S` 返回**当时真正发布**的报表原文（按批固定行，含每行的 `fact_version` 与 `dimension_version`），不受后续补录影响。同一 `asof` 被发布多次时默认取最早一次（`--latest` 取最近一次）。

每次 `apply` 在日志里追加一条 `published` 记录（`asof`、行数、SHA-256 摘要、行快照）。`verify` 同时校验两件事：当前物化视图等于从日志全量重建的结果；每条已发布报表的快照摘要未被篡改，并标出哪些 `asof` 因补录已无法用今日逻辑重算出同一结果（`backfilled`）。

## 验收覆盖

`tests/test_bitemporal.py` 用一个**不共享引擎代码**的 oracle：按 `(system, version)` 重放原始事件流，逐 `(entity, 业务瞬时)` 维护被相信的维表版本，在每个切点快照。

1. **12 个切点 + 8 次回溯修订**：`SCENARIO` 含拆分、撤销、回收、部分重叠、同系统时间冲突、事实删除/恢复/改 entity。按 8 个不等批次乱序灌入，12 个系统切点逐条事实比对 oracle（`test_twelve_cutpoints_match_oracle`）。相邻区间边界点单独断言：`at=5` 落在 `[5,10)` 内、`at=10` 落在其外，`at=8` 命中而 `at=12` 不命中（`test_boundary_points_of_adjacent_intervals`）。
2. **批次回放 + 重复修订**：同一组版本按 4 种批次切分（含逐条）灌入，当前结果与全部历史切点一致（`test_current_and_history_independent_of_batching`）；整批重放与单条修订重放均返回 `accepted=0, affected=[]`，结果、历史与日志长度不变（`test_repeating_a_revision_is_idempotent`）。
3. **影响范围核对**：逐批将 `affected` 与手工推导范围比对（`test_affected_matches_hand_computed_scope`），并断言 `affected` 不跨批累积、不包含未变化的行。
4. **性质测试**：12 个随机种子 × 乱序分批，增量结果与 oracle 全量重建逐切点相等，`verify` 通过；另有 8 个随机种子断言 `changes` 恰为真正变化的 key。

`demo.json` 保留原样例，`apply`/`query` 行为兼容。

## 输入约定与错误处理

输入 `events` 含两种事件。每项 `system` 为非负整数获知时间，`version` 为稳定的非空事件标识。同 version 完全重复无影响；不同载荷冲突整批拒绝并退出 2，且状态文件保持不变（原子发布：先写临时文件再 `os.replace`）。`remove` 必须带 `from`/`to`；`to < from` 非法。state 为单个 JSON 文件；旧版只有 `events` 的文件可直接读取并升级，物化视图若与日志不符会在读取时重建。

