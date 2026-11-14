"""Auto-ducking mixing engine (pure stdlib, deterministic, chunk-independent)."""
import math

from .filters import true_peak_db
from .loudness import (
    integrated_lufs,
    kweighted_ms_frames,
    ms_to_lufs,
    voice_confidence_frames,
)
from .envelope import envelope_frames, render_sample_gain_db, target_duck_frames


class EngineParams:
    def __init__(self, win_s=0.1, hop_s=0.05,
                 attack_s=0.02, hold_s=0.15, release_s=0.30,
                 min_depth=8.0, max_depth=18.0, rate_db_s=60.0,
                 tp_limit_db=-1.0):
        self.win_s = win_s
        self.hop_s = hop_s
        self.attack_s = attack_s
        self.hold_s = hold_s
        self.release_s = release_s
        self.min_depth = min_depth
        self.max_depth = max_depth
        self.rate_db_s = rate_db_s
        self.tp_limit_db = tp_limit_db


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def pumping_metrics(env_db, hop_s, active_mask):
    """Objective pump diagnostics on the music attenuation envelope.

    gap_reattack_events : attenuation re-ramps inside a speech gap after it had
                          collapsed (ideal 0; the hold timer suppresses these).
    max_gap_rate_db_s   : fastest |d(attenuation)/dt| observed over gap frames,
                          bounded by the constant release slew rate.
    mean_gap_abs_rate_db_s : mean absolute envelope velocity over gap frames.
    """
    n = len(env_db)
    reattack = 0
    max_rate = 0.0
    vel_sum = 0.0
    vel_n = 0
    # A true pump is an upward ramp that can only occur after the envelope has
    # fully recovered (<1 dB) while speech is inactive. Short inter-word gaps
    # never reach that state because the hold timer freezes the envelope, so
    # normal dialogue re-opening after a *long* pause is not counted either.
    recovered_in_gap = False
    for k in range(1, n):
        d = env_db[k] - env_db[k - 1]
        rate = d / hop_s
        inactive = not active_mask[k]
        if inactive:
            ar = abs(rate)
            vel_sum += ar
            vel_n += 1
            if ar > max_rate:
                max_rate = ar
            if env_db[k] < 1.0:
                recovered_in_gap = True
        else:
            recovered_in_gap = False
        if d > 1e-9 and inactive and recovered_in_gap and env_db[k - 1] < 1.5:
            reattack += 1
    return dict(
        gap_reattack_events=reattack,
        max_gap_rate_db_s=round(max_rate, 3),
        mean_gap_abs_rate_db_s=round(vel_sum / vel_n, 3) if vel_n else 0.0,
        release_rate_limit_db_s=60.0,
    )


