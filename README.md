# 环境贴图跨面采样

Python 3.9+。运行 `python3 cube.py demo.json output.json`。faces 是按 `+x,-x,+y,-y,+z,-z` 命名的 6 个 N×N 线性单通道方阵，行号对应 v、列号对应 u。提供的 direction 和 locate 定义六面约定，绝对值轴相同时按 x、y、z 优先级。第 i 个像素中心为 2(i+0.5)/N−1。directions 是非零三维方向。samples 返回每个方向的跨面双线性结果。若中心面某一邻点越过边或角，将该邻点中心转换成三维向量，并在落入的面取对应最近像素，不在原面夹取。保持输入方向尺度不影响结果。

roughness 与 sizes 是等长列表。每一级输出 {roughness,size,samples,faces}。levels[i].samples 是对应输入 directions 的预过滤值，faces 是按该级 size 的六面预过滤结果。r=0 时使用跨面双线性过滤。r>0 使用归一化积分核 max(0,d·n)^k，k=max(0,2/r²−2)，k=0 时核为正半球的常数 1。源像素按其覆盖的立体角加权，累加后按权重归一化。可使用立体角精确公式或有明确误差控制的近似。可选 seed，固定种子可复现；确定性积分可以忽略 seed。所有计算和输出为线性亮度。
