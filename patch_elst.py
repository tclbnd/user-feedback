import struct, shutil, sys
from inspect_mp4 import track_infos, boxes

src, dst, dur = sys.argv[1], sys.argv[2], int(sys.argv[3])
data = bytearray(open(src,'rb').read())
# find moov then second trak's elst
moov = [ (p,hs,pe) for t,p,hs,pe in boxes(data) if t=='moov' ][0]
traks = [ (p,hs,pe) for t,p,hs,pe in boxes(data, moov[1], moov[2]) if t=='trak' ]
# audio trak is second typically; locate elst under each and print handler
from inspect_mp4 import find
for ti,(tp,ths,tpe) in enumerate(traks):
    for p,hs,pe in find(data,'elst',ths,tpe):
        print('trak',ti,'elst at',p,'payload',data[hs:pe].hex())
open(dst,'wb').write(data)
