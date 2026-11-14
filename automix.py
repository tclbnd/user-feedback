#!/usr/bin/env python3
"""Auto-ducking program mixer.

Usage: python3 automix.py req.json output.json

req.json fields:
  tracks : [{name, pcm}]            pcm = path to 16-bit PCM WAV (mono/stereo)
  fs     : sample rate (informational; WAV rate is authoritative)
  target_lufs : integrated loudness target (default -16 LUFS)
  params : {block: N}               render block size in samples; changing it
                                    must not change the gain envelope or output.

Outputs (next to output.json):
  mix.wav            full mix
  <track>.stem.wav   per-track rendered contribution (post ducking)
  envelope.json      per-frame gain curve + loudness statistics
  output.json        summary matching the baseline schema, extended
"""
import json
import math
import os
import struct
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ducking.engine import EngineParams, mix_program


def read_wav(path):
    w = wave.open(path, 'rb')
    nch = w.getnchannels()
    sw = w.getsampwidth()
    fs = w.getframerate()
    n = w.getnframes()
    raw = w.readframes(n)
    w.close()
    if sw == 2:
        vals = struct.unpack('<%dh' % (n * nch), raw)
        denom = 32768.0
    elif sw == 1:
        vals = tuple(b - 128 for b in raw)
        denom = 128.0
    elif sw == 4:
        vals = struct.unpack('<%dl' % (n * nch), raw)
        denom = 2147483648.0
    else:
        raise ValueError('unsupported sample width %d' % sw)
    if nch == 1:
        pcm = [v / denom for v in vals]
    else:
        # downmix stereo to mono with clipping guard
        pcm = [0.0] * n
        for i in range(n):
            s = sum(vals[i * nch + c] for c in range(nch)) / nch
            pcm[i] = max(-1.0, min(1.0, s / denom))
    return pcm, fs


def write_wav(path, pcm, fs):
    n = len(pcm)
    out = bytearray()
    for x in pcm:
        v = int(round(max(-1.0, min(1.0, x)) * 32767.0))
        out += struct.pack('<h', v)
    w = wave.open(path, 'wb')
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(fs)
    w.writeframes(bytes(out))
    w.close()


def main():
    req_path = sys.argv[1]
    out_path = sys.argv[2]
    out_dir = os.path.dirname(os.path.abspath(out_path))
    req_dir = os.path.dirname(os.path.abspath(req_path))
    req = json.load(open(req_path))

    tracks = []
    fs = None
    for t in req['tracks']:
        pcm_path = t['pcm']
        if not os.path.isabs(pcm_path):
            pcm_path = os.path.join(req_dir, pcm_path)
        pcm, tfs = read_wav(pcm_path)
        fs = fs or tfs
        tracks.append(dict(name=t['name'], pcm=pcm))
    fs = fs or req.get('fs', 16000)

    params = EngineParams()
    block = None
    if 'params' in req and req['params']:
        block = req['params'].get('block')

    result = mix_program(
        tracks, fs,
        target_lufs=float(req.get('target_lufs', -16.0)),
        params=params, block=block,
    )

    mix_path = os.path.join(out_dir, 'mix.wav')
    write_wav(mix_path, result['mix'], fs)

    # per-track rendered contributions (stems) for inspection
    stem_paths = {}
    voice_i = result['voice_index']
    for i, name in enumerate(result['names']):
        stem = (result['voice_contrib'] if i == voice_i
                else result['music_contrib'])
        # when several music tracks exist, shared bed is exported once per name
        p = os.path.join(out_dir, name + '.stem.wav')
        write_wav(p, stem, fs)
        stem_paths[name] = os.path.basename(p)

    envelope_path = os.path.join(out_dir, 'envelope.json')
    with open(envelope_path, 'w') as f:
        json.dump(dict(
            frame=result['frame'],
            stats=result['stats'],
            sample_music_gain_db=result['sample_music_gain_db'],
            stems=stem_paths,
        ), f, indent=1)

    summary = dict(
        out='mix.wav',
        gains=result['static_gain_db'],
        peak_db=result['stats']['mix_true_peak_dbtp'],
        lufs=result['stats']['mix_integrated_lufs'],
        target_lufs=result['stats']['target_lufs'],
        loudness_error_lufs=result['stats']['loudness_error_lufs'],
        true_peak_dbtp=result['stats']['mix_true_peak_dbtp'],
        envelope='envelope.json',
        stems=stem_paths,
        stats=result['stats'],
    )
    with open(out_path, 'w') as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary['stats'], indent=1))


if __name__ == '__main__':
    main()
