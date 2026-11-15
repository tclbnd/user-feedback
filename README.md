# 多光源阴影图集分配与级联归属
`python3 atlas.py req.json output.json`
req.json: `{"lights":[{id,prio,influence,dist}],"slots":8,"frames":[{"cam_z":v}]}`
- 每帧按 (prio desc, influence desc) 排序取前 slots 分配,槽位 id 稳定
- 低优先级被挤出时其余映射不变; 级联归属带滞回(切换次数记录)
- 输出 `{"alloc":{frame:{light:slot}},"evict":[],"cascade":{frame:{light:idx}},"digest"}`
缺陷基线: 每帧按字典序重排灯+槽位轮转分配→漂移.
