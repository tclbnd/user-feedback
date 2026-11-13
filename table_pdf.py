"""PDF extraction front end with a text-stream baseline."""
import argparse
import json
from pathlib import Path
from pypdf import PdfReader


def extract(source,batch):
    pdf=PdfReader(source)
    rows=[]
    for i,page in enumerate(pdf.pages,1):
        for line in page.extract_text().splitlines():
            rows.append({"page":i,"text":line})
    return {"rows":rows,"rejected":[]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("source",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("--batch-pages",type=int,default=2)
    a=p.parse_args()
    a.output.write_text(json.dumps(extract(a.source,a.batch_pages),sort_keys=True)+"\n")


if __name__=="__main__":
    main()
