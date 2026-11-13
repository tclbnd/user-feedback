# 时空轨迹范围检索

Python 3.9+。运行 `python3 geo.py store apply demo.json`，然后 `python3 geo.py store query boxes.json`。数据源字段 crs 固定 EPSG:4326。输入 points 列表，每条有 device（非空字符串）、t（Unix 整秒）、rev（正整数修订）、lon、lat、source（来源字符串）。经度输入范围 [−180,180]，存储时将 +180 规范为 −180。纬度范围 [−90,90]。所有数值必须有限。以 (device,t) 为主键，保留最高修订的记录。内容一致的同修订重复以及更旧版本无效果，同修订内容冲突退出 2。无效坐标或坐标系退出 2，错误指出字段和点的下标。

query 输入含 queries，每项为 id、west、east、south、north、start、end。经度沿向东的闭弧从 west 到 east。west=-180 且 east=180 表示整圈，west=east 表示单条经线。其他情况下将端点规范化，若 west > east 则跨日期变更线，否则为普通闭区间。纬度包含两端，时间半开。返回 version 和 results，后者与查询输入同顺序，每项包括 id、rows、candidate_blocks、scanned_points。rows 保留完整来源，按 (t,device) 稳定排序。candidate_blocks 为实际读取的空间/时间桶数，scanned_points 为这些桶中进入精确谓词的候选总行数。每个查询的计数单独计算。

按时间小时分区和空间格网索引。apply 每次只更新受影响的桶，原子公开新版本。全重复或旧版本输入不生成新版本。旧快照可通过 query --version N 读取。传 --crash-before-publish 在发布前无清理退出 97，旧版不受影响。正常重放可完成发布。首个有效快照版本为 1，apply 输出 version 和 total_points。可用 SQLite 或文件快照。

实现说明：快照存放于 `head.json`（原子替换的当前版本指针）与 `snapshots/vN.json`（不可变版本）。空间格网为 0.1° 单元，时间按小时（Unix 秒向下取整）。验证可用 `python3 tests.py`。
