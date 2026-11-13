"""验收测试：BS.1770 响度归一化。

覆盖：
  1. 长静音 / 短节目 / 5.1 向量上，与独立参考计量器 ref_meter 交叉比对
     （积分响度 <=0.2 LU，真峰值 <=0.3 dB）；
  2. 含采样间峰值的节目输出 <= -1 dBTP；可行节目达到 -23 LUFS +/-0.3 LU；
  3. 限峰受约束与全静音节目的诊断、无 NaN；短节目与未知布局的明确结果/错误；
  4. --block 读取块长度不改变任何测量结果。
"""
import json
import math
import os
import subprocess
import sys
import wave
from array import array

import make_demo
import normalize
import ref_meter

WORK = "scratch/acceptance"
LU_TOL = 0.2
TP_TOL = 0.3

VECTORS = [
    # 名称, make_demo 参数, 布局
    ("long_silence", dict(case="normal", seconds=3.0, channels=2), "stereo"),
    ("surround51",   dict(case="surround", seconds=3.0, channels=6), "5.1"),
    ("intersample",  dict(case="intersample", seconds=2.0, channels=2), "stereo"),
    ("limited",      dict(case="limited", seconds=3.0, channels=2), "stereo"),
    ("silence",      dict(case="silence", seconds=2.0, channels=2), "stereo"),
    ("short",        dict(case="short", seconds=0.3, channels=2), "stereo"),
    ("mono",         dict(case="normal", seconds=2.0, channels=1), "mono"),
]

failures = []


def check(cond, msg):
    status = "PASS" if cond else "FAIL"
    print("  [%s] %s" % (status, msg))
    if not cond:
        failures.append(msg)


def run_cli(src, dst, report, layout, block=4096):
    p = subprocess.run(
        [sys.executable, "normalize.py", src, dst, report,
         "--layout", layout, "--block", str(block)],
        capture_output=True, text=True)
    return p


def finite_pcm(path):
    with wave.open(path, "rb") as w:
        frames = array("h")
        frames.frombytes(w.readframes(w.getnframes()))
    # int16 天然有限；额外核对无 NaN 语义的非法值不存在
    return all(-32768 <= v <= 32767 for v in frames)


