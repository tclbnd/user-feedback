"""One-command local acceptance for the event recorder.

Run it with a local FFmpeg (fixtures are encoded with libx264/AAC and the
published playlist is actually decoded):

    python3 verify.py [--work DIR] [--keep]

The suite builds synthetic captures for the normal path, out-of-order /
duplicate delivery, truncated-then-complete delivery, a permanent hole, a
timestamp reset and an initialisation change.  It then publishes each capture,
checks the playlist/publiation invariants, verifies every retained compressed
packet against the capture (payload digest, DTS/PTS relationship and A/V
offset), decodes the two-track output with FFmpeg, and kills the recorder at
both crash points before replaying it and comparing against the uninterrupted
baseline.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Optional

WORK = Path(__file__).resolve().parent
sys.path.insert(0, str(WORK))
sys.path.insert(0, str(WORK / "tests"))

import fixtures
from ingest import read_capture
from mp4 import parse_fragment, parse_init, parse_init_tracks
from mux import plan_session

VIDEO = 1
AUDIO = 2


class AcceptanceError(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise AcceptanceError(message)


def resolve_ffmpeg() -> str:
    candidate = os.environ.get("FFMPEG", "ffmpeg")
    try:
        completed = subprocess.run(
            [candidate, "-version"], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )
    except OSError as error:
        raise AcceptanceError(
            "FFmpeg is not executable (%r): %s\n"
            "Set FFMPEG to a local FFmpeg build with libx264 and AAC."
            % (candidate, error)
        ) from error
    if completed.returncode != 0:
        raise AcceptanceError(
            "FFmpeg (%r) failed to start: %s"
            % (candidate, completed.stderr.decode("utf-8", "replace").strip())
        )
    return candidate


def publish(capture: Path, output: Path, crash: Optional[str] = None) -> int:
    environment = dict(os.environ)
    environment.pop("RECORDER_CRASH_AT", None)
    environment.pop("HLS_CRASH_AT", None)
    if crash:
        environment["RECORDER_CRASH_AT"] = crash
    completed = subprocess.run(
        [sys.executable, str(WORK / "recorder.py"), str(capture), str(output)],
        env=environment,
    )
    return completed.returncode


def tree_hashes(directory: Path) -> Dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir()) if path.is_file()
    }


def playlist_entries(output: Path) -> List[str]:
    index = output / "index.m3u8"
    check(index.is_file(), "missing index.m3u8 in %s" % output)
    text = index.read_text(encoding="utf-8")
    check(text.endswith("\n"), "playlist is not newline terminated")
    check("#EXT-X-ENDLIST" in text, "playlist is not complete")
    check("#EXT-X-PLAYLIST-TYPE:EVENT" in text, "playlist is not an EVENT")
    media = [line for line in text.splitlines()
             if line and not line.startswith("#")]
    for name in media:
        path = output / name
        check(path.is_file(), "playlist references missing %s" % name)
        parse_fragment(path.read_bytes())
    for chunk in text.split('EXT-X-MAP:URI="')[1:]:
        init = output / chunk.split('"')[0]
        check(init.is_file(), "playlist references missing init %s" % init)
        parse_init_tracks(init.read_bytes())
    return media


def decode(ffmpeg: str, output: Path) -> None:
    index = output / "index.m3u8"
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(index),
         "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if completed.returncode != 0:
        raise AcceptanceError(
            "decoding %s failed:\n%s" % (index,
                                         completed.stderr.decode("utf-8",
                                                                 "replace"))
        )


def decode_per_map(ffmpeg: str, output: Path) -> None:
    """Decode each init section (a generation with a distinct EXT-X-MAP).

    This is deliberately independent of an HLS player's support for changing
    EXT-X-MAP mid-playlist: the playlist structure is checked separately and
    every generation is decoded on its own.
    """
    text = (output / "index.m3u8").read_text(encoding="utf-8")
    groups = []
    current_init = None
    for line in text.splitlines():
        if line.startswith("#EXT-X-MAP:URI=\""):
            current_init = line.split('"')[1]
        elif line and not line.startswith("#"):
            if not groups or groups[-1][0] != current_init:
                groups.append((current_init, []))
            groups[-1][1].append(line)
    for init_name, names in groups:
        check(init_name is not None, "media segment before EXT-X-MAP")
        combined = output / (".decode-%d.mp4" % os.getpid())
        with combined.open("wb") as stream:
            stream.write((output / init_name).read_bytes())
            for name in names:
                stream.write((output / name).read_bytes())
        try:
            completed = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error",
                 "-i", str(combined), "-map", "0:v:0", "-map", "0:a:0",
                 "-f", "null", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
        finally:
            combined.unlink()
        if completed.returncode != 0:
            raise AcceptanceError(
                "decoding generation %s failed:\n%s"
                % (init_name, completed.stderr.decode("utf-8", "replace"))
            )


# ---------------------------------------------------------------------------
# Packet level verification


def _track_samples(epoch: dict, name: str) -> dict:
    track = epoch["tracks"][name]
    info = parse_init(track["init"].read_bytes())
    defaults = {info["track_id"]: info.get("trex") or {}}
    samples = []
    for seq in sorted(track["candidates"]):
        parsed = parse_fragment(
            track["candidates"][seq]["path"].read_bytes(), defaults)
        selected = parsed["tracks"].get(info["track_id"])
        if selected is None:
            selected = next(iter(parsed["tracks"].values()))
        samples.extend(dict(sample) for sample in selected)
    return {
        "timescale": info["timescale"],
        "origin": track["clock_origin"],
        "samples": samples,
        "init": track["init"],
        "missing": list(track["missing"]),
    }


def source_model(capture: Path) -> dict:
    session = read_capture(capture)
    epochs = []
    for epoch in session["epochs"]:
        epochs.append({
            "epoch": epoch["epoch"],
            "video": _track_samples(epoch, "video"),
            "audio": _track_samples(epoch, "audio"),
        })
    return {"session": session, "epochs": epochs}


def _sample_hash(sample: dict) -> str:
    return hashlib.sha256(sample["data"]).hexdigest()


def verify_packets(capture: Path, output: Path,
                   coverage_slack: int = 4) -> dict:
    """Check published packets against the capture, per recording generation."""
    plan = plan_session(read_capture(capture))
    source = source_model(capture)
    source_by_epoch = {item["epoch"]: item for item in source["epochs"]}
    epoch_by_segment = {segment["name"]: segment["epoch"]
                        for segment in plan["segments"]}
    base_by_epoch = {item["epoch"]: item["base_seconds"]
                     for item in plan["epochs"]}

    index = (output / "index.m3u8").read_text(encoding="utf-8")
    media_names = [line for line in index.splitlines()
                   if line and not line.startswith("#")]
    check(media_names == [segment["name"] for segment in plan["segments"]],
          "published segment list differs from the deterministic plan")

    indices = {}
    for item in source["epochs"]:
        for track_name, track_id in (("video", VIDEO), ("audio", AUDIO)):
            track = item[track_name]
            keyed = {}
            for sample in track["samples"]:
                key = (_sample_hash(sample), sample["cto"],
                       sample["duration"], sample["flags"] & 0xFFFF)
                keyed.setdefault(key, []).append(sample)
            indices[(item["epoch"], track_id)] = {
                "track": track,
                "keyed": keyed,
                "matched": 0,
                "deltas": set(),
            }

    stats = {}
    for name in media_names:
        epoch_id = epoch_by_segment[name]
        segment = parse_fragment((output / name).read_bytes())
        for track_id, samples in segment["tracks"].items():
            info = indices.get((epoch_id, track_id))
            check(info is not None, "unknown track %d in %s" % (track_id, name))
            track = info["track"]
            timescale = track["timescale"]
            origin = track["origin"]
            base = base_by_epoch[epoch_id]
            expected_delta = int(base * timescale + Fraction(1, 2))
            for position, sample in enumerate(samples):
                key = (_sample_hash(sample), sample["cto"],
                       sample["duration"], sample["flags"] & 0xFFFF)
                candidates = info["keyed"].get(key)
                check(candidates, "packet %d in %s is not from the capture"
                      % (position, name))
                matches = []
                for candidate in candidates:
                    delta = sample["dts"] - (candidate["dts"] - origin)
                    if abs(delta - expected_delta) <= 2:
                        matches.append((abs(delta - expected_delta),
                                        delta))
                check(matches, "packet %d in %s has an unexpected timestamp"
                      % (position, name))
                delta = min(matches)[1]
                info["deltas"].add(delta)
                info["matched"] += 1
                check(sample["pts"] - sample["dts"] == sample["cto"],
                      "PTS/DTS offset changed in %s" % name)
                if position:
                    previous = samples[position - 1]
                    check(sample["dts"] == previous["dts"]
                          + previous["duration"],
                          "decode timestamps are not contiguous in %s" % name)

        video = segment["tracks"].get(VIDEO, [])
        audio = segment["tracks"].get(AUDIO, [])
        if video and audio:
            video_ts = indices[(epoch_id, VIDEO)]["track"]["timescale"]
            audio_ts = indices[(epoch_id, AUDIO)]["track"]["timescale"]
            offset = abs(Fraction(video[0]["pts"], video_ts)
                         - Fraction(audio[0]["pts"], audio_ts))
            check(offset <= Fraction(1024, 48000) + Fraction(3003, 90000),
                  "audio/video offset %.6f s in %s" % (float(offset), name))

    for (epoch_id, track_id), info in indices.items():
        track = info["track"]
        check(info["matched"] > 0, "no packets published for %s/%s"
              % (epoch_id, track_id))
        deltas = info["deltas"]
        check(not deltas or max(deltas) - min(deltas) <= 2,
              "timestamps of %s/%d were rebased inconsistently: %s"
              % (epoch_id, track_id, sorted(deltas)))
        if deltas:
            delta = sorted(deltas)[0]
            seconds = Fraction(delta, track["timescale"])
            check(abs(seconds - base_by_epoch[epoch_id])
                  <= Fraction(1, track["timescale"]),
                  "epoch %s base offset mismatch" % epoch_id)
        holes = bool(source_by_epoch[epoch_id]["video"]["missing"]
                     or source_by_epoch[epoch_id]["audio"]["missing"])
        allowance = coverage_slack + (400 if holes else 0)
        dropped = len(track["samples"]) - info["matched"]
        check(dropped <= allowance,
              "too many packets dropped for %s/%d: %d"
              % (epoch_id, track_id, dropped))
        stats.setdefault(epoch_id, {})[track_id] = {
            "published": info["matched"],
            "source": len(track["samples"]),
            "delta": sorted(deltas),
        }

    for item in source["epochs"]:
        stats.setdefault(item["epoch"], {})
    return stats


# ---------------------------------------------------------------------------
# Scenario checks


def scenario_baseline(ffmpeg: str, scenarios: Dict[str, Path], root: Path) -> Path:
    output = root / "published" / "normal"
    check(publish(scenarios["normal"], output) == 0, "normal publication failed")
    playlist_entries(output)
    decode(ffmpeg, output)
    verify_packets(scenarios["normal"], output, coverage_slack=4)
    print("PASS normal: playlist, packet payloads, timestamps and decode")
    return output


def scenario_duplicates(scenarios, root, baseline):
    for name in ("reorder", "truncated"):
        output = root / "published" / name
        check(publish(scenarios[name], output) == 0,
              "%s publication failed" % name)
        playlist_entries(output)
        check(tree_hashes(output) == tree_hashes(baseline),
              "%s did not reproduce the normal publication" % name)
        print("PASS %s: identical to the uninterrupted baseline" % name)


def scenario_missing(ffmpeg, scenarios, root):
    output = root / "published" / "missing"
    check(publish(scenarios["missing"], output) == 0,
          "missing publication failed")
    playlist_entries(output)
    decode(ffmpeg, output)
    verify_packets(scenarios["missing"], output, coverage_slack=0)
    source = source_model(scenarios["missing"])
    video = source["epochs"][0]["video"]
    origin = video["origin"]
    # The lost GOP's decode interval must not be published at all.
    parsed = parse_fragment(
        (scenarios["missing"].parent / "video-002.m4s").read_bytes())
    lost = next(iter(parsed["tracks"].values()))
    low = lost[0]["dts"]
    high = lost[-1]["dts"] + lost[-1]["duration"]
    indices = []
    for name in playlist_entries(output):
        segment = parse_fragment((output / name).read_bytes())
        for sample in segment["tracks"].get(VIDEO, []):
            # First generation, so the output timestamp is the source
            # timestamp shifted by the clock origin.
            indices.append(sample["dts"] + origin)
    check(not any(low <= absolute < high for absolute in indices),
          "the permanently missing GOP was partially published")
    # ``source_model`` already excludes the permanently missing fragment, so
    # every accepted video packet must be published.
    check(len(indices) == len(video["samples"]),
          "missing GOP handling retained an unexpected number of packets")
    print("PASS missing: %d/%d video packets retained, recovered at next keyframe"
          % (len(indices), len(video["samples"]) + len(lost)))


def scenario_reset(ffmpeg, scenarios, root):
    output = root / "published" / "reset"
    check(publish(scenarios["reset"], output) == 0, "reset publication failed")
    playlist_entries(output)
    decode_per_map(ffmpeg, output)
    verify_packets(scenarios["reset"], output, coverage_slack=4)
    text = (output / "index.m3u8").read_text(encoding="utf-8")
    check(text.count("#EXT-X-DISCONTINUITY") == 1,
          "timestamp reset did not mark exactly one discontinuity")
    # Generation two keeps playing forward despite its zeroed timestamps.
    plan = plan_session(read_capture(scenarios["reset"]))
    check(len(plan["epochs"]) == 2, "two generations expected")
    check(plan["epochs"][1]["base_seconds"] > 0,
          "zeroed timestamps were not rebased")
    print("PASS reset: discontinuity marked and timestamps rebased")


def scenario_init_change(ffmpeg, scenarios, root):
    output = root / "published" / "init_change"
    check(publish(scenarios["init_change"], output) == 0,
          "init change publication failed")
    playlist_entries(output)
    decode_per_map(ffmpeg, output)
    verify_packets(scenarios["init_change"], output, coverage_slack=4)
    text = (output / "index.m3u8").read_text(encoding="utf-8")
    maps = [chunk.split('"')[0]
            for chunk in text.split('EXT-X-MAP:URI="')[1:]]
    check(len(set(maps)) == 2, "initialisation change did not switch MAP")
    check(text.count("#EXT-X-DISCONTINUITY") == 1,
          "initialisation change did not mark a discontinuity")
    print("PASS init change: second EXT-X-MAP selected at the discontinuity")


def scenario_crashes(scenarios, root, baseline):
    expected = tree_hashes(baseline)
    for point in ("before_playlist", "after_playlist"):
        output = root / "published" / ("crash-" + point)
        code = publish(scenarios["normal"], output, crash=point)
        check(code == -9, "crash point %s did not kill the recorder" % point)
        index = output / "index.m3u8"
        if index.exists():
            playlist_entries(output)
        for media in output.glob("segment-*.m4s"):
            parse_fragment(media.read_bytes())
        if point == "after_playlist":
            check(index.exists(), "playlist missing after %s" % point)
            check(not (output / "progress.json").exists(),
                  "progress was written before the playlist crash point")
        else:
            check(not index.exists(),
                  "playlist was published before the crash point")
        check(publish(scenarios["normal"], output) == 0,
              "replay after %s failed" % point)
        check(tree_hashes(output) == expected,
              "replay after %s differs from the uninterrupted baseline" % point)
        print("PASS crash %s: state readable, replay byte-identical" % point)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path,
                        help="directory for generated fixtures and output")
    parser.add_argument("--keep", action="store_true",
                        help="keep the work directory")
    arguments = parser.parse_args()

    temporary = None
    if arguments.work is None:
        temporary = tempfile.TemporaryDirectory(prefix="hls-acceptance-")
        root = Path(temporary.name)
    else:
        root = arguments.work
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)

    try:
        ffmpeg = resolve_ffmpeg()
    except AcceptanceError as error:
        print("ERROR: %s" % error)
        if temporary is not None:
            temporary.cleanup()
        return 2

    try:
        scenarios = fixtures.build_scenarios(root / "captures")
        baseline = scenario_baseline(ffmpeg, scenarios, root)
        scenario_duplicates(scenarios, root, baseline)
        scenario_missing(ffmpeg, scenarios, root)
        scenario_reset(ffmpeg, scenarios, root)
        scenario_init_change(ffmpeg, scenarios, root)
        scenario_crashes(scenarios, root, baseline)
    except AcceptanceError as error:
        print("FAIL: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - report the failing stage
        import traceback
        traceback.print_exc()
        print("FAIL: unexpected error: %s" % error)
        return 1
    finally:
        if temporary is not None and not arguments.keep:
            temporary.cleanup()

    print("ACCEPTANCE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
