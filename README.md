# ABR GOP 对齐多档编码模拟
`python3 abr.py req.json output.json`
req.json: `{"frames":[{"t":i,"scene":s(i),"complexity":可选}],"ladder":[{"name":"hi","rate":4000},{"name":"lo","rate":800}],"gop":8,"tol":0.05}`

## 处理流程
1. 场景切换检测只执行一次, 以最高档(目标码率最大档)为参考; 场景切处强制 IDR
   并重置固定周期计数, 无切换时每 `gop` 帧一个 IDR, 片尾不足一个 GOP 的余量
   作为收尾 GOP。
2. 所有档位复用同一份 GOP 分割计划, 帧类型(I/P)与引用结构逐帧一致,
   仅帧级量化参数 QP 不同。
3. GOP 内 VBR: GOP 比特预算按参考编码代价加权分配, 帧 QP 跟随帧复杂度
   (`size = A·2^(-QP/6)`, 闭式解, 未钳位时精确命中目标码率); 全局唯一
   标定尺度 K 使高档 QP 更低/PSNR 更高。
4. 每个 GOP 是独立编码分块(closed GOP), `run(req, gop_order=...)` 可改变
   分块编码顺序而不改变拼装后的码流。

## 输出结构
- `gops`: 全档唯一的 GOP 表 `{start,end,idr,frames,scene_cut}`
- `ladder.<name>`: `rate_target/rate_avg/rate_dev`、逐帧
  `{t,ts,dts,pts,gop,type,qp,size,psnr,ref}`、每 GOP 码率统计 `gop_stats`
- `psnr.<name>`: 序列级平均 PSNR(另有 GOP 级 `psnr_avg` 与帧级 `psnr`)
- `alignment`: 切换点对齐报告(边界一致性、DTS/PTS 单调、跨档时间戳差、
  场景切落点、码率容差、引用结构), `aligned` 为总体结论
- 各档同一显示帧的 DTS/PTS/ts 完全相同(差值为 0), 切换点帧号逐档一致。

## 验收
`python3 verify.py`: 240 帧 / 20 个场景切换 / 5 档, 校验 GOP 边界逐档一致、
时间戳零差值、场景切全部落在 GOP 边界、码率偏差 ≤ 5%、PSNR 报告齐全、
分块乱序码流不变、片尾收尾 GOP。另见 `edge_tests.py` 边界用例。

