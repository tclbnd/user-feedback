# 掩蔽驱动音频水印嵌入与盲检测

纯标准库实现(无 numpy 依赖): `wm.py` 为嵌入/检测库 + CLI, `eval_wm.py` 为验收评估。

## 用法
```
嵌入: python3 wm.py req.json out.json
      req.json = {"pcm":"in.wav","bits":[1,0,...],"seed":7,"frame":1024}
检测: python3 wm.py req.json out.json
      req.json = {"pcm":"wm.wav","detect":true,"seed":7,"frame":1024,"nbits":40}
评估: python3 eval_wm.py        # 全部验收项, 报告写入 out/report.json
```
- 16bit mono wav(采样率自适应); 每比特 3 帧重复(hop=1024), 输出 `{"out","embed":[...],"detect":{...}}`
- `embed`: 逐帧 `{frame,kind,bit,cls,alpha,energy,snr_db,margin_db,band_hz}` (嵌入位置/能量/掩蔽余量)
- `detect`: `{sync_found,sync_score,phase,payload_start_sample,bits,conf,missing}`; 同 seed 嵌入逐样本可复现

## 设计
1. **掩蔽分配**: 帧长 2048/hop 1024, sqrt-Hann + 50% OLA(完美重构); Bark 域 Johnston 扩展
   掩蔽模型 + Terhardt 绝对听阈 + 邻帧时域掩蔽泄漏(前向强/后向弱); 水印按可掩蔽幅度
   (3x 中位数截顶平坦化)分配到 1.0–4.5 kHz 嵌入带, 逐 bin 不超过可掩蔽幅度;
   安静帧 x0.5 / 瞬态帧 x0.7 强度; 每 1024 样本块硬限幅 -26dB ⇒ 逐帧 SNR ≥ 25dB。
2. **同步与检测**: 24 帧已知同步头(位置无关 PN, 后缀对齐允许头部被裁) + 4 帧 trailer
   抗 ±1 帧错位; 8 相位扫描容忍任意样本级裁剪起点(误差 ≤1 帧); 逐帧白化(局部均值去除
   +离群截断)相关 + 有界掩蔽匹配模板; 每比特 3 帧软合并; |corr|<0.20 判缺失并输出置信度。
3. **可复现**: 全部 PN/同步序列由 seed 派生(random.Random), 无其他随机源。

## 验收结果 (eval_wm.py, 见 out/report.json)
- 10 个攻击样本(原样/两种裁剪/模拟感知编码 Bark 带量化/5kHz 低通/12kHz 重采样/
  包络跟随噪声 -28dB/增益 0.35/G.711 μ-law/中段替换): 真值窗检出率 10/10 = 100%,
  同步误差 ≤ 0.22 帧, 中段替换的 5 个比特全部正确判缺失;
- 误报(未水印 2 信号 x 3 攻击 + 错种子): 2/8020 帧 = 2.5e-4 ≤ 1e-3;
- 逐帧 SNR 最小 26.0 dB ≥ 25 dB; 4 种子复跑样本级/逐比特一致。

## 已知边界
- 纯白色宿主(持续宽带噪声)在 -26dB 水印下相关值天然受限, 检测依赖宿主谱结构;
  极端信噪比(如全局 0dB)不在"常见转码与剪辑"范围内。
- 基线缺陷(等能量直叠可闻、无同步、检测不相关)已全部修复。

