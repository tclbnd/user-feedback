# ABR GOP 对齐多档编码模拟
`python3 abr.py req.json output.json`
req.json: `{"frames":[{"t":i,"scene":s(i)}],"ladder":[{"name":"hi","rate":4000},{"name":"lo","rate":800}],"gop":8,"tol":0.05}`
- 以最高档检测场景切换确定统一 GOP 边界(场景切处强制 IDR, 否则每 gop 帧一个 IDR); 低档复用同一边界
- 输出 `{"gops":[{start,end,idr}],"ladder":{name:{"rate_avg":r,"frames":[{t,ts,dts,pts}]},"switch_ok":true},"psnr":{name:v}}`
- 各档按帧列出单调递增的 DTS 与 PTS，同一显示帧的 PTS 和切换点帧号逐档一致。
缺陷基线: 各档独立按 gop 切, 边界漂移.
