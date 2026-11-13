"""SQL lineage prototype using flat column-name matches."""
import argparse
import json
from pathlib import Path
import sqlglot
from sqlglot import exp


def analyze(data):
    result=[]
    for entry in data["queries"]:
        tree=sqlglot.parse_one(entry["sql"],read="sqlite")
        cols=[]
        for sel in tree.selects:
            vals=sorted({table+"."+c.name for c in sel.find_all(exp.Column)
                         for table,fields in data["schemas"].items() if c.name in fields})
            cols.append({"name":sel.alias_or_name,"value":vals,"row":[],"unknown":False})
        result.append({"id":entry["id"],"columns":cols,"issues":[]})
    return {"queries":result}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    a.output.write_text(json.dumps(analyze(json.loads(a.input.read_text())),sort_keys=True)+"\n")


if __name__=="__main__":
    main()
