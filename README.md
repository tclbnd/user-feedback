# SSR 分层步进与命中置信度
`python3 ssr.py req.json output.json`
req.json: `{"depth":"d.json","rays":[{ox,oy,od}],"hiz":true,"res":64}`
depth.json=64x64 深度缓冲(平面/台阶场景). 光线步进求与深度面交点:
- 分层最小深度查询; 跨遮挡回退二分细化; 出屏/近裁剪标记 out|clip
- 命中置信度=步进距离*法线夹角衰减; 背面命中 conf*=0.3 不丢
- 输出 `{"hits":[{ray_i,t,conf,src:"march|miss|out"}],"steps_hist":{...},"digest"}`
缺陷基线: 固定大步长(穿透+抖动),无置信度.
