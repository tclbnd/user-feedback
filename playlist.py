import shutil
from pathlib import Path


def publish(stage: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for source in [stage / "init.mp4", *sorted(stage.glob("segment-*.m4s"))]:
        shutil.copyfile(source, output / source.name)
    shutil.copyfile(stage / "index.m3u8", output / "index.m3u8")
