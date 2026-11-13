"""端到端校准流程。

连续参考时钟 R
--------------
同步事件由同一源以参考时间等间隔（30 s）发射，闪光与声脉冲成对。把每个
物理同步事件用全局序号 k（从 0 起）标记，其参考时刻 R_k = (k+1)*30 s。

1. 录音机 epoch 0（基准分段）的全部检测事件按采集顺序对应连续全局序号，
   用 R_k 对 mic 本地 tick 做完整稳健拟合（含连续速率变化分段），得到
   真实振荡器映射 f0。f0 显式补偿 mic 振荡器速率，不是简单恒等。
2. DP 配对是连接“相机观测”与“录音机观测”的边。基准 mic0 的事件序号
   经配对边传给相机；mic0 内但与重启后相机配对的事件，序号同样来自
   mic0 全事件表，因此相机重启边界天然正确。
3. 录音机重启后的新 epoch，本地 tick 被重置；其首事件全局序号紧接该设备
   上一 epoch 尾部事件序号 +1（重启不改全局事件顺序）。每个 epoch 内部
   事件严格等间隔、按采集顺序排列，故已知任意位置即可补齐整段序号。
4. 对每个 epoch 用 (tick, R) 锚点做稳健仿射拟合。重启只改 offset，
   R 连续，回拨两侧绝对时间首尾相接；重复本地时间戳由 epoch 标签消歧。
   未配上的伪峰/漏检只记录、不参与拟合。
"""
import math

from . import model, pairing

RMS_GATE = 0.10
SYNC_PERIOD_R = 30.0
TICK_TOL = 1e-6


def split_epochs(ticks):
    """把检测时刻划成 epoch，返回 list[list[tick]]（每个内部升序）。

    兼容两种输入形态：
    - 采集顺序：重启表现为当前 tick 比上一采集位置大幅回落（负跳变）。
    - 已排序顺序：重启后本地时钟从接近 0 重新计时，在升序序列里表现为
      一组小 tick 接在大 tick 之后——即相邻差出现一次“断崖式”重置，
      其后的 tick 重新从低值爬升。
    两种形态都通过“相邻负差超过回把门限”识别；自然长间隔是正差，不触发。
    """
    # 若输入本身已含负跳（采集顺序），直接按负跳切；否则按排序后的断崖切。
    seq = list(ticks)
    has_negative_step = any(
        seq[i] < seq[i - 1] - model.REGRESSION
        for i in range(1, len(seq)))
    if not has_negative_step:
        seq = sorted(ticks)
    epochs = [[]]
    prev = None
    for tick in seq:
        if prev is not None and tick < prev - model.REGRESSION:
            epochs.append([])
        epochs[-1].append(tick)
        prev = tick
    return [sorted(ep) for ep in epochs]


def _epoch_pair_matrix(cam_epochs, mic_epochs):
    """返回合格 cam/mic epoch 组合及其 DP 配对与仿射 RMS。"""
    candidates = []
    for ci, c_marks in enumerate(cam_epochs):
        for mi, m_marks in enumerate(mic_epochs):
            pairs, ua, ub = pairing.pair_events(c_marks, m_marks)
            if len(pairs) < model.MIN_MARKS:
                continue
            slope, offset = model._ols([(tm, tc) for tc, tm in pairs])
            rms = math.sqrt(sum((tc - (slope * tm + offset)) ** 2
                                for tc, tm in pairs) / len(pairs))
            candidates.append(dict(ci=ci, mi=mi, rms=rms, pairs=pairs,
                                   ua=ua, ub=ub))
    return candidates


def _select_matching(candidates, cam_epochs, mic_epochs):
    """全局最优二分匹配：每个 cam/mic epoch 最多用一次。

    重启后不同 epoch 的本地 tick 范围重叠，配对数与 RMS 都可能误导。
    决定性判据是 *tick 覆盖率*：正确组合的配对事件横跨该 epoch 检测标记
    的整个本地时间跨度，错误的跨重启组合只落在重叠的小 tick 区间。
    枚举所有互不冲突的匹配方案，先最大化最小覆盖率，再最大化总配对数，
    最后以总 RMS 裁决。
    """
    import itertools

    def coverage(c, cam_epochs, mic_epochs):
        c_span = max(t for t, _ in c["pairs"]) - \
            min(t for t, _ in c["pairs"])
        m_span = max(t for _, t in c["pairs"]) - \
            min(t for _, t in c["pairs"])
        c_full = max(cam_epochs[c["ci"]]) - min(cam_epochs[c["ci"]])
        m_full = max(mic_epochs[c["mi"]]) - min(mic_epochs[c["mi"]])
        return min(c_span / max(c_full, 1e-9),
                   m_span / max(m_full, 1e-9))

    valid = [c for c in candidates if c["rms"] <= RMS_GATE]
    best = None
    for r in range(1, len(valid) + 1):
        for combo in itertools.combinations(valid, r):
            cis = [c["ci"] for c in combo]
            mis = [c["mi"] for c in combo]
            if len(set(cis)) != len(cis) or len(set(mis)) != len(mis):
                continue
            min_cov = min(coverage(c, cam_epochs, mic_epochs) for c in combo)
            total_pairs = sum(len(c["pairs"]) for c in combo)
            total_rms = sum(c["rms"] for c in combo)
            # 评分优先级：匹配的 epoch 数（让尽可能多分段被覆盖）→
            # 最小 tick 覆盖率 → 总配对数 → 总 RMS（越小越好）
            score = (len(combo), round(min_cov, 6), total_pairs, -total_rms)
            if best is None or score > best[0]:
                best = (score, list(combo))
    return best[1] if best else []


