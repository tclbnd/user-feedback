# 等功率交叉淡化与剪接点相位连续

`python3 xfade.py input.json output.json`

input.json: `{"a":[float],"b":[float],"len":512,"block":128,"seed":1}` (a,b 为待衔接信号,长度=淡化窗)

模型约定：
- 等功率淡化:g_a=cos(πt/2),g_b=sin(πt/2);剪接点在过零窗内吸附
- 检测两端相位斜率差,必要时短窗相位对齐;块边界不影响结果
- 输出 `{"out":[float],"splice_pos":n,"stats":{...},"digest":"sha1"}`;复跑摘要一致

缺陷基线:线性等幅+硬剪接(中点下凹+相位咔哒)。
