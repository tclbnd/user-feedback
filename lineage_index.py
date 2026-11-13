"""Incremental query dependency index.

Holds (schema, queries) -> Analysis. Recomputes only the affected nodes
when a table schema or a single query changes, computes impacted output
columns for source changes, and exports deterministic, reproducible
reports (affected columns + evidence paths + run manifest).
"""
import hashlib
import json

from analyzer import Analyzer, BC, bc_str

TOOL_VERSION = "lineage-index/1.0"


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class LineageIndex(object):
    def __init__(self, schema):
        self.schema = dict((t, list(cols)) for t, cols in schema.items())
        self.queries = {}
        self.analyses = {}
        self.analyzer = Analyzer(self.schema)

    # ---------------------------------------------------------- updates
    def set_query(self, qid, sql):
        """Add or replace one query; recompute only that node."""
        self.queries[qid] = sql
        self.analyses[qid] = self.analyzer.analyze(qid, sql)
        return [qid]

    def remove_query(self, qid):
        self.queries.pop(qid, None)
        self.analyses.pop(qid, None)
        return [qid]

    def update_schema(self, table, cols):
        """Change one table's schema; recompute every query that
        references the table (including unresolved references)."""
        self.schema[table] = list(cols)
        self.analyzer = Analyzer(self.schema)
        recomputed = []
        for qid, sql in sorted(self.queries.items()):
            a = self.analyses[qid]
            if table in a.tables:
                self.analyses[qid] = self.analyzer.analyze(qid, sql)
                recomputed.append(qid)
        return recomputed

    # ----------------------------------------------------------- impact
    @staticmethod
    def _matches(bc, change):
        if bc.table != change["table"]:
            return False
        kind = change.get("kind", "column")
        if kind == "rows":
            return True          # row insert/delete touches any dependency
        if change.get("col") is None:
            return True          # whole-table / schema change
        if bc.col == "*":
            return not bc.rows   # unknown-table wildcard: conservative
        return bc.col == change["col"]

    def impact(self, changes):
        """changes: list of dict(table=..., col=...|None,
        kind='column'|'rows'|'schema').
        Returns {qid: {outcol: {kinds: [...], evidence: [...]}}}."""
        affected = {}
        for qid in sorted(self.analyses):
            a = self.analyses[qid]
            for oc in a.outputs:
                kinds = set()
                evidence = []
                for ch in changes:
                    for bc, evs in oc.v.items():
                        if self._matches(bc, ch):
                            kinds.add("value")
                            evidence.extend(evs)
                    for bc, evs in oc.r.items():
                        if self._matches(bc, ch):
                            kinds.add("row")
                            evidence.extend(evs)
                if kinds:
                    seen, ev_out = set(), []
                    for e in evidence:
                        key = (e["scope"], e["line"], e["col"], e["text"],
                               e["kind"], e["note"])
                        if key not in seen:
                            seen.add(key)
                            ev_out.append(e)
                    affected.setdefault(qid, {})[oc.name] = dict(
                        kinds=sorted(kinds), evidence=ev_out)
        return affected

    # ----------------------------------------------------------- export
    def manifest(self):
        return dict(
            tool=TOOL_VERSION,
            schema_sha256=_sha(json.dumps(self.schema, sort_keys=True)),
            query_sha256=dict((qid, _sha(sql))
                              for qid, sql in sorted(self.queries.items())),
            query_count=len(self.queries),
        )

    def export_impact(self, changes, path):
        doc = dict(
            manifest=self.manifest(),
            changes=[dict((k, ch[k]) for k in sorted(ch)) for ch in changes],
            affected=self.impact(changes),
        )
        text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
        open(path, "w").write(text)
        return text

    def export_graph(self, path):
        """Full dependency graph with AST source positions."""
        queries = {}
        for qid in sorted(self.analyses):
            a = self.analyses[qid]
            outs = {}
            for oc in a.outputs:
                def edges(depmap, kind):
                    out = []
                    for bc, evs in sorted(depmap.items(),
                                          key=lambda kv: bc_str(kv[0])):
                        out.append(dict(
                            source=bc_str(bc),
                            rows_only=bool(bc.rows),
                            kind=kind,
                            evidence=evs))
                    return out
                outs[oc.name] = dict(
                    value=edges(oc.v, "value"), row=edges(oc.r, "row"))
            queries[qid] = dict(
                tables=sorted(a.tables), reports=a.reports, outputs=outs)
        doc = dict(manifest=self.manifest(), queries=queries)
        text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
        open(path, "w").write(text)
        return text
