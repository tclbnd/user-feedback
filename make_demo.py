import argparse
from io import BytesIO
import random
from pathlib import Path
from pypdf import PdfReader,PdfWriter
from reportlab.pdfgen import canvas


def create(target,variant=0,fault=False):
    stream=BytesIO()
    sheet=canvas.Canvas(stream,pagesize=(612,792),invariant=True)
    truth=[]
    last=None
    for page in range(1,4):
        glyphs=[(32,767,f"TABLE TRANSFER / {page}"),(31,32,"ARCHIVE INDEX FOOTER")]
        for column in range(2):
            base=32+column*294+(variant%3)
            x=(base,base+47,base+198)
            glyphs.extend(zip(x,[724]*3,("KEY","DESCRIPTION","AMOUNT")))
            if page>1 and column==0:
                y=705
                marker=f"part-p{page}"
                glyphs.append((x[1],y,f"CONT: {marker}"))
                if last is not None:
                    last["description"]+=" "+marker
                    last["source"]["description"].append({"page":page,"x":x[1],"y":y})
            for i in range(4):
                n=(page-1)*8+column*4+i
                y=674-i*42
                id=f"R{n:03d}"
                label=f"case-{variant}-{n}"
                amount=None if n%5==0 else n*14
                glyphs.extend([(x[0],y,id),(x[1],y,label)])
                provenance=lambda xx,yy:{"page":page,"x":xx,"y":yy}
                entry={"id":id,"description":label,"amount":amount,
                       "source":{"id":[provenance(x[0],y)],"description":[provenance(x[1],y)],
                                 "amount":[] if amount is None else [provenance(x[2],y)]}}
                if amount is not None:
                    glyphs.append((x[2],y,str(amount)))
                if n%4==1:
                    glyphs.append((x[1],y-14,"continued"))
                    entry["description"]+=" continued"
                    entry["source"]["description"].append(provenance(x[1],y-14))
                if fault and n==2:
                    glyphs.append((x[2]+9,y,"777"))
                else:
                    truth.append(entry)
                    last=entry
        if fault and page==1:
            glyphs.append((79,704,"CONT: stray"))
        random.Random(variant*19+page).shuffle(glyphs)
        sheet.setFont("Helvetica",9)
        for x,y,text in glyphs:
            sheet.drawString(x,y,text)
        sheet.showPage()
    sheet.save()
    stream.seek(0)
    pdf=PdfReader(stream)
    writer=PdfWriter()
    for i,p in enumerate(pdf.pages):
        if i==1:
            p.rotate(90 if variant%2 else 270)
        writer.add_page(p)
    with target.open("wb") as out:
        writer.write(out)
    return truth


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("output",type=Path)
    a=p.parse_args()
    create(a.output)
