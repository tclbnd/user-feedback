"""统一时间轴输出：帧索引清单与重采样音频。

帧映射
------
对统一参考时间轴 R 上每个目标帧时刻 r_grid，找到本地 tick 满足
map_tick(tick)=r_grid 的相机帧（最近邻 + 半帧保护），输出帧索引清单。
落在未校准有效区间之外的网格时刻标记 calibrated=False，不臆造对应帧。

音频重采样
----------
录音机每个 epoch 的本地采样时钟与 R 满足仿射关系。把每个 epoch 的 PCM
片段按其 slope 用浮点索引线性插值重采样到统一参考采样率，再按映射出的
R 位置拼接；未校准区间用静音填充并在清单中标注，绝不静默丢弃或拉伸。
所有区间都记录映射误差与来源输入片段，便于追溯。
"""
import bisect
import math
import wave

from .model import map_tick, _segment_knots


def build_frame_manifest(frames, device_models, fps, duration_R,
                         max_half_frame=None):
    """生成统一时间轴的帧索引清单。

    frames: list[(epoch, local_tick, R_truth_or_None)]，按采集顺序。
    device_models: pipeline 输出的该设备 epoch 模型 dict。
    返回 list[dict]，每项含 grid_R、frame_index（无映射为 None）、
    epoch、calibrated、error_ms（有真值时）。查找用二分，整体 O(n log n)。
    """
    if max_half_frame is None:
        max_half_frame = 0.5 / fps
    # 按 epoch 分桶并按本地 tick 排序，保留全局帧下标
    by_epoch = dict()
    for global_idx, (epoch, tick, truth) in enumerate(frames):
        by_epoch.setdefault(epoch, []).append((tick, truth, global_idx))
    for ep in by_epoch:
        by_epoch[ep].sort()
    # 预提取每 epoch 的 tick 数组，供二分复用，避免网格内重复构造
    ticks_by_epoch = dict((ep, [m[0] for m in marks])
                          for ep, marks in by_epoch.items())
    # 每个 epoch 在参考时间轴上的 R 覆盖域。重启后不同 epoch 的本地 tick
    # 会重复，必须用 R 域消歧：网格点只在覆盖它的那个 epoch 内取帧。
    R_domain = dict()
    for ep, res in device_models.items():
        if not res["calibrated"]:
            continue
        r_vals = [seg["slope"] * seg["tick_lo"] + seg["offset"]
                  for seg in res["segments"]]
        r_vals += [seg["slope"] * seg["tick_hi"] + seg["offset"]
                   for seg in res["segments"]]
        R_domain[ep] = (min(r_vals), max(r_vals))
    n_grid = int(duration_R * fps)
    manifest = []
    for k in range(n_grid):
        grid_R = (k + 0.5) / fps
        found = None
        for ep, marks in by_epoch.items():
            res = device_models.get(ep)
            if res is None or not res["calibrated"]:
                continue
            # R 域过滤：跳过不覆盖该网格时刻的 epoch（含外推余量）
            dom_lo, dom_hi = R_domain[ep]
            if not (dom_lo - 1.0 / fps <= grid_R <= dom_hi + 1.0 / fps):
                continue
            target_tick = _invert_map(res, grid_R)
            if target_tick is None:
                continue
            ticks_only = ticks_by_epoch[ep]
            pos = bisect.bisect_left(ticks_only, target_tick)
            # 比较插入点两侧候选，按“映射后 R 与网格 R 的偏差”择优，
            # 这样帧时间戳抖动不会因本地 tick 接近而误选
            cand = []
            if pos < len(marks):
                cand.append(pos)
            if pos > 0:
                cand.append(pos - 1)
            best = None
            for i in cand:
                tick_i, truth_i, gidx_i = marks[i]
                R_i = map_tick(res, tick_i)
                if R_i is None:
                    continue
                d = abs(R_i - grid_R)
                if best is None or d < best[0]:
                    best = (d, i, tick_i, truth_i, gidx_i, R_i)
            if best is None:
                continue
            d, idx, tick, truth, global_idx, R_est = best
            # 允许一个帧周期的选择容差（最近邻网格的理论上限半帧）
            if d > 1.0 / fps + max_half_frame:
                continue
            # 映射误差 = 该帧映射出的 R 与其真实采集 R 之差（时钟标定误差），
            # 而非网格采样差；后者是最近邻取帧的固有量化（上限半个帧周期）
            map_err_ms = (abs(R_est - truth) * 1000.0
                          if truth is not None else None)
            found = dict(grid_R=grid_R, frame_index=global_idx, epoch=ep,
                         calibrated=True, local_tick=tick,
                         mapped_R=R_est, error_ms=map_err_ms,
                         grid_quantization_ms=d * 1000.0,
                         source_segment=ep)
            break
        if found is None:
            found = dict(grid_R=grid_R, frame_index=None, epoch=None,
                         calibrated=False, local_tick=None, mapped_R=None,
                         error_ms=None, grid_quantization_ms=None,
                         source_segment=None)
        manifest.append(found)
    return manifest


