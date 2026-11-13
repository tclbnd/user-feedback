import hashlib
import json
from pathlib import Path


def generate():
    fields=[]
    expected=[]
    for shot in range(7):
        if shot==3:
            for j in range(6):
                value=43+(j%2)*125
                rows=[[value+x%3 for x in range(10)] for _ in range(3)]
                fields.append({"id":len(fields),"parity":"bottom" if j%2==0 else "top",
                               "rows":rows,"cut":j==0})
            continue
        parity=(shot%2)
        for i in range(8):
            level=30+((i*39+shot*47)%170)
            if i==5:
                level=30+((4*39+shot*47)%170)
            image=[[level+x%3+y for x in range(10)] for y in range(6)]
            count=2+(i+shot)%2
            ids=[]
            for k in range(count):
                index=len(fields)
                ids.append(index)
                fields.append({"id":index,"parity":"bottom" if parity else "top",
                               "rows":image[parity::2],"cut":i==0 and k==0})
                parity=1-parity
            expected.append((hashlib.sha256(bytes(v for row in image for v in row)).hexdigest(),ids))
    return {"fields":fields},expected


if __name__=="__main__":
    Path("demo.json").write_text(json.dumps(generate()[0])+"\n")
