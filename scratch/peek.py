import struct
d = open("video0.mp4","rb").read()
print("mvhd", d[26090:26090+32].hex())
print("elst", d[26306:26306+28].hex())
print("tkhd", d[26206:26206+40].hex())
print("mdhd", d[26342:26342+32].hex())
print("hdlr", d[26374:26374+45].hex())
