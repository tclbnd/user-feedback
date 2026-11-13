# 双输入双输出脉冲响应切换

Python 3.9+。运行 `python3 convolution.py demo.json output.json`。输入含 partition（2 的幂，8–128）、initial（初始响应名）、kernels（名称到 [输出声道][输入声道][tap] 数组映射）、blocks（交错的双声道输入块列表），以及 switches=[{at,to,fade}]。at 为绝对输出采样索引，fade 为正整数。切换按 at 排序，切换区间不得重叠，允许尾音期切换。已知每个响应的所有过去输入历史；任一时刻都可从其卷积输出取值。设当前旧响应的卷积为 a、新响应为 b，切换后第 j 个样本在 0≤j<fade 时取 (1−j/fade)×a+(j/fade)×b，达到 fade 后只用新响应。总长度 N+最大 tap 长度−1，缺失输入填零。离线输出索引以 0 对齐，latency 报告 partition−1 的最多等待样本数。

使用固定分区频域卷积，单次回调块长度任意，跨块保留输入历史。可初始化频域响应、环缓冲和工作区。JSON 入出与结果列表不算 DSP 工作区分配。stats 至少含 fft_count（实际 FFT 调用数）和 steady_state_buffer_allocations（开始处理音频后创建或扩容的 DSP 工作区/历史数组数）。需要能自测 FFT 与时域路径一致。出错要报告输入位置，失败不覆盖既有输出。
