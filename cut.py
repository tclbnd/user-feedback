"""Legacy single-clip copy path."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser()
    p.add_argument("edl",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("report",type=Path)
    a=p.parse_args()
    edit=json.loads(a.edl.read_text())
    first=edit["clips"][0]
    media=a.edl.parent/edit["sources"][first["source"]]
    ff=os.environ.get("ZQ_FFMPEG","ffmpeg")
    subprocess.run([ff,"-hide_banner","-loglevel","error","-nostdin","-y","-ss",
                    str(first["start_ms"]/1000),"-i",str(media),"-t",
                    str((first["end_ms"]-first["start_ms"])/1000),"-c","copy",str(a.output)],check=True)
    a.report.write_text(json.dumps({"frames":[],"audio":[]})+"\n")


if __name__=="__main__":
    main()
