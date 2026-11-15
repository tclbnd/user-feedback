# 确定性粒子模拟与深度排序混合
`python3 sim.py req.json output.json`
req.json: `{"n":64,"steps":300,"seed":7,"force":[0,-9.8],"bounds":[0,64],"threads":4,"chunk":16}`
- 每步 v+=f*dt,p+=v*dt,出界反弹(rest=0.5); 线程/分块划分不影响逐粒子结果(定数)
- 深度排序(降序)+premult alpha 混合输出帧摘要
- 输出 `{"traj":[[x,y]],"blend":[r,g,b,a],"digest","order":[]}`
缺陷基线: 位置依赖循环顺序累加(浮点求和乱序)+按提交顺序 alpha 混合.
