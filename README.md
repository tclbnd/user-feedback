# 虚拟纹理页表与 Mip 驻留
`python3 vt.py req.json output.json`
req.json: `{"pages":256,"phys":64,"frames":[[{pid,mip,area}]],"bw":8}`
- 每帧请求按 (mip asc, area desc) 入队; 带宽限制换页数; 缺页回退上级 mip
- 两阶段提交: 新页驻留后旧映射才回收; 输出 `{"resident":[[pid]],"faults":[n],"digest"}`
缺陷基线: 每帧全量换页(无驻留记忆),无回退.
