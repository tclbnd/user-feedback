# 虚拟纹理页表与 Mip 驻留
`python3 vt.py req.json output.json [chunks] [threads]`
req.json: `{"pages":256,"phys":64,"grid":16,"frames":[[{pid,mip,area}]],"bw":8}`
- 每帧请求先按 (pid,mip) 聚合去重(面积取最大), 再按 (mip asc, area desc, pid asc)
  进入优先级队列; 带宽 `bw` 限制每帧换页数, 未满足请求带帧龄跨帧迟滞
  (MAX_PENDING_AGE), 缺页沿四叉树父链回退到最近驻留祖先
- 两阶段提交: prepare 阶段只在暂存区选槽+模拟 IO, 不动活动页表; commit 先安装
  新映射再回收旧物理页; IO 失败整体回滚, 上级 Mip 映射保持有效(回退祖先本帧保护)
- 输出 `{"resident":[[pid]],"resident_detail":[[[pid,mip,slot]]],"faults":[n],
  "fallbacks":[n],"hard_faults":[n],"pageins":[n],"rollbacks":[n],
  "pending":[[[pid,mip]]],"digest"}`; digest 为规范页表摘要, 与分块/线程/重放顺序无关
- 验收: `python3 test_vt.py`(手工参考逐帧核对、确定性重排、高速回放硬缺页帧 <=5%、
  故障注入回滚、迟滞老化、CLI 端到端)
