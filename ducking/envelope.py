"""Duck-depth target and attack/hold/release envelope (dB domain, frame rate)."""


def target_duck_frames(conf, voice_lufs, music_lufs,
                       min_depth=8.0, max_depth=18.0, depth_floor=0.2):
    """Per-frame target attenuation (positive dB = cut).

    Depth follows BOTH voice confidence and the short-term loudness balance:
    louder voice vs. music -> deeper duck; weak/uncertain voice -> little duck.
    A min_depth floor guarantees >=8 dB of ducking whenever speech is confident.
    """
    out = []
    for c, vl, ml in zip(conf, voice_lufs, music_lufs):
        if c <= 0.0:
            out.append(0.0)
            continue
        gap = vl - ml                       # >0 when voice sits above music
        gap_term = 9.0 + 0.75 * max(0.0, gap)
        depth = max(min_depth, min(max_depth, gap_term))
        t = c * depth
        out.append(0.0 if t < depth_floor else t)
    return out


def envelope_frames(target, hop_s,
                    attack_s=0.020, hold_s=0.150, release_s=0.300,
                    rate_db_s=60.0):
    """Attack / hold / release follower on a positive-dB attenuation target.

    * attack  : constant dB/s slew while target exceeds current level
    * hold    : after target collapses, level is frozen for hold_s, bridging
                inter-word gaps so there is no re-attack pumping between words
    * release : constant dB/s slew back to zero after the hold elapses

    Timing advances purely on the fixed frame grid, hence output is independent
    of any sample-level processing chunk size or chunk order.
    """
    n = len(target)
    dt = hop_s
    step = rate_db_s * dt                  # dB per frame
    hold_frames = max(1, int(round(hold_s / dt)))

    level = 0.0
    hold_left = 0
    env = [0.0] * n
    for k in range(n):
        t = target[k]
        if t >= level:
            level = min(t, level + step)   # constant-rate attack
            hold_left = hold_frames
        else:
            if hold_left > 0:
                hold_left -= 1             # hold: freeze through word gaps
            else:
                level = max(t, level - step)   # constant-rate release
                if level < 0.02:
                    level = 0.0
        env[k] = level
    return env


def render_sample_gain_db(env_db, n_samples, hop, win):
    """Expand frame-level attenuation to per-sample gain dB with linear
    interpolation in the dB domain. A pure function of the frame envelope, so
    any chunking of the render pass yields identical samples."""
    half = win // 2
    nf = len(env_db)
    last = nf - 1
    gains = [0.0] * n_samples
    for i in range(n_samples):
        pos = (i + half) / hop             # fractional frame coordinate
        k = int(pos)
        frac = pos - k
        if k >= last:
            g = env_db[last]
        elif k < 0:
            g = env_db[0]
        else:
            g = env_db[k] * (1.0 - frac) + env_db[k + 1] * frac
        gains[i] = g
    return gains
