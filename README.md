# 入仓质量规则增量执行
用法 `python3 pcache.py scenario.json output.json`。输入为 `rules`、`queries`、`changes` 三个数组。
- rules 项为 `{"id":"r1","pred":["dept=eng","level>5"],"allow":true}`。按数组顺序首次全部谓词命中即选 allow，无命中为 false。等号比字符串，大于号比整数，缺字段不命中。谓词按数组顺序检查，遇到不匹配立即停止本规则。每次求值记录实际读取的字段集合。
- queries 每项带 `{"sub":{"dept":"eng","level":7}}`，数组位置是行 ID。启动时对每行填缓存但不输出结果。更正 `{"kind":"attr","key":"level","to":9,"sub_i":0}` 仅改变指定行。以 `{"kind":"rule","id":"r1","pred":["dept=ops"],"allow":false}` 更新已有规则，pred 和 allow 可独立给出。更正逐个生效。
- 每个更正后按 queries 顺序执行全体查询。输出 `results` 数组含所有更正后的布尔结果。空更正输出空数组。输出 `hits`、`misses` 只统计更正后的查询，不计启动填缓存。 `invalidations` 按属性名累计清除的缓存条目，下发规则更改导致的清除记键 rule。 `dep_index` 将字段映射到最终依赖该字段的行 ID，数组按 ID 升序。
现有程序每次重算所有行，没有依赖索引。
