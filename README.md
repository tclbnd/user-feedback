# 广播响度归一化

Python 3.9+。运行 `python3 normalize.py input.wav output.wav report.json --layout stereo`。输入输出为 48 kHz signed 16-bit PCM WAV。layout 为 mono、stereo 或 5.1（FL、FR、FC、LFE、BL、BR）。5.1 中 LFE 不参与响度计算，但计入真峰值。按 ITU-R BS.1770 的 K 加权、400 ms 窗和 100 ms 步长、绝对及相对门控实现积分响度。夹具最短 0.55 秒；不足一个完整窗的节目报告 lufs=null。真峰值以至少 4 倍过采样和足够滤波支撑估计。--block 是读取帧数，不得改变结果。

基于测得的 LUFS 求整体增益，再按真峰值限制最大增益。峰值优先，允许留安全余量但不得使可行节目偏离 0.3 LU。--target 默认 -23，--ceiling 默认 -1。报告至少为 {layout,gain_db,input:{lufs,dbtp},output:{lufs,dbtp},reason}，reason 为 target、peak_limited、silence 或 insufficient_duration。无定义数值用 null。真峰值约束针对落盘后的 PCM。拒绝不支持的布局。可添加诊断。演示音源可运行 `python3 make_demo.py` 生成。
