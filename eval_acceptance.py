"""验收脚本: 合成标注数据集, 验证全部验收标准。

1) Precision/Recall >= 0.85/0.80, 起始点时间偏差中位数 <= 20ms,
   弱起音漏检数 <= 缺陷基线(固定能量阈值)的 1.2 倍。
2) 两组 (frame,hop) 复跑, 起始点集合匹配率 >= 95%。
3) 乱序分段处理后 digest 与整段处理一致。
"""
import math, random, statistics, json, os, subprocess, sys
import onset

SR = 16000
TMP = os.environ.get("TMPDIR", "/tmp")

# ---------------- 合成标注数据集 ----------------

def synth_dataset(sr=SR, dur_s=20.0, seed=7):
    rnd = random.Random(seed)
    n = int(sr * dur_s)
    audio = [0.0] * n
    # 低电平背景噪声
    for i in range(n):
        audio[i] = 0.004 * (rnd.random() * 2 - 1)
    ann = []  # (t_ms, strength)

    def add_tone(t_s, amp, freq, decay_s=0.25, label="strong"):
        i0 = int(t_s * sr)
        ln = int(0.6 * sr)
        fade = int(0.03 * sr)  # 30ms 淡出, 避免硬截断产生偏移咔哒声
        for j in range(ln):
            i = i0 + j
            if i >= n:
                break
            env = math.exp(-j / (decay_s * sr))
            if j > ln - fade:
                env *= 0.5 * (1.0 + math.cos(math.pi * (j - (ln - fade)) / fade))
            audio[i] += amp * env * math.sin(2 * math.pi * freq * j / sr)
        ann.append((t_s * 1000.0, label))

    # 主节拍序列: 约 400ms 间隔, 强弱混合, 含弱起音
    t = 0.5
    k = 0
    while t < dur_s - 5.5:
        r = rnd.random()
        if r < 0.25:
            amp, lab = 0.06, "weak"
        elif r < 0.6:
            amp, lab = 0.22, "medium"
        else:
            amp, lab = 0.6, "strong"
        add_tone(t, amp, rnd.choice([220, 330, 440, 660, 880]), label=lab)
        t += rnd.choice([0.4, 0.4, 0.4, 0.8, 0.6])
        k += 1
    # 颤音段落: 持续颤音音(6Hz, +-3% 频偏) + 内部 3 个真实起音
    v0 = 12.0
    vlen = 4.0
    i0 = int(v0 * sr)
    ph = 0.0
    vlen_n = int(vlen * sr)
    vfade = int(0.05 * sr)
    for j in range(vlen_n):
        i = i0 + j
        if i >= n:
            break
        f = 520.0 * (1.0 + 0.03 * math.sin(2 * math.pi * 6.0 * j / sr))
        ph += 2 * math.pi * f / sr
        g = 1.0
        if j < vfade:
            g = 0.5 * (1.0 - math.cos(math.pi * j / vfade))
        if j > vlen_n - vfade:
            g = 0.5 * (1.0 + math.cos(math.pi * (j - (vlen_n - vfade)) / vfade))
        audio[i] += 0.3 * g * math.sin(ph)
    for dt, amp, lab in [(0.8, 0.5, "strong"), (2.0, 0.07, "weak"), (3.1, 0.25, "medium")]:
        add_tone(v0 + dt, amp, 990.0, label=lab)
    ann.sort()
    return audio, ann

# ---------------- 缺陷基线(参考): 固定能量阈值 ----------------

def baseline_detect(audio, sr=SR, frame=512, hop=128):
    ons = []
    for i in range(0, len(audio) - frame, hop):
        e = sum(x * x for x in audio[i:i + frame]) / frame
        if e > 0.01:
            t = i / sr * 1000.0
            if not ons or t - ons[-1] > 1:
                ons.append(t)
    return ons

# ---------------- 匹配与指标 ----------------

def match(detected, annotated, tol_ms=50.0):
    """按时间贪心匹配, 返回 (匹配对, 未匹配检测下标, 未匹配标注下标)。"""
    det = sorted(detected)
    ann = sorted(range(len(annotated)), key=lambda i: annotated[i][0])
    pairs, used_d, used_a = [], set(), set()
    di = 0
    for ai in ann:
        ta = annotated[ai][0]
        best, bd = None, tol_ms + 1
        for j, td in enumerate(det):
            if j in used_d:
                continue
            d = abs(td - ta)
            if d <= tol_ms and d < bd:
                best, bd = j, d
        if best is not None:
            pairs.append((det[best], ta))
            used_d.add(best)
            used_a.add(ai)
    missed_a = [i for i in range(len(annotated)) if i not in used_a]
    return pairs, missed_a

