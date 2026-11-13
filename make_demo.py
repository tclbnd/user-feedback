from pathlib import Path
import struct
import zlib


def c(kind,data):
    return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data))


def main():
    png=b"\x89PNG\r\n\x1a\n"+c(b"IHDR",struct.pack(">IIBBBBB",3,2,8,6,0,0,0))
    png+=c(b"acTL",struct.pack(">II",2,0))
    png+=c(b"fcTL",struct.pack(">IIIIIHHBB",0,3,2,0,0,1,25,0,0))
    png+=c(b"IDAT",zlib.compress((b"\0"+bytes([40,120,240,255])*3)*2))
    png+=c(b"fcTL",struct.pack(">IIIIIHHBB",1,1,1,1,0,2,25,2,1))
    png+=c(b"fdAT",struct.pack(">I",2)+zlib.compress(bytes([0,240,40,15,128])))
    Path("demo.png").write_bytes(png+c(b"IEND",b""))


if __name__=="__main__":
    main()