def main():
    os.makedirs(WORK, exist_ok=True)
    print("== 参考计量器自检 ==")
    if not ref_meter.selftest():
        failures.append("ref_meter 锚点自检失败")
        return finish()

    reports = {}
    for name, kw, layout in VECTORS:
        print("\n== 向量 %s (%s) ==" % (name, layout))
        src = os.path.join(WORK, name + ".wav")
        dst = os.path.join(WORK, name + ".out.wav")
        rep = os.path.join(WORK, name + ".json")
        make_demo.make(src, **kw)

        p = run_cli(src, dst, rep, layout)
        check(p.returncode == 0, "CLI 退出码 0（stderr=%r）" % p.stderr.strip())
        if p.returncode != 0:
            continue
        r = json.load(open(rep))
        reports[name] = r
        ref_in = ref_meter.measure(src, layout)
        ref_out = ref_meter.measure(dst, layout)

        # ---- 交叉比对输入计量（短/静音向量 LUFS 可为 None，单独处理）----
        if r["input"]["lufs"] is not None and ref_in["integrated_lufs"] is not None:
            d_lu = abs(r["input"]["lufs"] - ref_in["integrated_lufs"])
            check(d_lu <= LU_TOL,
                  "输入积分响度差 %.3f LU <= %.1f" % (d_lu, LU_TOL))
        if r["input"]["dbtp"] is not None and ref_in["dbtp"] is not None:
            d_tp_in = abs(r["input"]["dbtp"] - ref_in["dbtp"])
            check(d_tp_in <= TP_TOL,
                  "输入真峰值差 %.3f dB <= %.1f" % (d_tp_in, TP_TOL))
        else:
            check(r["input"]["dbtp"] is None and ref_in["dbtp"] is None,
                  "输入真峰值两侧均为 null")

        # ---- 输出交叉比对 ----
        if r["output"]["lufs"] is not None:
            d_lu = abs(r["output"]["lufs"] - ref_out["integrated_lufs"])
            check(d_lu <= LU_TOL,
                  "输出积分响度差 %.3f LU <= %.1f" % (d_lu, LU_TOL))
        if r["output"]["dbtp"] is not None:
            d_tp = abs(r["output"]["dbtp"] - ref_out["dbtp"])
            check(d_tp <= TP_TOL,
                  "输出真峰值差 %.3f dB <= %.1f" % (d_tp, TP_TOL))
            check(r["output"]["dbtp"] <= -1.0 + 1e-9,
                  "输出真峰值 %.3f dBTP <= -1 dBTP" % r["output"]["dbtp"])

        check(finite_pcm(dst), "输出 PCM 全部为有限合法 int16")
        check(math.isfinite(r["gain_db"]), "gain_db 有限")
        check(r["layout"] == layout and r["diagnostics"]["channels"]
              == normalize.LAYOUTS[layout]["channels"], "报告声道布局正确")
        check(r["diagnostics"]["input_file"].endswith(name + ".wav")
              and r["diagnostics"]["output_file"].endswith(name + ".out.wav"),
              "报告文件路径可对照到音频文件")

    print("\n== 验收语义核对 ==")
    # 可行节目（长静音但整体足够响）达到目标
    r = reports["long_silence"]
    check(r["reason"] == "target", "可行节目 reason=target，实际=%s" % r["reason"])
    check(abs(r["output"]["lufs"] - (-23.0)) <= 0.3,
          "可行节目输出 %.3f LUFS 落在 -23 +/-0.3" % r["output"]["lufs"])
    # 相对门确实剔除了静音块（27 块中部分被门控）
    g = r["diagnostics"]["gating"]
    check(g["blocks_gated"] < g["blocks_total"],
          "长静音向量相对门剔除块（%d/%d 保留）"
          % (g["blocks_gated"], g["blocks_total"]))

    # 采样间峰值节目：输出必须被压到 -1 dBTP 内
    r = reports["intersample"]
    check(r["output"]["dbtp"] <= -1.0 + 1e-9,
          "采样间峰值节目输出 %.3f dBTP <= -1" % r["output"]["dbtp"])

    # 限峰受约束：诊断说明原因，输出响度允许偏离目标
    r = reports["limited"]
    check(r["reason"] == "peak_limited", "限峰节目 reason=peak_limited，实际=%s"
          % r["reason"])
    check(r["output"]["dbtp"] <= -1.0 + 1e-9,
          "限峰节目输出 %.3f dBTP <= -1" % r["output"]["dbtp"])
    check(len(r["diagnostics"]["warnings"]) >= 1, "限峰节目给出限制原因诊断")
    print("    诊断：%s" % r["diagnostics"]["warnings"][0])

    # 全静音
    r = reports["silence"]
    check(r["reason"] == "silence", "全静音 reason=silence，实际=%s" % r["reason"])
    check(r["input"]["lufs"] is None and r["output"]["lufs"] is None
          and r["input"]["dbtp"] is None and r["output"]["dbtp"] is None,
          "全静音 LUFS/TP 均为 null")
    check(abs(r["gain_db"]) < 1e-12, "全静音单位增益")
    check(len(r["diagnostics"]["warnings"]) == 1, "全静音给出明确诊断")

    # 短节目
    r = reports["short"]
    check(r["reason"] == "insufficient_duration",
          "短节目 reason=insufficient_duration，实际=%s" % r["reason"])
    check(r["input"]["lufs"] is None and r["output"]["lufs"] is None,
          "短节目 LUFS 为 null")
    check(abs(r["gain_db"]) < 1e-12, "短节目单位增益")

    # 5.1：LFE 不参与响度（改喂 LFE-only 能量验证权重为 0）
    print("\n== 5.1 LFE 排除规则 ==")
    lfe_src = os.path.join(WORK, "lfe_only.wav")
    make_demo.make(lfe_src, case="surround", seconds=1.0, channels=6)
    # 手工把非 LFE 声道清零，仅保留 LFE
    with wave.open(lfe_src, "rb") as w:
        fr = array("h"); fr.frombytes(w.readframes(w.getnframes()))
        params = w.getparams()
    for i in range(len(fr) // 6):
        for c in (0, 1, 2, 4, 5):
            fr[i * 6 + c] = 0
    lfe_only = os.path.join(WORK, "lfe_clean.wav")
    with wave.open(lfe_only, "wb") as w:
        w.setparams(params); w.writeframes(fr.tobytes())
    lm = normalize.BlockLoudnessMeter(6, normalize.LAYOUTS["5.1"]["weights"], 48000)
    tpm = normalize.TruePeakMeter(6)
    with wave.open(lfe_only, "rb") as w:
        raw = array("h"); raw.frombytes(w.readframes(w.getnframes()))
    chs = [array("d", (x / 32768.0 for x in raw[c::6])) for c in range(6)]
    lm.process(chs); tpm.process(chs)
    lufs, _ = lm.integrated()
    check(lufs is None, "仅 LFE 有信号时积分响度为 null（LFE 权重 0）")
    check(tpm.dbtp() is not None and tpm.dbtp() > -7.0,
          "LFE 仍计入真峰值（%.2f dBTP）" % tpm.dbtp())

    print("\n== --block 不变性 ==")
    src = os.path.join(WORK, "long_silence.wav")
    baselines = {}
    for block in (1, 97, 4096, 50000):
        dst = os.path.join(WORK, "block_%d.wav" % block)
        rep = os.path.join(WORK, "block_%d.json" % block)
        p = run_cli(src, dst, rep, "stereo", block=block)
        check(p.returncode == 0, "block=%d 运行成功" % block)
        r = json.load(open(rep))
        sig = (r["input"]["lufs"], r["input"]["dbtp"],
               r["output"]["lufs"], r["output"]["dbtp"], r["gain_db"])
        baselines.setdefault("sig", sig)
        check(all((a is None and b is None)
                  or (a is not None and b is not None and abs(a - b) < 1e-12)
                  for a, b in zip(baselines["sig"], sig)),
              "block=%d 测量与基准逐位一致" % block)

    print("\n== 错误处理 ==")
    bad = os.path.join(WORK, "bad_layout.json")
    p = run_cli(src, os.path.join(WORK, "bad.wav"), bad, "7.1")
    check(p.returncode != 0 and "未知声道布局" in p.stderr,
          "未知布局被拒绝并给出明确错误")
    mono_src = os.path.join(WORK, "mono.wav")
    p = run_cli(mono_src, os.path.join(WORK, "mismatch.wav"),
                os.path.join(WORK, "mismatch.json"), "stereo")
    check(p.returncode != 0 and "声道" in p.stderr,
          "布局/文件声道数不符被拒绝")
    return finish()


def finish():
    print("\n" + "=" * 48)
    if failures:
        print("验收失败 %d 项：" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("全部验收项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
