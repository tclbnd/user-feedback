# 多光源阴影图集分配与级联归属

```
python3 atlas.py req.json output.json
```

## 输入

```jsonc
{
  "lights": [{"id": "L1", "prio": 10, "influence": 0.8, "dist": 12,
              "type": "point", "tile": [128, 128]}],
  "slots": 8,
  "frames": [{"cam_z": 12.0}],
  // 可选
  "cascade": {"near": 0, "far": 100, "count": 4, "hysteresis_ratio": 0.01},
  "atlas":   {"width": 1024, "height": 1024, "granularity": 32, "gap": 0,
              "exact_sizes": false}
}
```

* `lights` 是静态灯表；排序键为 `(prio 降序, influence 降序, dist 升序, id 升序)`。
  最后一级 `id` 只做确定性兜底，因此灯表顺序不影响结果。
* 未写 `id` 的灯用自身属性哈希得到形如 `auto:xxxxxxxxxxxxxxxx` 的稳定身份，
  这样重排灯表也不会改变摘要；两盏属性完全相同又都没有 `id` 的灯无法区分，
  会以 `duplicate_anonymous_light` 上报（不会按列表下标编造名字）。
* `type` 为 `dir`/`directional`/`sun` 或其 `directional` 标志为真时走级联；
  `point`/`spot`/`area` 等固定槽位图集灯恒为级联 `0`；完全未标注类型时按方向光处理。
* `frames[].cam_z` 为相机沿视锥轴的深度（也接受 `camera_z` / `z` / `cam`）。
* `frames[].lights` 可给出该帧的灯属性覆盖：`{id: {...}}` 或完整列表。缺省即静态。
* `slots` 缺省 8；`0` 表示本帧不分配任何槽位（全部进 `evict`）。

## 输出

```jsonc
{
  "alloc":   {"0": {"L1": 0, "L2": 3}},   // 帧 -> 灯 -> 槽位（稳定）
  "evict":   ["L9"],                       // 全程出现过的被挤出灯（去重、首次出现顺序）
  "evict_frames": {"0": ["L9"]},           // 逐帧挤出集合
  "order":   {"0": ["L1", "L2", ...]},     // 逐帧排序结果（含被挤出灯）
  "cascade": {"0": {"sun": 1}},            // 帧 -> 方向光 -> 级联序号
  "cascade_switches": [{"frame": 3, "light": "sun", "from": 0, "to": 1}],
  "cascade_switch_count": {"sun": 1},
  "cascade_config": {"bounds": [0, 25, 50, 75, 100], "hysteresis": 1,
                     "overlaps": [[24, 26, 0, 1], ...], "tie_rule": "lower"},
  "regions": {"0": {"L1": {"x": 0, "y": 0, "w": 128, "h": 128}}},  // 仅当给了 atlas
  "failures": [{"frame": 0, "light": "L7", "reason": "indivisible_size", "detail": {}}],
  "digest": "...",          // 分配摘要（alloc + evict + 失败原因 + regions）
  "cascade_digest": "..."   // 级联归属摘要
}
```

## 规则

**槽位分配（稳定性优先）**
1. 已有映射原样保留，绝不为了"更好看"而重排；被挤出的灯不会让其它灯换槽。
2. 重新入选的灯优先回到自己原来的槽位；真·新灯按排名取最小空闲槽。
3. 槽位号只在 `[0, slots)` 内取，且不会有两个灯共用同一槽位。

**级联归属（滞回）**
* 分界点集合为 `bounds`；相机恰在分界点上时归**低**一级联（`tie_rule: "lower"`）。
* 每个内部分界点有宽 `2 × hysteresis` 的重叠带（`overlaps` 用 `[lo, hi, 低, 高]` 描述）。
  相机在带内时保持上一次的归属，只有越过 `boundary ± hysteresis` 才切换；
  因此边界附近小幅振荡只会切换 1 次，`cascade_switch_count` 可作验收指标。
* `hysteresis` 缺省为级联深度的 1%（`hysteresis_ratio: 0.01`）。

**图集打包（可选）**
* 按分配优先级做 first-fit 行式打包，`granularity` 为最小可分配格。
* 失败不会中断整帧：灯仍保有逻辑槽位，只是没有 `region`，并写入 `failures`：
  * `indivisible_size` —— `exact_sizes` 下灯尺寸不是 `granularity` 的整数倍；
  * `oversize` —— 单块尺寸超过图集；
  * `pack_failed` —— 图集剩余空间不足；
  * `invalid_tile` —— 尺寸非正数。

`failures` 里另外可能出现两类输入问题（`frame` 为 `null`，表示与具体帧无关）：
`duplicate_light_id`（显式 id 重复）与 `duplicate_anonymous_light`（无 id 且属性完全相同）。

**摘要稳定**
`digest` 与 `cascade_digest` 只依赖分配结果与失败原因，均按帧号/灯名排序后再哈希：
随机重排 `lights` 列表、相机在非边界处微动都不会改变它们。

## 测试

```
python3 test_atlas.py
```

覆盖验收场景：12 灯 / 8 槽的排序与集合、低优先级挤出后其余映射不变、
级联边界振荡的切换次数与滞回带、灯表重排后摘要一致、矩形打包失败与尺寸不可分。
另外覆盖：无 `id` 灯的稳定身份、逐帧优先级覆盖、返回灯回收原槽位、以及
`slots=0` / 空输入 / 非有限数值等降级路径。
