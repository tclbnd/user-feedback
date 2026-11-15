# 环境贴图的球谐 irradiance 投影与系数截断

`python3 envmap.py input.json output.json`

input.json: `{"scene":{"type":"uniform|dirlight|gradient|patches",...},"w":32,"h":16,"blocks":4,"dirs":[[theta,phi]...],"seed":1}`

场景契约（解析定义，θ∈[0,π) φ∈[0,2π)）：
- uniform: 常亮 `v`
- dirlight: `dir=[θ,φ]` 上半径 0.05 rad 的圆盘,强度 `i`
- gradient: `lo + (hi-lo)·(0.5+0.5·cosθ)`
- patches: `rects=[[θ0,θ1,φ0,φ1,v]...]`,其余 `bg`

模型约定：
- 投影到三阶 SH(9 实系数,通道单值)：c_lm = ∫L·Y_lm·dΩ，纹素立体角 sinθ·Δθ·Δφ
- irradiance 重建须做 SH 余弦卷积(A_l 缩放):A0=π, A1=2π/3, A2=π/4
- 投影可按 blocks 分块累加，块数/乱序不改变最终系数
- 输出 `{"coeffs":[9 floats],"irradiance":[逐dir],"block_count":n,"digest":"sha1"}`

缺陷基线：等纹素加权(忽略 sinθ 两极拉伸)、irradiance 直接求和无 A_l 卷积。
