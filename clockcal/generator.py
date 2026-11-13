"""带真值的双设备素材生成器。

物理模型：连续参考时间 R（秒，从 0 开始）。每台设备由独立振荡器驱动，
其本地时钟 t 与 R 的真实关系在每个采集分段（重启 session）内为

    R = rate * t + offset

分段内部还可能出现一次 *连续* 的速率变化（速率突变但时间不跳）。
设备重启后本地时钟从接近 0 重新计时，于是同一个本地时间戳会在多个
分段重复出现——这正是 epoch 划分要解决的问题。

同步事件每 30 秒（R 轴）同时产生一次闪光与一声 1 kHz 提示音。
"""
import math
import random

DURATION_R = 2400.0          # 40 分钟
SYNC_PERIOD = 30.0
CAM_FPS = 30.0
AUDIO_RATE = 2000      # 1 kHz 同步脉冲满足奈奎斯特，纯标准库处理更快
CAM_JITTER = 0.0012          # 帧时间戳抖动 1.2 ms
DETECT_JITTER = 0.0008       # 检测亚帧/亚采样误差 0.8 ms


class Device:
    """一台设备的真实时钟模型（含重启与一次连续速率变化）。"""

    def __init__(self, name, rng, rate_ppm, restart_R,
                 rate_change_R=None, rate_change_ppm=None):
        self.name = name
        self.rng = rng
        self.restart_R = restart_R
        # 每个 session: (R_start, R_end, rate, t_offset_to_R)
        base_rate = 1.0 + rate_ppm * 1e-6
        self.sessions = []
        # session 0
        if rate_change_R is not None and rate_change_R < restart_R:
            r1 = base_rate
            r2 = 1.0 + rate_change_ppm * 1e-6
            t_at_change = rate_change_R / r1
            off2 = rate_change_R - r2 * t_at_change
            self.sessions = [
                dict(R0=0.0, R1=rate_change_R, rate=r1, offset=0.0,
                     t0=0.0, t1=t_at_change),
                dict(R0=rate_change_R, R1=restart_R, rate=r2, offset=off2,
                     t0=t_at_change, t1=(restart_R - off2) / r2),
            ]
        else:
            t_restart = restart_R / base_rate
            self.sessions = [dict(R0=0.0, R1=restart_R, rate=base_rate,
                                  offset=0.0, t0=0.0, t1=t_restart)]
        # 重启后的 session，本地时钟从 0 重新计时（保留少量初始偏移）
        t_init = 5.0 + rng.uniform(0, 5)
        off = restart_R - base_rate * t_init
        self.sessions.append(dict(R0=restart_R, R1=DURATION_R,
                                  rate=base_rate, offset=off,
                                  t0=t_init,
                                  t1=t_init + (DURATION_R - restart_R) / base_rate))

    def to_local(self, R):
        """真值：参考时刻 R -> 设备本地 tick。"""
        for s in self.sessions:
            if s["R0"] - 1e-9 <= R <= s["R1"] + 1e-9:
                return (R - s["offset"]) / s["rate"]
        raise ValueError("R outside device lifetime")

    def to_R(self, tick, session_index):
        """真值：某 session 内本地 tick -> R。"""
        s = self.sessions[session_index]
        return s["rate"] * tick + s["offset"]

    def session_at_R(self, R):
        for i, s in enumerate(self.sessions):
            if s["R0"] - 1e-9 <= R <= s["R1"] + 1e-9:
                return i
        return len(self.sessions) - 1


def build_devices(rng):
    """构造相机与录音机：相机在 20 min 重启，录音机在 27 min 重启，
    录音机在 12 min 有一次连续速率变化。"""
    # 重启时刻刻意选在两个 30 s 同步事件之间，避免边界事件脉冲被截断
    cam = Device("cam", rng, rate_ppm=+180.0, restart_R=1215.0)
    mic = Device("mic", rng, rate_ppm=-120.0, restart_R=1605.0,
                 rate_change_R=720.0, rate_change_ppm=+260.0)
    return cam, mic


