import json, math, random, os, subprocess, sys
import wm

os.makedirs("out", exist_ok=True)
SR = 16000
x = [0.2*math.sin(2*math.pi*330*i/SR)+0.1*math.sin(2*math.pi*990*i/SR)
     + 0.01*random.Random(i).gauss(0, 1) for i in range(SR*11)]
wm.write_wav("out/in.wav", x, SR)
bits = [random.Random(3).getrandbits(1) for _ in range(40)]
json.dump({"pcm": "in.wav", "bits": bits, "seed": 7, "frame": 1024},
          open("out/req_emb.json", "w"))
subprocess.run([sys.executable, "wm.py", "out/req_emb.json", "out/emb.json"],
               check=True)
r = json.load(open("out/emb.json"))
print("embed log frames:", len(r["embed"]),
      "first payload entry:", [e for e in r["embed"] if e["kind"] == "payload"][0])
print("self-detect ok:", r["detect"]["bits"] == bits)
json.dump({"pcm": "wm.wav", "detect": True, "seed": 7, "frame": 1024,
           "nbits": 40}, open("out/req_det.json", "w"))
subprocess.run([sys.executable, "wm.py", "out/req_det.json", "out/det.json"],
               check=True)
d = json.load(open("out/det.json"))["detect"]
print("cli detect: sync_found=%s score=%.2f bits_ok=%s missing=%d" % (
    d["sync_found"], d["sync_score"], d["bits"] == bits, len(d["missing"])))