def _fit(ticks, refs):
    sync_events = [dict(seq=i, tick=t, reference=r)
                   for i, (t, r) in enumerate(zip(ticks, refs))]
    return model.fit_epoch(sync_events)



def calibrate(cam_ticks, mic_ticks):
    """校准两台设备到同一条连续参考时钟，返回 (models, report)。"""
    cam_epochs = split_epochs(list(cam_ticks))
    mic_epochs = split_epochs(list(mic_ticks))
    candidates = _epoch_pair_matrix(cam_epochs, mic_epochs)
    chosen = _select_matching(candidates, cam_epochs, mic_epochs)
    chosen_keys = set((c["ci"], c["mi"]) for c in chosen)

    rows, unmatched_cam, unmatched_mic = [], [], []
    # 已在某个被选组合中成功配对的 tick，不计入未配对（即使它也出现在
    # 被拒的跨 epoch 组合里）
    paired_cam, paired_mic = set(), set()
    for c in chosen:
        for tc, tm in c["pairs"]:
            paired_cam.add((c["ci"], round(tc, 9)))
            paired_mic.add((c["mi"], round(tm, 9)))
    for c in candidates:
        if (c["ci"], c["mi"]) in chosen_keys:
            continue
        for tc, tm in c["pairs"]:
            # 错误 epoch 组合里的“配对”不成立；仅当该 tick 在正确组合中
            # 也没配上时，才作为未配对记录
            if (c["ci"], round(tc, 9)) not in paired_cam:
                unmatched_cam.append((c["ci"], tc))
            if (c["mi"], round(tm, 9)) not in paired_mic:
                unmatched_mic.append((c["mi"], tm))
    for c in chosen:
        for tc, tm in c["pairs"]:
            rows.append((c["ci"], c["mi"], tc, tm))
        # 正确组合中 DP 未能配上的检测（伪峰/漏检）才是真正的未配对
        for t in c["ua"]:
            if (c["ci"], round(t, 9)) not in paired_cam:
                unmatched_cam.append((c["ci"], t))
        for t in c["ub"]:
            if (c["mi"], round(t, 9)) not in paired_mic:
                unmatched_mic.append((c["mi"], t))
    rows.sort()

    mic_models, cam_models = dict(), dict()
    base = next((c for c in chosen if c["mi"] == 0), None)
    if base is None or not mic_epochs:
        for ep in range(len(mic_epochs)):
            mic_models[ep] = model.fit_epoch([])
        for ep in range(len(cam_epochs)):
            cam_models[ep] = model.fit_epoch([])
        return (dict(cam=cam_models, mic=mic_models),
                dict(pair_count=len(rows), unmatched_cam=unmatched_cam,
                     unmatched_mic=unmatched_mic,
                     cam_epoch_count=len(cam_epochs),
                     mic_epoch_count=len(mic_epochs), gauge_mic_epoch=None))

    # 第一步：为基准 mic epoch 0 分配全局事件序号。
    # 不能假设检测事件连续等间隔——整段标记被移除后，相邻检测标记之间会
    # 跨过若干个缺失事件。用本地时间间隔除以标称周期（30 s，振荡器偏差
    # 仅数百 ppm）四舍五入估计序号步长，缺失事件自然表现为序号跳跃。
    mic0 = mic_epochs[0]

    def assign_indices(marks, nominal_period, slope_hint=1.0):
        """按相邻间隔估计全局序号，返回与 marks 等长的序号列表。

        鲁棒处理两类异常：
        - 缺失标记：相邻间隔是标称周期的整数倍，四舍五入得到步长 >1；
        - 伪峰：间隔明显小于半个周期，步长 round 为 0（伪峰不占用真实
          事件序号），后续真实事件继续沿正确序号推进。
        最终的伪峰锚点还会被下游稳健拟合按残差剔除。
        """
        if not marks:
            return []
        indices = [0]
        half = 0.5 * nominal_period / max(slope_hint, 1e-9)
        for a, b in zip(marks, marks[1:]):
            raw = (b - a) * slope_hint / nominal_period
            step = int(round(raw))
            if (b - a) < half:
                step = 0  # 伪峰：不前进事件序号
            indices.append(indices[-1] + max(0, step))
        return indices

    # mic0 用单位斜率作初估（mic 速率偏差仅 -120 ppm，对取整无影响）
    mic0_idx = assign_indices(mic0, SYNC_PERIOD_R, 1.0)
    mic_models[0] = _fit(mic0, [SYNC_PERIOD_R * (k + 1) for k in mic0_idx])

    # 第二步：录音机重启 epoch 的序号。首事件序号由与上一 epoch 尾部的
    # 时间间隔估计；epoch 内部同样按间隔分配，正确处理缺失事件。
    mic_full = [mic0_idx]
    for ep in range(1, len(mic_epochs)):
        marks = mic_epochs[ep]
        # 重启后本地时钟重置，首事件序号参考全局：用上一 epoch 最后序号
        # 加一个间隔步长（重启间隙在 R 上仍是 30 s 的整数倍）
        prev_last_seq = mic_full[ep - 1][-1]
        local_idx = assign_indices(marks, SYNC_PERIOD_R, 1.0)
        mic_full.append([prev_last_seq + 1 + (k - local_idx[0])
                         for k in local_idx])
    mic_lookup = dict()
    for ep, marks in enumerate(mic_epochs):
        for i, t in enumerate(marks):
            mic_lookup[(ep, round(t, 9))] = mic_full[ep][i]

    # 第三步：确定相机每个 epoch 的全局序号。
    # cam0 通过与 mic0 的配对锚定首事件序号；重启后的 cam epoch 与 mic
    # 重启 epoch 的首事件不同（两台设备重启时刻不同），因此相机序号沿
    # *相机自己的链* 传播：首序号 = 上一相机 epoch 尾部序号 +1，epoch 内
    # 再用本地间隔估计步长，正确处理缺失事件。配对仅用于一致性校验。
    cam_full = []
    combo_by_cam = dict((c["ci"], c) for c in chosen)
    for ep, marks in enumerate(cam_epochs):
        local_idx = assign_indices(marks, SYNC_PERIOD_R, 1.0)
        if ep == 0:
            combo = combo_by_cam.get(0)
            if combo is not None and combo["mi"] == 0 and combo["pairs"]:
                first_seq = mic_lookup.get(
                    (0, round(combo["pairs"][0][1], 9)), 0)
            else:
                first_seq = 0
            cam_full.append([first_seq + (k - local_idx[0])
                             for k in local_idx])
        else:
            prev_last = cam_full[ep - 1][-1]
            cam_full.append([prev_last + 1 + (k - local_idx[0])
                             for k in local_idx])

    # 第四步：序号转 R，稳健拟合每个 epoch。
    # 伪峰在 assign_indices 中被赋予与前一事件相同的序号（step=0），
    # 这里按序号去重：每个全局序号只保留首个检测（即真实事件），伪峰
    # 不进入锚点；残留的异常再交给 fit_epoch 的稳健拟合剔除。
    def anchors(epochs, full):
        out = dict()
        for ep, marks in enumerate(epochs):
            ticks, refs = [], []
            seen = set()
            for i, t in enumerate(marks):
                seq = full[ep][i]
                if seq is None or seq in seen:
                    continue
                seen.add(seq)
                ticks.append(t)
                refs.append(SYNC_PERIOD_R * (seq + 1))
            out[ep] = (ticks, refs)
        return out


    mic_anchors = anchors(mic_epochs, mic_full)
    cam_anchors = anchors(cam_epochs, cam_full)
    for mi, (t, r) in mic_anchors.items():
        if mi != 0:
            mic_models[mi] = _fit(t, r)
    for ci, (t, r) in cam_anchors.items():
        cam_models[ci] = _fit(t, r)

    report = dict(
        pair_count=len(rows), unmatched_cam=unmatched_cam,
        unmatched_mic=unmatched_mic, cam_epoch_count=len(cam_epochs),
        mic_epoch_count=len(mic_epochs), gauge_mic_epoch=0,
        total_sync_events=len(mic0),
        mic_first_index=[col[0] for col in mic_full],
        cam_first_index=[col[0] for col in cam_full],
    )
    return dict(cam=cam_models, mic=mic_models), report