def sync_events_R():
    n = int(DURATION_R // SYNC_PERIOD)
    return [SYNC_PERIOD * (i + 1) for i in range(n)]


def make_checkpoints_R():
    """每 30 秒一个对应检查点（与同步事件时刻重合，共 79 个）。"""
    return sync_events_R()


def simulate(seed=2026, remove_window=None, spurious=0):
    """生成一次完整采集。

    remove_window: (R_lo, R_hi)，窗口内的同步标记在两台设备上都移除，
      且不产生对应闪光/提示音（模拟整段标记缺失）。
    spurious: 注入的局部伪峰数量（相机与录音机各一半左右）。
    返回 dict，含设备真值、检测到的标记、帧/音频生成所需参数、检查点真值。
    """
    rng = random.Random(seed)
    cam, mic = build_devices(rng)
    events_R = sync_events_R()
    removed = []
    if remove_window is not None:
        lo, hi = remove_window
        kept = []
        for R in events_R:
            if lo <= R <= hi:
                removed.append(R)
            else:
                kept.append(R)
        events_R = kept

    def detected(device, R):
        return device.to_local(R) + rng.gauss(0.0, DETECT_JITTER)

    cam_marks = [(R, detected(cam, R), cam.session_at_R(R)) for R in events_R]
    mic_marks = [(R, detected(mic, R), mic.session_at_R(R)) for R in events_R]

    # 伪峰：落在随机位置，彼此不与真标记重合
    cam_spurious, mic_spurious = [], []
    for _ in range(spurious):
        R = rng.uniform(60, DURATION_R - 60)
        side = rng.randrange(2)
        if side == 0:
            t = cam.to_local(R) + rng.uniform(-0.05, 0.05)
            cam_spurious.append((R, t, cam.session_at_R(R)))
        else:
            t = mic.to_local(R) + rng.uniform(-0.05, 0.05)
            mic_spurious.append((R, t, mic.session_at_R(R)))

    # 相机帧：30 fps，每 session 独立按本地时钟出帧，带时间戳抖动
    frames = []  # (session, local_tick, R_truth)
    for si, s in enumerate(cam.sessions):
        t = s["t0"]
        while t <= s["t1"]:
            tick = t + rng.gauss(0.0, CAM_JITTER)
            R_truth = s["rate"] * t + s["offset"]
            frames.append((si, tick, R_truth))
            t += 1.0 / CAM_FPS

    checkpoints = make_checkpoints_R()
    return dict(
        rng=rng, cam=cam, mic=mic,
        events_R=events_R, removed_R=removed,
        cam_marks=cam_marks, mic_marks=mic_marks,
        cam_spurious=cam_spurious, mic_spurious=mic_spurious,
        frames=frames, checkpoints_R=checkpoints,
        duration_R=DURATION_R, cam_fps=CAM_FPS, audio_rate=AUDIO_RATE,
    )


def _pcm_i16(samples):
    """float [-1,1] 采样转 16-bit little-endian PCM（批量打包）。"""
    import struct
    clamped = [max(-1.0, min(1.0, v)) for v in samples]
    return struct.pack("<%dh" % len(clamped),
                       *[int(round(v * 32767)) for v in clamped])


def render_audio_segments(scenario, include_removed=True):
    """按 mic epoch 生成音频 PCM 片段（含提示音、噪声），返回 segment 列表。

    每个 segment: dict(epoch, pcm_bytes, local_rate, start_tick, end_tick,
                       emit_ticks)。被 remove_window 移除的事件不产生提示音。
    """
    from . import detect
    mic = scenario["mic"]
    rate = scenario["audio_rate"]
    removed = set(scenario["removed_R"])
    # 每个 mic epoch 的起止 R（重启 session 即新 epoch；速率变化不拆 epoch）
    epoch_R = []
    sessions = mic.sessions
    # session 0..n-2 中连续速率变化属于同一 epoch；最后一个 session 是重启
    restart_session = len(sessions) - 1
    epoch_R.append((sessions[0]["R0"], sessions[restart_session]["R0"]))
    epoch_R.append((sessions[restart_session]["R0"], DURATION_R))

    rng = scenario["rng"]
    segments = []
    # 片段两端各留 0.5 s 余量，确保首尾同步脉冲完整、不被缓冲区边界截断
    pad_R = 0.5
    for ep, (R0, R1) in enumerate(epoch_R):
        s = sessions[restart_session] if ep == 1 else sessions[0]
        seg_R0 = max(0.0, R0 - pad_R)
        seg_R1 = min(DURATION_R, R1 + pad_R)
        t0 = (seg_R0 - s["offset"]) / s["rate"]
        t1 = (seg_R1 - s["offset"]) / s["rate"]
        duration_tick = t1 - t0
        emits = []
        for R in scenario["events_R"]:
            if not (R0 <= R <= R1) or R in removed:
                continue
            emits.append(mic.to_local(R) - t0)  # 片段内相对 tick
        n = int(round(duration_tick * rate))
        # 快速近似高斯背景噪声（两次均匀分布之差，方差 1/6，再缩放到 sigma）
        samples = [(rng.random() - rng.random()) * 0.0245 for _ in range(n)]
        two_pi_f = 2 * math.pi * 500.0
        for e in emits:
            k0 = max(0, int((e - 0.03) * rate))
            k1 = min(n, int((e + 0.03) * rate))
            for k in range(k0, k1):
                tt = k / rate - e
                env = math.exp(-0.5 * (tt / 0.010) ** 2)
                samples[k] += 0.6 * env * math.cos(two_pi_f * tt)
        # 注入局部伪脉冲（与真标记同频率但出现在无事件位置）
        for R, t_abs, _si in scenario.get("mic_spurious", []):
            if not (seg_R0 <= R <= seg_R1):
                continue
            e = mic.to_local(R) - t0
            k0 = max(0, int((e - 0.03) * rate))
            k1 = min(n, int((e + 0.03) * rate))
            for k in range(k0, k1):
                tt = k / rate - e
                env = math.exp(-0.5 * (tt / 0.010) ** 2)
                samples[k] += 0.6 * env * math.cos(two_pi_f * tt)
        segments.append(dict(
            epoch=ep, pcm_bytes=_pcm_i16(samples), local_rate=rate,
            start_tick=t0, end_tick=t1, emit_ticks=emits,
            emit_R=[R for R in scenario["events_R"] if R0 <= R <= R1
                    and R not in removed]))
    return segments


def render_frame_brightness(scenario, inject_spurious=True):
    """为每个相机帧生成亮度（真标记处闪光），返回与 frames 平行的列表。

    闪光只在该帧所属采集分段（epoch）内的事件上渲染；重启后本地时钟
    重新计时的帧不会重复出现重启前事件的闪光。inject_spurious 为真时
    在随机帧注入局部伪亮度峰，考验检测器与稳健拟合的抗误配能力。
    """
    from . import detect
    cam = scenario["cam"]
    removed = set(scenario["removed_R"])
    valid_R = [R for R in scenario["events_R"] if R not in removed]
    rng = scenario["rng"]
    restart_R = cam.restart_R
    # 伪峰位置（本地 tick）。仅注入到与真标记不重叠的位置
    pseudo_ticks = []
    if inject_spurious:
        for R, t, _si in scenario.get("cam_spurious", []):
            pseudo_ticks.append(t)
    brightness = []
    for epoch, tick, truth_R in scenario["frames"]:
        v = detect.FLASH_BASE + rng.gauss(0.0, 0.005)
        for R in valid_R:
            in_epoch = (R < restart_R) if epoch == 0 else (R >= restart_R)
            if not in_epoch:
                continue
            local = cam.to_local(R)
            v = max(v, detect.flash_brightness(tick, local))
        for pt in pseudo_ticks:
            v = max(v, detect.flash_brightness(tick, pt))
        brightness.append(v)
    return brightness
