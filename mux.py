import os
import shutil
import subprocess
from pathlib import Path


def ffmpeg_executable() -> str:
    return os.environ.get("FFMPEG", "ffmpeg")


def make_playlist(session: dict, stage: Path) -> None:
    for name, track in session["tracks"].items():
        with (stage / f"{name}.mp4").open("wb") as output:
            for path in [track["init"], *track["fragments"]]:
                with path.open("rb") as source:
                    shutil.copyfileobj(source, output)
    subprocess.run(
        [
            ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", "video.mp4", "-i", "audio.mp4", "-map", "0:v:0",
            "-map", "1:a:0", "-c", "copy", "-f", "hls", "-hls_time", "2",
            "-hls_segment_type", "fmp4", "-hls_playlist_type", "event",
            "-hls_flags", "independent_segments", "-hls_fmp4_init_filename", "init.mp4",
            "-hls_segment_filename", "segment-%03d.m4s", "index.m3u8",
        ],
        cwd=stage,
        check=True,
    )
