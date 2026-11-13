from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
import argparse
import pyarrow as pa
import pyarrow.parquet as pq


def f(name,kind,id):
    return pa.field(name,kind,metadata={b"PARQUET:field_id":str(id).encode()})


def files(root):
    root.mkdir(parents=True,exist_ok=True)
    dec=pa.decimal128(14,3)
    rows=[
        ("v1",[f("customer",pa.string(),1),f("label",pa.string(),2),f("rank",dec,3)],
         [{"customer":"one","label":"original","rank":Decimal("7.255")}]),
        ("v2",[f("customer",pa.string(),1),f("label",pa.string(),90),f("renamed",pa.string(),2),
               f("charge",dec,3),f("entries",pa.list_(pa.struct([f("what",pa.string(),5),f("price",dec,6)])),4)],
         [{"customer":"two","label":"ignore","renamed":"kept","charge":Decimal("7.245"),
           "entries":[{"what":"usd","price":Decimal("1.255")},None]},
          {"customer":"three","label":"ignore","renamed":None,"charge":Decimal("999999.000"),"entries":[]}]),
        ("v3",[f("id",pa.string(),1),f("label",pa.string(),90),
               f("entries",pa.list_(pa.struct([f("name",pa.string(),5),f("total",dec,6)])),4),
               f("at",pa.timestamp("ms",tz="UTC"),7)],
         [{"id":"four","label":"ignore","entries":[],"at":datetime(2026,1,1,tzinfo=timezone.utc)},
          {"id":"five","label":"ignore","entries":None,"at":None}]),
        ("v4",[f("id",pa.string(),1),f("at",pa.timestamp("us"),7)],
         [{"id":"badtime","at":datetime(2026,1,1)}]),
        ("legacy",[pa.field("id",pa.string()),pa.field("same",pa.string())],
         [{"id":"unmapped","same":"ambiguous"}]),
    ]
    for name,schema,data in rows:
        pq.write_table(pa.Table.from_pylist(data,schema=pa.schema(schema)),root/f"{name}.parquet")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    files(p.parse_args().root)
