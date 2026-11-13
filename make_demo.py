import json
from pathlib import Path
import struct

data=b"".join(struct.pack("<hh",i*17-800,900-i*13) for i in range(81))
Path("demo.pcm").write_bytes(data)
Path("ranges.json").write_text(json.dumps({"ranges":[{"id":"all","start":0,"end":81}]})+"\n")