def mix_program(tracks, fs, target_lufs=-16.0, voice_name='vox',
                params=None, block=None):
    """Run the full ducking mix.

    tracks : list of dicts with keys name (str) and pcm (list of float in [-1,1])
    block  : render block size in samples (README params.block). Affects only
             chunking of the render loop, never analysis or the gain curve.
    """
    params = params or EngineParams()
    names = [t['name'] for t in tracks]
    voice_i = next((i for i, nm in enumerate(names)
                    if voice_name in nm.lower()), 0)
    music_i = [i for i in range(len(tracks)) if i != voice_i]
    n = max(len(t['pcm']) for t in tracks)
    for t in tracks:
        if len(t['pcm']) < n:
            t['pcm'] = t['pcm'] + [0.0] * (n - len(t['pcm']))

    # 1. short-term K-weighted loudness on an absolute frame grid
    hop = int(round(params.hop_s * fs))
    win = int(round(params.win_s * fs))
    n_frames = max(1, int(math.ceil(max(n - 1, 0) / hop)) + 1)
    frame_ms = [
        kweighted_ms_frames(t['pcm'], fs, params.win_s, params.hop_s)[0]
        for t in tracks
    ]
    frame_lufs = [[ms_to_lufs(m) for m in ms_list] for ms_list in frame_ms]

    # 2. voice activity confidence
    conf, _conf_raw = voice_confidence_frames(
        tracks[voice_i]['pcm'], fs, hop, win, n_frames
    )
    active = [c >= 0.30 for c in conf]

    # 3. target duck from confidence + loudness gap, then A/H/R envelope
    if music_i:
        music_ms = [
            sum(frame_ms[i][k] for i in music_i) for k in range(n_frames)
        ]
        music_lufs = [ms_to_lufs(m) for m in music_ms]
    else:
        music_lufs = [-120.0] * n_frames
    target = target_duck_frames(
        conf, frame_lufs[voice_i], music_lufs,
        min_depth=params.min_depth, max_depth=params.max_depth,
    )
    env = envelope_frames(
        target, params.hop_s, params.attack_s, params.hold_s,
        params.release_s, params.rate_db_s,
    )

    # 4. static gain from target loudness, capped by true-peak limit.
    # The ducking envelope lowers the music for part of the program, so the
    # trim is solved against the *ducked* mix in two fixed-point passes rather
    # than against the raw sum; one correction makes it converge.
    raw_mix = [
        sum(tracks[i]['pcm'][j] for i in range(len(tracks)))
        for j in range(n)
    ]
    raw_lufs = integrated_lufs(raw_mix, fs)
    bus_trim = min(target_lufs - raw_lufs, 12.0) if math.isfinite(raw_lufs) \
        else 0.0

    music_gain_db = render_sample_gain_db(env, n, hop, win)
    block = block or n
    boundaries = list(range(0, n, block)) + [n]
    duck_lin = [10.0 ** (-g / 20.0) for g in music_gain_db]

    def render_with_trim(trim_db):
        lin_static = []
        gains_db = {}
        for i, t in enumerate(tracks):
            tp = true_peak_db(t['pcm'])
            tp_cap = params.tp_limit_db - (tp if math.isfinite(tp) else -120.0)
            g = min(trim_db, tp_cap)
            gains_db[t['name']] = g
            lin_static.append(10.0 ** (g / 20.0))
        out = [0.0] * n
        voice_contrib = [0.0] * n
        music_contrib = [0.0] * n
        # block slicing changes loop boundaries only; per-sample gain is a pure
        # function of the envelope, so samples are chunk-order independent
        for b in range(len(boundaries) - 1):
            lo, hi = boundaries[b], boundaries[b + 1]
            for j in range(lo, hi):
                v = 0.0
                m = 0.0
                for i in range(len(tracks)):
                    x = tracks[i]['pcm'][j] * lin_static[i]
                    if i == voice_i:
                        v += x
                    else:
                        x *= duck_lin[j]
                        m += x
                voice_contrib[j] = v
                music_contrib[j] = m
                out[j] = v + m
        return out, voice_contrib, music_contrib, gains_db

    out, voice_contrib, music_contrib, static_db = render_with_trim(bus_trim)
    ducked_lufs = integrated_lufs(out, fs)
    if math.isfinite(ducked_lufs):
        bus_trim = min(bus_trim + target_lufs - ducked_lufs, 15.0)
        out, voice_contrib, music_contrib, static_db = \
            render_with_trim(bus_trim)

    # bus true-peak guard: static trim only (no time-varying limiter)
    mix_tp = true_peak_db(out)
    bus_guard = 0.0
    if math.isfinite(mix_tp) and mix_tp > params.tp_limit_db:
        bus_guard = params.tp_limit_db - mix_tp
        scale = 10.0 ** (bus_guard / 20.0)
        out = [x * scale for x in out]
        voice_contrib = [x * scale for x in voice_contrib]
        music_contrib = [x * scale for x in music_contrib]
        for key in static_db:
            static_db[key] += bus_guard

    mix_lufs = integrated_lufs(out, fs)
    final_tp = true_peak_db(out)

    frame_voice = [
        10.0 * math.log10(max(frame_ms[voice_i][k], 1e-12))
        for k in range(n_frames)
    ]
    frame_music = [
        10.0 * math.log10(max(
            sum(frame_ms[i][k] for i in music_i), 1e-12))
        for k in range(n_frames)
    ]

    speech_env = [env[k] for k in range(n_frames) if active[k]]
    gap_env = [env[k] for k in range(n_frames) if not active[k]]
    n_active = sum(1 for a in active if a)
    pump = pumping_metrics(env, params.hop_s, active)

    stats = dict(
        n_frames=n_frames,
        frame_hop_s=params.hop_s,
        voice_active_frames=n_active,
        duck_db_speech_mean=round(
            sum(speech_env) / len(speech_env), 3) if speech_env else 0.0,
        duck_db_speech_p95=_percentile(sorted(speech_env), 0.95),
        duck_db_speech_min=round(min(speech_env), 3) if speech_env else 0.0,
        duck_db_speech_max=round(max(speech_env), 3) if speech_env else 0.0,
        duck_db_gap_mean=round(
            sum(gap_env) / len(gap_env), 3) if gap_env else 0.0,
        confidence_mean_active=round(
            sum(c for c, a in zip(conf, active) if a) / max(1, n_active), 4),
        raw_integrated_lufs=round(raw_lufs, 3),
        mix_integrated_lufs=round(mix_lufs, 3),
        target_lufs=target_lufs,
        loudness_error_lufs=round(mix_lufs - target_lufs, 3),
        mix_true_peak_dbtp=round(final_tp, 3),
        true_peak_limit_dbtp=params.tp_limit_db,
        bus_trim_db=round(bus_trim + bus_guard, 3),
        pumping=pump,
    )
    frame = dict(
        t_s=[round(k * params.hop_s, 4) for k in range(n_frames)],
        voice_confidence=[round(c, 5) for c in conf],
        voice_lufs=[round(v, 3) for v in frame_lufs[voice_i]],
        music_lufs=[round(v, 3) for v in music_lufs],
        target_duck_db=[round(v, 4) for v in target],
        music_duck_db=[round(v, 4) for v in env],
        voice_power_db=[round(v, 3) for v in frame_voice],
        music_power_db=[round(v, 3) for v in frame_music],
    )
    return dict(
        mix=out,
        voice_contrib=voice_contrib,
        music_contrib=music_contrib,
        frame=frame,
        static_gain_db=static_db,
        stats=stats,
        sample_music_gain_db=music_gain_db,
        names=names,
        voice_index=voice_i,
    )