def match_rate(ons1, ons2, tol_ms):
    a, b = sorted(ons1), sorted(ons2)
    used = set()
    m = 0
    for t in a:
        best, bd = None, tol_ms + 1
        for j, u in enumerate(b):
            if j in used:
                continue
            d = abs(u - t)
            if d <= tol_ms and d < bd:
                best, bd = j, d
        if best is not None:
            used.add(best)
            m += 1
    return 2.0 * m / (len(a) + len(b)) if (a or b) else 1.0

# ---------------- 验收流程 ----------------

def main():
    audio, ann = synth_dataset()
    weak_idx = [i for i, (_, lab) in enumerate(ann) if lab == "weak"]
    results = {}

    # --- 标准 1: 精度/召回/时间偏差/弱起音漏检 ---
    r1 = onset.detect(audio, sr=SR, frame=512, hop=128)
    pairs, missed = match(r1["onsets_ms"], ann, tol_ms=50.0)
    prec = len(pairs) / len(r1["onsets_ms"]) if r1["onsets_ms"] else 0.0
    rec = len(pairs) / len(ann)
    dev = statistics.median([abs(d - a) for d, a in pairs]) if pairs else 1e9
    weak_missed_ours = sum(1 for i in missed if i in weak_idx)
    base = baseline_detect(audio)
    _, missed_b = match(base, ann, tol_ms=50.0)
    weak_missed_base = sum(1 for i in missed_b if i in weak_idx)
    ok1 = (prec >= 0.85 and rec >= 0.80 and dev <= 20.0
           and weak_missed_ours <= 1.2 * weak_missed_base)
    results["1_precision_recall_deviation_weak"] = {
        "precision": round(prec, 4), "recall": round(rec, 4),
        "median_deviation_ms": round(dev, 2),
        "weak_missed_ours": weak_missed_ours,
        "weak_missed_baseline": weak_missed_base,
        "n_onsets": len(r1["onsets_ms"]), "n_annotated": len(ann),
        "bpm": r1["bpm"], "grid_coverage": (r1["grid"] or {}).get("coverage"),
        "pass": ok1,
    }

    # --- 标准 2: 两组帧长帧移复跑一致 + 乱序分段一致 ---
    r2 = onset.detect(audio, sr=SR, frame=1024, hop=256)
    mr = match_rate(r1["onsets_ms"], r2["onsets_ms"], tol_ms=40.0)
    # 乱序分段: 帧起点区间按 hop 对齐切 8 段, 洗牌后处理
    hop = 128
    last = len(audio) - 512
    bounds = [0]
    step = ((last // 8) // hop) * hop
    for i in range(1, 8):
        bounds.append(i * step)
    bounds.append(last)
    segs = [(bounds[i], bounds[i + 1]) for i in range(8)]
    rnd = random.Random(99)
    rnd.shuffle(segs)
    r3 = onset.detect(audio, sr=SR, frame=512, hop=128, segments=segs)
    same_digest = (r3["digest"] == r1["digest"])
    ok2 = (mr >= 0.95 and same_digest)
    results["2_framehop_consistency_shuffle"] = {
        "match_rate_512_128_vs_1024_256": round(mr, 4),
        "shuffled_segments_digest_equal": same_digest,
        "pass": ok2,
    }

    # --- CLI 契约冒烟测试 ---
    inp = os.path.join(TMP, "onset_in.json")
    outp = os.path.join(TMP, "onset_out.json")
    with open(inp, "w") as f:
        json.dump({"sr": SR, "frame": 512, "hop": 128, "audio": audio}, f)
    subprocess.run([sys.executable, "onset.py", inp, outp], check=True,
                   cwd=os.path.dirname(os.path.abspath(__file__)))
    cli = json.load(open(outp))
    results["3_cli_contract"] = {
        "digest_equal": cli["digest"] == r1["digest"],
        "keys": sorted(cli.keys()),
        "pass": cli["digest"] == r1["digest"],
    }

    allpass = all(v["pass"] for v in results.values())
    results["ALL_PASS"] = allpass
    print(json.dumps(results, indent=2, ensure_ascii=False))
    sys.exit(0 if allpass else 1)

if __name__ == "__main__":
    main()
