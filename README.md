# 噪声整形抖动量化的误差反馈与底噪谱形

## 运行

```
python3 run_validation.py        # 完整验收, 产物写入 results/
python3 dither.py input.json output.json   # 单任务 CLI
```

仅依赖 Python 3 标准库（自写基-2 FFT / Welch PSD / 相干正交投影 THD+N，
无需 numpy/scipy）。

## 接口

input.json: `{"sr":96000,"samples":[float -1..1],"bits":16,
"mode":"none|rect|tpdf","shaping":[a1,a2] 或 null,"block":4096,"seed":1}`

输出 `{"out":[int 量化样本],"digest":"sha1(全量输出)","stats":{...}}`。

## 模型约定

- 量化步长 q = 1/2^(bits-1)，码 n 对应电平 n·q，饱和到 [-2^(bits-1), 2^(bits-1)-1]。
- TPDF 抖动 = 两个独立 U(-q/2,q/2) 之和，方差 q²/6，一阶 PDF 在量化格点连续，
  使量化误差与信号完全去相关、消除谐波相关与极限环。
- **环内抖动二阶 delta-sigma**（关键）：若把抖动加在普通误差反馈环的量化器
  输入，输出恒等式 y = x + d + NTF·e 中 d 走平坦通路(STF=1)，抖动不被整形，
  4-8 kHz 仅降约 1.7 dB。本实现把抖动注入双积分器环内：
      s1[n] = s1[n-1] + x[n] - y[n-1]
      s2[n] = s2[n-1] + s1[n] - y[n-1] + g1·e[n-1] + g2·e[n-2], g1=2-a1, g2=1+a2
      u = s2 + d;  y = Q(u);  e = y - u
  线性化 z 域：STF(z)=z⁻²，NTF(z)=1-a1·z⁻¹-a2·z⁻²。抖动与量化残差都被同一
  NTF 整形，DC 增益 (1-a1-a2)²≈0，误差能量推向高频。
- 反馈环 5 个状态 s1,s2,yp,e1,e2 初始置零；分块时状态与 RNG 跨块延续、随机源
  为单条连续序列，因此任意块大小与整段处理**逐比特一致**（digest 相同）。
- 静音段在 TPDF 持续激励下无极限环（长滞后自相关、谱线突出度、码分布三重判据）。

## 验收结果（96 kHz / 16 bit / -60 dBFS 1 kHz，详见 results/REPORT.md）

| 指标 | 要求 | 实测 |
|---|---|---|
| 最差谐波 | ≤ -90 dBFS | ≈ -155 dBFS（3 组系数） |
| 4-8 kHz 量化噪声下降 | ≥ 6 dB | 12.2 / 15.5 / 15.5 dB |
| 2 块长 × 3 系数 PSD 摘要一致 | 一致 | 跨块偏差 0.0 dB，逐比特相同 |
| 静音极限环 | 无 | 全部通过 |
| 总判定 | — | ALL PASS |

缺陷基线演示（results/ 与 debug_trunc.py）：12-bit 无抖动截位最差谐波
-85.7 dBFS（超 -90 限值），TPDF 后降到 -118.4；矩形/TPDF 无整形时底噪平坦。

