# 虚拟纹理页表与 Mip 驻留

```
python3 vt.py req.json output.json [--threads N] [--chunk K] [--hysteresis H]
```

`req.json`: `{"pages":256,"phys":64,"frames":[[{pid,mip,area}]],"bw":8}`

`pid` 按不透明句柄处理 (不校验是否 < `pages`); `pages` 只用来推导 mip 金字塔
的几何关系; 若输入自带 `parent` (pid 或 `{"pid":..,"mip":..}`) 则以显式父子
关系为准.

- 每帧请求先做规范化聚合: 同一 pid 的重复记录按 (mip 低, area 大) 合并,
  再按 `(mip asc, area desc, pid asc)` 排优先级队列. 队列顺序与记录的到达
  顺序、分块粒度、线程数无关.
- 队首前 `phys` 个请求构成该帧的目标驻留集合; 已在驻留的页只做 touch,
  不会重复换入. 每帧换页数受 `bw` 限制, 超预算的请求保留旧的 (更粗 mip)
  映射.
- 跨帧迟滞 (`--hysteresis`, 默认 1): 最近请求过但本帧不再请求的页会多留
  几帧, 抑制换页抖动导致同一页隔帧反复加载. 这类页优先于本帧新请求页被回收.
- 淘汰顺序: 先回收过期页, 再回收本帧不再需要的细节页; 粗粒度主干页最后淘汰.
  正在为某个回退项提供映射的页不会被回收, 因此"回退到上级 mip 的映射保持
  有效".
- 每帧两阶段提交: 安装事务先提交新映射, 之后回收事务才丢弃被替换的映射并
  归还物理槽. 事务切换期间新旧映射同时可寻址, 请求不会落到已释放的槽上.
- 输出 `{"resident":[[pid]],"faults":[n],"digest"}`; `digest` 是对逐帧页表
  摘要 (含 resident/fallback/missing 与槽位归属) 的 sha256, 请求重放顺序
  变化不改变它.

## 测试

```
python3 -m unittest discover -s tests -v
```

`tests/test_vt.py` 内含一份独立参照实现 (`ReferencePlanner`), 逐帧比对驻留
集合与缺页数, 并覆盖: 重放顺序/分块/线程数不变性、回退映射有效性、两阶段
提交无槽位别名、高速回放带宽预算与 5% 缺页帧预算.
