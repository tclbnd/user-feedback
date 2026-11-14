# 多特征静音判定智能剪辑
`python3 cut.py req.json output.json`
req.json: `{"pcm":"in.wav","fs":16000,"features":{"level_db":-45,"flat":0.6,"min_ms":200},"human":[{"start":1.0,"end":2.0}人工参考段]}`
- 帧(20ms)级判定: 电平<thr 且 谱平坦度>flat 且 持续>=min_ms → 静音; 气口(<80ms)保留; 突发噪声不衰减判非静音
- 剪切点前后保护 50ms+淡入淡出 20ms; 间隔<300ms 的保留段合并
- 输出 `{"keep":[{start,end}],"drop":[{start,end}],"boundaries":[ms],"stats":{...}}`
缺陷基线: 纯电平门限一刀切.
