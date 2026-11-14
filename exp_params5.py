from inspect_mp4 import track_infos, samples_from_info

def nals(p):
    out = []
    pos = 0
    while pos + 4 <= len(p):
        n = int.from_bytes(p[pos:pos + 4], "big")
        pos += 4
        if n <= 0 or pos + n > len(p):
            break
        out.append(p[pos:pos + n])
        pos += n
    return out

data, infos = track_infos("video0.mp4")
v = [i for i in infos if i.get("handler") == "vide"][0]
s = samples_from_info(data, v)[0]
for nal in nals(data[s["offset"]:s["offset"] + s["size"]]):
    if nal[0] & 31 == 6:
        i = nal.find(b"options: ")
        txt = nal[i + 9:].split(b"\x00")[0].decode()
        open("params_v0.txt", "w").write(txt)
        print(len(txt), repr(txt[-30:]))
