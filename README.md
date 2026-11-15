# 入湖分层存储的压实选层与水位推进

`python3 lsm.py ops.json output.json`

ops.json: `{"levels":3,"cap":[4,4],"seed":7,"ops":[{"op":"put|del|snap|read","k":"k01","v":1,"snap":0}...]}`

模型约定：
- 每层存 `{k: [seq,v]}` 按 seq 新→旧排序；put 进 L0，层满触发压实
- 压实选层：按下层容量与键范围重叠度选文件组（本模拟按键范围归并整层→下层）
- del 写 seq 墓碑（v=None）；快照持有读视图，压实不得回收其可见版本
- 水位 watermark=已压实最大 seq，只进不退；read 在 snap 上按可见性读最新≤快照seq
- 输出 `{"levels":[{k:[[seq,v]]}],"watermark":v,"compactions":n,"reads":[v],"errors":[],"digest":"sha1全状态"}`

缺陷基线：压实随机选层+水位回退，快照读丢版本。
