import sys
from pathlib import Path
from inspect_mp4 import track_infos, samples_from_info

def nal_types(payload):
    out=[]
    pos=0
    while pos+4 <= len(payload):
        n=int.from_bytes(payload[pos:pos+4],'big')
        pos+=4
        if n<=0 or pos+n>len(payload):
            out.append(('BAD',n)); break
        out.append(payload[pos]&31)
        pos+=n
    return out

def first_slice_info(payload):
    # return nal types; simpler
    return nal_types(payload)

def main():
    for path in sys.argv[1:]:
        data, infos = track_infos(path)
        v=[i for i in infos if i.get('handler')=='vide'][0]
        samples=samples_from_info(data,v)
        print('====',path,'stss',v['stss'])
        for s in samples:
            p=data[s['offset']:s['offset']+s['size']]
            print(s['i'], 'key' if s['key'] else '   ', 'pts',s['pts'],'dts',s['dts'],'types',first_slice_info(p))
main()
