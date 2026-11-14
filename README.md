# 入仓质量规则增量执行
用法 `python3 pcache.py scenario.json output.json`。输入为 `rules`、`queries`、`changes` 三个数组。
- rules 项为 `{"id":"r1","pred":["dept=eng","level>5"],"allow":true}`。按数组顺序首次全部谓词命中即选 allow，无命中为 false。等号比字符串，大于号比整数，缺字段不命中。谓词按数组顺序检查，遇到不匹配立即停止本规则。每次求值记录实际读取的字段集合。
- queries 每项带 `{"sub":{"dept":"eng","level":7}}`，数组位置是行 ID。启动时对每行填缓存但不输出结果。更正 `{"kind":"attr","key":"level","to":9,"sub_i":0}` 仅改变指定行。以 `{"kind":"rule","id":"r1","pred":["dept=ops"],"allow":false}` 更新已有规则，pred 和 allow 可独立给出。更正逐个生效。
- 每个更正后按 queries 顺序执行全体查询。输出 `results` 数组含所有更正后的布尔结果。空更正输出空数组。输出 `hits`、`misses` 只统计更正后的查询，不计启动填缓存。 `invalidations` 按属性名累计清除的缓存条目，下发规则更改导致的清除记键 rule。 `dep_index` 将字段映射到最终依赖该字段的行 ID，数组按 ID 升序。

## 缓存与失效语义（pcache.py）
- 每条缓存保存 `result` 与决定该结果时实际读取的字段集合 `deps`（短路求值，未读到的字段不入集合）。
- 属性更正只更新指定行；仅当该行缓存存在且 `key in deps` 时清除本行缓存，其他行及不依赖该字段的结论保持命中。缺字段也会记录读取，因此后续新增该字段同样触发重算。
- 规则更正按 id 局部更新（`pred`、`allow` 可独立给出），随后清空全部缓存（按当前条目数累计到 `invalidations["rule"]`），下一轮查询自动重建并按新规则重新求值，杜绝复用旧拦截结论。
- `dep_index` 由最终缓存反向汇总：字段 -> 依赖它的行 ID 升序列表，可解释当前每个结论由哪些数据字段参与。
- 同一记录快照下，增量缓存路径与冷启动全量重算的 `results`、`dep_index`、计数完全一致（见 `tests/`）。

## 测试
- `python3 tests/generate_fixture.py` 生成夹具（含确定性的 1000 次更正夹具）。
- `python3 -m unittest discover -s tests` 运行全部单测与 60 组随机属性测试。
- `python3 regression_check.py` 运行题述两个缺陷的最小回归断言。
- 1000 次更正夹具实测命中率约 0.99（要求 >= 0.90）。