def _invert_map(epoch_result, R):
    """求 R 在该 epoch 模型下对应的本地 tick（逐分段求解并校验有效区间）。"""
    segs = epoch_result["segments"]
    if not segs:
        return None
    for seg in segs:
        if abs(seg["slope"]) < 1e-12:
            continue
        tick = (R - seg["offset"]) / seg["slope"]
        # 落在该分段锚点覆盖域（含外推余量）即采用
        if seg["tick_lo"] - 30.0 <= tick <= seg["tick_hi"] + 30.0:
            return tick
    seg = segs[0]
    if abs(seg["slope"]) < 1e-12:
        return None
    return (R - seg["offset"]) / seg["slope"]



def resample_audio(segments, device_models, out_rate, duration_R,
                   n_channels=1, sampwidth=2, tail_pad=0.5):
    """把分 epoch 的音频片段重采样并拼接到统一参考时间轴。

    segments: list[dict(epoch, pcm_bytes, local_rate, start_tick, end_tick)]
    tail_pad: 输出尾部额外保留的秒数，容纳落在时长边界的末端脉冲。
    返回 (out_pcm, manifest)。manifest 逐段记录 R 范围、输入片段、
    残差与状态；未校准区间以静音填充并标记 uncalibrated。
    """
    total_samples = int((duration_R + tail_pad) * out_rate)
    out = bytearray(total_samples * n_channels * sampwidth)
    manifest = []
    cursor_R = 0.0
    for seg in sorted(segments, key=lambda s: s["epoch"]):
        ep = seg["epoch"]
        res = device_models.get(ep)
        if res is None or not res["calibrated"]:
            manifest.append(dict(epoch=ep, status="uncalibrated",
                                 R_range=None, source="segment-%d" % ep,
                                 residual_ms=None, filled_silence=True))
            continue
        # 重采样需要覆盖整个采集片段，包括有效区间外几十毫秒的边界。
        # 这里不用 map_tick 的有效区间门限，而是直接用模型分段做仿射外推
        # （外推距离远小于 30 s 上限，属 README 允许的范围）。
        def free_R(tick):
            seg = res["segments"][0]
            return seg["slope"] * tick + seg["offset"], tick

        R_start, t_start = free_R(seg["start_tick"])
        R_end, t_end = free_R(seg["end_tick"])
        if R_start is None or R_end is None:
            manifest.append(dict(epoch=ep, status="uncalibrated",
                                 R_range=None, source="segment-%d" % ep,
                                 residual_ms=None, filled_silence=True))
            continue
        # 按夹紧后的 tick 范围截取 PCM，避免越界部分被错误拉伸
        skip = max(0, int(round((t_start - seg["start_tick"])
                                * seg["local_rate"])))
        keep = max(0, int(round((t_end - t_start) * seg["local_rate"])))
        pcm_in = seg["pcm_bytes"][skip * sampwidth:
                                  (skip + keep) * sampwidth]
        # 填充本段之前的未覆盖间隙（静音）
        if R_start > cursor_R + 1e-6:
            manifest.append(dict(epoch=None, status="gap",
                                 R_range=[cursor_R, R_start],
                                 source=None, residual_ms=None,
                                 filled_silence=True))
        # 浮点索引线性插值重采样。整数率转换无法表达 ppm 级速率差，
        # 故直接用仿射映射 R = slope*t + offset 把每个输出样本位置
        # 反算到源片段的本地样本索引，精确补偿振荡器速率。
        # epoch 内若存在连续速率变化（多个 model segment），按输出 R
        # 所在分段选择对应的 slope/offset，避免全段误用首段速率。
        model_segs = res["segments"]
        knots = _segment_knots(model_segs) if len(model_segs) > 1 else []
        src = struct_unpack_i16(pcm_in)
        src_n = len(src)
        local_rate = seg["local_rate"]
        start_sample = int(round(R_start * out_rate))
        # 含端点：跨度对应样本数 +1，避免末端脉冲因取整少写一个样本
        n_out = int(round((R_end - R_start) * out_rate)) + 1
        filled = 0
        for j in range(n_out):
            out_idx = start_sample + j
            if out_idx >= total_samples:
                break
            R = R_start + j / out_rate
            mseg = _segment_for_R(model_segs, knots, R)
            slope, offset = mseg["slope"], mseg["offset"]
            # 绝对本地 tick t 满足 R = slope*t + offset；源片段 PCM 的
            # 样本索引相对片段起点 t_start，故减去 t_start 再换算
            abs_tick = (R - offset) / slope
            src_tick = abs_tick - t_start
            fi = src_tick * local_rate
            i0 = int(math.floor(fi))
            if i0 < 0:
                continue
            # 末端脉冲可能恰好落在最后一个样本：钳位而非跳过
            if i0 >= src_n - 1:
                i0 = src_n - 2 if src_n >= 2 else 0
            frac = min(1.0, max(0.0, fi - i0))
            value = src[i0] * (1.0 - frac) + src[i0 + 1] * frac
            out[out_idx * sampwidth:out_idx * sampwidth + 2] = \
                struct_pack_i16(value)
            filled += 1
        end_sample = min(total_samples, start_sample + n_out)
        manifest.append(dict(
            epoch=ep, status="calibrated",
            R_range=[R_start, R_end], source="segment-%d" % ep,
            residual_ms=res["residual_ms"], filled_silence=False,
            slopes=[s["slope"] for s in model_segs],
            samples_written=filled,
            out_sample_range=[start_sample, end_sample]))
        cursor_R = max(cursor_R, R_end)
    return bytes(out), manifest


