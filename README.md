# 分区回填与发布

Python 3.9+。运行 `python3 backfill.py ROOT build demo.json`。输入为 inputs 字典及 nodes 字典。inputs 键代表日级、带地区的源分区，值为 {value,version}。nodes 的每个键代表派生分区，值为其有序依赖键列表。DAG 中周/滚动窗口及跨时区映射已由清单展开，依赖可指向 inputs、其他 node 或缺失的输入键。缺失输入贡献 0，并有单独指纹。node 值为依赖 value 的整数和。依赖列表顺序不变，多个节点可用同一父节点。应拒绝循环。

为每个源按规范 JSON 数组 ["source",key,version,value] 计算 SHA-256，缺失依赖用 ["missing",key]，派生节点用 ["node",key,[按依赖列表的子指纹]]。规范 JSON 为 UTF-8、separators=(",",":")、ensure_ascii=False，无结尾换行。输出 values 与 fingerprints 只含派生节点。executed 是本次实际计算的派生节点，按拓扑层及键升序，未改动的指纹复用已公开或验证有效的暂存结果。对每个执行分区可提供受影响的依赖路径。

inspect 返回已发布 values/fingerprints，未构建为空。--crash-after-layer N 使第 N 个派生层（从 1 开始，含未重算层）处理结束、还未公开新结果时退出 97，保留已完成暂存进度。正常恢复前可能更换输入清单。根据新依赖指纹复用仍有效的暂存，失效的暂存必须重算，最后原子发布一致全图。演示输入无需外部依赖。
