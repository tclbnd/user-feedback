# 谱图相位迭代重建
`python3 phase.py req.json output.json`
req.json: `{"mag":"m.json","iters":30,"seed":7,"win":256,"hop":64}`
mag.json: [[帧][频]]实信号单边幅度谱，每帧有 `win // 2 + 1` 个频点（包含直流与奈奎斯特频点）。Griffin-Lim 交替施加幅度约束与时域重叠一致。
- 固定 seed 初始化; 逐次记录谱一致性误差; eps 早停
- 输出 `{"signal":[...],"err":[...],"snr":v,"iters_used":n}`
缺陷基线: 随机相位一次投影,不迭代.
