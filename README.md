# 均衡器系数平滑与拉链噪声抑制
`python3 eq.py req.json output.json`
req.json: `{"fs":48000,"blocks":64,"bs":64,"auto":[{"at":0.3,"biquad":{"g_db":12}}]}`
- biquad peaking(g_db 增益,fc=1k,Q=1); 系数按采样级向目标平滑(指数 tau=50ms)
- 系数变化率限幅,滤波器状态连续; 输出 `{"out":"out.wav","coef_traj":[[b0,b1,b2,a1,a2]@block],"settle_ms":t,"peak_db":v}`
- 块大小不影响波形(1e-4 包络).
缺陷基线: 每块直接换系数(阶跃不连续).