def _segment_for_R(model_segs, knots, R):
    """按输出 R 选择对应的模型分段。

    先用各段映射的 R 覆盖域判断；R 落在所有锚点覆盖之外（片段两端的
    外推区）时，取 R 域边界距离最近的分段，而不是默认最后一段，避免
    起点外推误用高速率分段。
    """
    if len(model_segs) == 1:
        return model_segs[0]
    bounds = []
    for seg in model_segs:
        r_lo = seg["slope"] * seg["tick_lo"] + seg["offset"]
        r_hi = seg["slope"] * seg["tick_hi"] + seg["offset"]
        bounds.append((min(r_lo, r_hi), max(r_lo, r_hi), seg))
    for lo, hi, seg in bounds:
        if lo <= R <= hi:
            return seg
    # 外推区：选边界最近的分段
    def gap(b):
        lo, hi, _ = b
        return min(abs(R - lo), abs(R - hi))
    return min(bounds, key=gap)[2]


def struct_unpack_i16(pcm):
    import struct
    return [v[0] / 32767.0 for v in struct.iter_unpack("<h", pcm)]


def struct_pack_i16(value):
    import struct
    value = max(-1.0, min(1.0, value))
    return struct.pack("<h", int(round(value * 32767)))


def write_wav(path, pcm, rate, n_channels=1, sampwidth=2):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        wf.writeframes(pcm)
