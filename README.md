# 离线体积雾积分

Python 3.9+，使用标准库。运行 `python3 fog.py demo.json output.json`，或添加 `--no-skip` 禁用空块跳过。JSON 记录逐射线测试数据，背景色和介质源项使用线性亮度。

scene 包含正数 step 与 rays。每条射线含 near、far、surface_color、可选 surface_t 和按 a 升序且不重叠的 medium 块。每个块含半开区间 [a,b)、rho=[起点,终点]、light=[起点,终点]、light_extinction（默认 0）。rho 为非负消光系数，rho 与 light 在块内线性插值。沿射线位置 x 的局部光源亮度还乘 exp(−light_extinction×(x−a))。间隙和两个 rho 端点都为零的块属于空区。只积分 [near, min(far,surface_t))，若上界不超过 near 则无介质贡献。没有 surface_t 时 surface_color 为远处背景。

透射 T=exp(−∫rho)，散射沿当前累积透射对 rho×局部光源积分。输出 transmit、scatter、color=scatter+transmit×surface_color、steps、skipped。steps 记录实际积分子段数，skipped 记录跳过的非零长度空段数量。关闭跳过时，空段也按 step 分段。每段积分不超过 step，并按块边界及表面截断。允许解析积分或确定性数值积分，精度见题面。
