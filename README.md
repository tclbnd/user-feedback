# 双时间维度关联

Python 3.9+。用 `python3 bitemporal.py apply state.db demo.json` 和 `python3 bitemporal.py query state.db --asof 9`。可修改 state 格式。

输入 events 含两种事件。每项 system 为非负整数获知时间，version 为稳定的非空事件标识。同 version 完全重复无影响；不同载荷冲突整批拒绝并退出 2。事件可乱序到达。对于同一 fact id 或同一实体覆盖相同业务时刻的同 system 多条不同版本事件，按 version 字典序较大者胜出。查询 asof 含该时刻事件。

fact 含 id、entity、at（业务时刻）、可选 op=delete。查询时选 id 在 asof 的最新事件，删除后不输出。dimension 含 entity、from、to、label 和 op=upsert|remove。业务区间半开 [from,to)，to=null 代表无上限。upsert 替换覆盖区间，remove 是该范围的遮蔽，撤销可通过更晚版本重新 upsert。每笔有效 fact 按 entity 和 at 选覆盖的最新 dimension。无匹配输出 null 和 reason=no_dimension；被遮蔽输出 null 和 reason=removed。匹配输出 reason=matched、label、dimension_version。query 输出 asof 和 rows，行按 id 排序并含 id、fact_version、entity、at、dimension_version、label、reason。

apply 返回 accepted（本批新增唯一事件数）和 affected（本批实际重新求关联的 fact id 集合，升序）。将截至最新 system 的有效 facts 作为当前报表。按变化的 fact 自身与新增维表事件覆盖的实体和业务区间选受影响行；无需在不同批次中累积 affected。维护当前视图及历史查询。持久化事件、当前视图与日志，一次 apply 原子发布。测试使用合成地区标签。
