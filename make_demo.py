import math
import struct
import wave


def make(path,seconds=3,channels=2,case="normal"):
    n=round(48000*seconds)
    rows=bytearray()
    for i in range(n):
        t=i/48000
        vals=[]
        for ch in range(channels):
            if case=="silence":
                v=0
            elif case=="limited":
                v=.015*math.sin(2*math.pi*430*t)
                if 1.21<t<1.21016:
                    v=.95*math.sin(2*math.pi*12000*t+math.pi/4)
            else:
                v=(0 if .72<t<1.6 else .16)*math.sin(2*math.pi*(420+17*ch)*t)
                if channels==6 and ch==3:
                    v=.18*math.sin(2*math.pi*60*t)
            vals.append(round(v*32767))
        rows.extend(struct.pack("<"+"h"*channels,*vals))
    with wave.open(str(path),"wb") as f:
        f.setparams((channels,2,48000,n,"NONE","not compressed"))
        f.writeframes(rows)


if __name__=="__main__":
    make("demo.wav")
