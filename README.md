# 双投影曲面校准

Python 3.9+。运行 `python3 projection.py demo.json output.json`。pattern[y][x] 是曲面 UV 整数网格上的线性目标亮度。projectors 含 id、markers、pixels、blocked、gain、black、gamma。每个 marker 含 surface=[u,v]、screen=[px,py]。屏幕映射模型为每坐标独立的二次多项式，基为 (1,u,v,u²,uv,v²)。缺失 marker 可省略；每个模型有至少 6 个满秩有效点。输出 calibration 按 id 排序，每项 {id,x,y,rms}，x、y 分别为 6 个系数，rms 为标记的二维重投影 RMS。退化模型报错。

pixels 是每台输出图像像素的统一曲面坐标查表，可非整数，允许少量畸变。blocked 是相同尺寸的布尔遮挡图。对每个 projector，在其有效图域构建从曲面坐标到覆盖的映射。夹具的 pixels 为规则曲面坐标矩形内的单位整数网格，标定系数用于几何诊断。遮挡和网格之外权重为零。有效区域以到该投影矩形边界的距离加 1 为初始权重，按同一曲面点归一化。单投影权重为 1。没有有效覆盖的点标记 invalid，combined 为 null。

给定线性目标 P，活动投影黑位和 B=sum(black)。第 i 台的理想输出亮度为 black_i+weight_i×max(0,P−B)，其电信号 s=clamp((light−black_i)/gain_i,0,1)^(1/gamma_i)，实际亮度 black_i+gain_i×s^gamma_i。关闭图元输出信号 0，禁用位置没有黑位贡献。输出 projectors=[{id,signal,weights}]，每个 signal 与 weights 形状和该投影 pixels 相同。combined 为按曲面网格相加得到的实际亮度。输出可由同一文件复跑得到一致 JSON。
