# 砖墙限制器的真峰值约束与释放整形

`python3 limiter.py input.json output.json`

input.json: `{"sr":48000,"samples":[float],"ceiling_db":-0.1,"attack_ms":1.0,"release_ms":50,"block":1024,"seed":1}`

模型约定：
- 4x 过采样检测真峰值(采样间峰)，预读窗内增益按起音/释放包络整形
- 多段限制联动，增益曲线平滑单调(释放段无锯齿振荡)
- 输出 `{"out":[float],"gain":[逐样本增益],"stats":{...},"digest":"sha1"}`，block 仅分块处理不影响结果
- 处理需确定性(seed 生效)

缺陷基线：逐样本硬钳——插值波峰超 ceiling，增益锯齿失真。
