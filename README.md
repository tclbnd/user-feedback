# ETL rule migration examples

fixtures/price-rules.json gives a finite input schema and two small pricing rules. Each case has an input domain, an older rule, and a proposed replacement. The expression vocabulary in this sample uses field, integer, is_missing, is_null, add, and if forms. The concrete interpreter and equivalence checker are to be implemented. Use separate outcome kinds for a field value and a classified error.
