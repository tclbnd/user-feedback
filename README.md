# 噪声整形抖动量化的误差反馈与底噪谱形

`python3 dither.py input.json output.json`

input.json: `{"sr":48000,"samples":[float -1..1],"bits":16,"mode":"none|rect|tpdf","shaping":[a1,a2] 或 null,"block":4096,"seed":1}`

模型约定：
- TPDF 抖动(两独立矩形和)+二阶噪声整形误差反馈环，量化噪声谱推向高频
- 反馈环初始条件置零，块边界状态延续；随机源由 seed 种子化保证可复现
- 输出 `{"out":[int 量化样本],"digest":"sha1","stats":{...}}`；digest 覆盖全部输出样本
- 静音段不得出现极限环振荡

缺陷基线：直接截位——-60dB 正弦出现谐波失真，底噪谱形平坦。
