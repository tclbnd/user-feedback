# Supplier NDJSON import fixtures

fixtures/supplier-a.ndjson is one logical append-only source. Offsets refer to UTF-8 bytes in that source. A repeated file uses the same source ID. An appended source retains its original bytes and ID. The importer and its replay/test entry points are to be implemented.

Version 1 stores amount as a decimal string. Version 2 stores amount_cents as an integer. Normalize to an exact amount with two decimal places and record currency. A missing note and a null note are distinct. Duplicate keys, invalid records, and amounts outside the declared precision must be classified. All rows are synthetic.
