# HDR 测光分区与局部色调映射
`python3 tm.py req.json output.json`
req.json: `{"hdr":"i.json","zones":[4,4],"tile":16}`
i.json=64x64 线性 HDR 亮度. 分区对数测光→曝光EV; 边缘感知局部增益(暗部增益≤3)
- 阶梯曝光图各档重建误差≤0.01; 极暗输入无 NaN/负值
- 输出 `{"ev":v,"out":[[v]],"zone_ev":[[]],"digest"}`
缺陷基线: 全局平均测光+Reinhard(高光削顶暗部死黑).
