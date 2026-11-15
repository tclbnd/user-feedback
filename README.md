# 侧链压缩的预读增益包络与拐点平滑

`python3 comp.py input.json output.json`

input.json: `{"sr":48000,"samples":[float],"thresh_db":-20,"ratio":4,"attack_ms":2,"release_ms":80,"lookahead_ms":5,"block":64,"seed":1}`

模型约定：
- 侧链预读 lookahead_ms 提前触发,主信号延迟补偿对齐(相位零偏移)
- 增益包络=检测器→起音/释放平滑→软拐点压缩律(增益变化率受限,拐点无斜率突变)
- 输出 `{"out":[主信号],"gain":[逐样本],"stats":{"triggers":n},"digest":"sha1"}`;block 仅分块参数不改结果
- seed 生效保证确定性

缺陷基线:无预读瞬时硬拐点——阶跃过冲+拐点折痕+泵浦锯齿。
