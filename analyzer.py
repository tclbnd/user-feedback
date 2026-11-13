"""Scope-aware column lineage analyzer.

Resolves column references per query scope (aliases, CTEs incl. shadowing,
star expansion, subqueries, outer joins) and splits dependencies into:
  - "value": source columns that determine an output column's VALUES
  - "row":   source columns that determine WHICH ROWS appear (filters,
             join conditions, GROUP BY/HAVING, DISTINCT, LIMIT+ORDER BY)

Every dependency edge carries evidence: a chain of AST source positions
(scope, line, col) from use site through CTE/subquery hops.
Incomplete schema, ambiguity and unsupported syntax produce reports with
positions plus a conservative dependency over-approximation.
"""

from collections import namedtuple

from sqlfront import (Parser, ParseError, tokenize, Node, Select, SetOp, With,
                      QueryTail, Table, Subq, Join, Col, Star, QStar, Func,
                      ScalarSubq, Exists, InOp)

BC = namedtuple("BC", ["table", "col", "rows"])
BC.__new__.__defaults__ = (False,)


def bc_str(bc):
    return "%s.%s" % (bc.table, bc.col)


def merge(dst, src):
    for bc, evs in src.items():
        dst.setdefault(bc, []).extend(evs)


class OutCol(object):
    def __init__(self, name, v=None, r=None):
        self.name = name
        self.v = v if v is not None else {}
        self.r = r if r is not None else {}


class Source(object):
    def __init__(self, kind, alias, table, cols, outcols, line=0, col=0):
        self.kind, self.alias, self.table = kind, alias, table
        self.cols, self.outcols = cols, outcols
        self.line, self.col = line, col


class CteDef(object):
    def __init__(self, cte, env, recursive):
        self.cte, self.env, self.recursive = cte, env, recursive


class Ctx(object):
    def __init__(self, an, label, outer=None, ctes=None):
        self.an = an
        self.label = label
        self.outer = outer
        self.ctes = ctes if ctes is not None else {}
        self.sources = []
        self.lifted_r = {}
        self.sub_count = 0


class Analysis(object):
    def __init__(self, qid, sql, outputs, reports, tables):
        self.qid, self.sql, self.outputs = qid, sql, outputs
        self.reports, self.tables = reports, tables

    def dep_map(self):
        d = {}
        for o in self.outputs:
            d[o.name] = dict(
                value=sorted(set(bc_str(bc) for bc in o.v)),
                row=sorted(set(bc_str(bc) for bc in o.r)))
        return d


def walk(node):
    yield node
    for v in vars(node).values():
        if isinstance(v, Node):
            for x in walk(v):
                yield x
        elif isinstance(v, (list, tuple)):
            for it in v:
                if isinstance(it, Node):
                    for x in walk(it):
                        yield x
                elif isinstance(it, (list, tuple)):
                    for it2 in it:
                        if isinstance(it2, Node):
                            for x in walk(it2):
                                yield x


class Analyzer(object):
    def __init__(self, schema):
        self.schema = schema

    def ev(self, scope, line, col, text, kind, note=""):
        return dict(scope=scope, line=line, col=col, text=text,
                    kind=kind, note=note)

    def report(self, kind, message, line, col, conservative=""):
        self.reports.append(dict(kind=kind, message=message, line=line,
                                 col=col, conservative=conservative))

    def analyze(self, qid, sql):
        self.qid = qid
        self.reports = []
        self.tables = set()
        self._cte_memo = {}
        self._cte_busy = set()
        try:
            ast = Parser(tokenize(sql)).parse()
        except ParseError as e:
            self.report("parse_error", str(e), e.line, e.col,
                        conservative="all outputs depend on all columns of "
                                     "all tables in the schema")
            outs = [OutCol("*")]
            for t in sorted(self.schema):
                for c in self.schema[t]:
                    ev = [self.ev(qid, e.line, e.col, "(parse error)",
                                  "value", note="conservative")]
                    outs[0].v[BC(t, c)] = list(ev)
                    outs[0].r[BC(t, c)] = list(ev)
            return Analysis(qid, sql, outs, self.reports,
                            set(self.schema))
        ctx = Ctx(self, qid)
        outs = self.resolve_query(ast, ctx)
        return Analysis(qid, sql, outs, self.reports, set(self.tables))

    # ---------------------------------------------------------- queries
    def resolve_query(self, node, ctx):
        if isinstance(node, With):
            env = dict(ctx.ctes)
            defs = {}
            for cte in node.ctes:
                if node.recursive:
                    snap = env
                else:
                    snap = dict(env)
                d = CteDef(cte, snap, node.recursive)
                defs[cte.name] = d
                env[cte.name] = d
            ctx2 = Ctx(self, ctx.label, outer=ctx.outer, ctes=env)
            return self.resolve_query(node.body, ctx2)
        if isinstance(node, QueryTail):
            if isinstance(node.inner, Select):
                return self.resolve_select(node.inner, ctx, tail=node)
            outs = self.resolve_query(node.inner, ctx)
            self.apply_tail(node, outs, None)
            return outs
        if isinstance(node, SetOp):
            return self.resolve_setop(node, ctx)
        if isinstance(node, Select):
            return self.resolve_select(node, ctx)
        raise ParseError("cannot resolve query node", node.line, node.col)

    def resolve_setop(self, node, ctx):
        lo = self.resolve_query(node.left, ctx)
        ro = self.resolve_query(node.right, ctx)
        if len(lo) != len(ro):
            self.report("unsupported",
                        "%s branches have different column counts (%d vs %d)"
                        % (node.op, len(lo), len(ro)),
                        node.line, node.col,
                        conservative="extra columns dropped from lineage")
        outs = []
        for i, l in enumerate(lo):
            oc = OutCol(l.name)
            merge(oc.v, l.v)
            merge(oc.r, l.r)
            if i < len(ro):
                merge(oc.v, ro[i].v)
                merge(oc.r, ro[i].r)
            outs.append(oc)
        if not (node.op == "UNION" and node.all):
            # dedup / membership: row existence depends on the VALUES OF
            # THE WHOLE ROW, so every output shares the combined value deps
            combined = {}
            for oc in outs:
                merge(combined, oc.v)
            for oc in outs:
                merge(oc.r, combined)
        return outs

    def apply_tail(self, tail, outs, sctx):
        if not (tail.limit and tail.order):
            return
        extra = {}
        outnames = dict((o.name, o) for o in outs)
        for item in tail.order:
            e = item.expr
            if isinstance(e, Col) and e.table is None \
                    and e.name in outnames:
                oc = outnames[e.name]
                ev = self.ev(sctx.label if sctx else self.qid,
                             e.line, e.col, e.text, "row",
                             note="ORDER BY with LIMIT decides row "
                                  "membership")
                for bc, evs in oc.v.items():
                    extra.setdefault(bc, []).append(ev)
                    extra[bc].extend(evs)
            elif sctx is not None:
                self.resolve_expr(e, sctx, "row", extra)
            else:
                self.report(
                    "unsupported",
                    "ORDER BY expression over a set operation cannot be "
                    "resolved to source columns",
                    e.line, e.col,
                    conservative="row dependencies of all outputs widened")
        for o in outs:
            merge(o.r, extra)

    # ---------------------------------------------------------- SELECT
    def resolve_select(self, sel, ctx, tail=None):
        sctx = Ctx(self, ctx.label, outer=ctx.outer, ctes=ctx.ctes)
        join_conds = []
        if sel.from_ is not None:
            self.build_from(sel.from_, sctx, join_conds)
        scope_r = {}
        merge(scope_r, sctx.lifted_r)
        for src in sctx.sources:
            if src.kind == "table":
                # scanning a table: output row membership depends on the
                # table's row set (rows-only dep: matched by row
                # insert/delete, not by column value changes)
                bc = BC(src.table, "*", src.cols is not None)
                scope_r.setdefault(bc, []).append(
                    self.ev(sctx.label, src.line, src.col,
                            "scan " + (src.table or "?"), "row",
                            note="table scan row set"))
        for on in join_conds:
            self.resolve_expr(on, sctx, "row", scope_r)
        if sel.where is not None:
            self.resolve_expr(sel.where, sctx, "row", scope_r)
        if sel.group:
            for g in sel.group:
                self.resolve_expr(g, sctx, "row", scope_r)
        if sel.having is not None:
            self.resolve_expr(sel.having, sctx, "row", scope_r)
        outs = []
        for idx, item in enumerate(sel.items):
            e = item.expr
            if isinstance(e, Star):
                self.expand_star(outs, sctx, None, e)
            elif isinstance(e, QStar):
                self.expand_star(outs, sctx, e.table, e)
            else:
                v = {}
                self.resolve_expr(e, sctx, "value", v)
                if item.alias:
                    name = item.alias
                elif isinstance(e, Col):
                    name = e.name
                else:
                    name = "expr_%d" % (idx + 1)
                outs.append(OutCol(name, v, {}))
        for o in outs:
            merge(o.r, scope_r)
        if sel.distinct:
            # DISTINCT dedups on the whole row: every output's row
            # membership depends on all outputs' value deps
            combined = {}
            for o in outs:
                merge(combined, o.v)
            for o in outs:
                merge(o.r, combined)
        if tail is not None:
            self.apply_tail(tail, outs, sctx)
        return outs

    # ---------------------------------------------------------- FROM
    def build_from(self, node, sctx, join_conds):
        if isinstance(node, Join):
            self.build_from(node.left, sctx, join_conds)
            self.build_from(node.right, sctx, join_conds)
            if node.on is not None:
                join_conds.append(node.on)
            if node.using:
                for u in node.using:
                    for src in sctx.sources:
                        if src.cols is not None and u in src.cols:
                            c = Col(node.line, node.col, table=src.alias,
                                    name=u, text="%s.%s" % (src.alias, u))
                            self.resolve_expr(c, sctx, "row", sctx.lifted_r)
            return
        if isinstance(node, Table):
            name = node.name
            if name in sctx.ctes:
                outs = self.resolve_cte(name, sctx.ctes[name], sctx)
                src = Source("query", node.alias or name, None,
                             [o.name for o in outs], outs)
                sctx.sources.append(src)
                for oc in outs:
                    merge(sctx.lifted_r, oc.r)
                return
            self.tables.add(name)
            cols = self.schema.get(name)
            if cols is None:
                self.report(
                    "missing_table",
                    "table %r is not in the schema; its columns are unknown"
                    % name, node.line, node.col,
                    conservative="references to %s.<col> are taken as stated;"
                                 " unqualified references widen to %s.*"
                                 % (name, name))
            sctx.sources.append(
                Source("table", node.alias or name, name, cols, None,
                       node.line, node.col))
            return
        if isinstance(node, Subq):
            alias = node.alias
            if not alias:
                alias = "sub_%d" % (len(sctx.sources) + 1)
                self.report("unsupported",
                            "derived table without alias", node.line,
                            node.col,
                            conservative="auto-named %s" % alias)
            sub_ctx = Ctx(self, "%s.%s" % (sctx.label, alias),
                          outer=None, ctes=sctx.ctes)
            outs = self.resolve_query(node.query, sub_ctx)
            sctx.sources.append(
                Source("query", alias, None, [o.name for o in outs], outs))
            for oc in outs:
                merge(sctx.lifted_r, oc.r)
            return
        raise ParseError("unsupported FROM item", node.line, node.col)

    def resolve_cte(self, name, ctedef, sctx):
        key = id(ctedef.cte)
        if key in self._cte_memo:
            return self._cte_memo[key]
        if key in self._cte_busy or ctedef.recursive:
            outs = self.conservative_cte(name, ctedef, sctx)
            self._cte_memo[key] = outs
            return outs
        self._cte_busy.add(key)
        cctx = Ctx(self, "%s.cte_%s" % (sctx.label, name),
                   outer=None, ctes=ctedef.env)
        outs = self.resolve_query(ctedef.cte.body, cctx)
        aliases = ctedef.cte.col_aliases
        if aliases:
            new = []
            for i, oc in enumerate(outs):
                nm = aliases[i] if i < len(aliases) else oc.name
                new.append(OutCol(nm, oc.v, oc.r))
            for j in range(len(outs), len(aliases)):
                new.append(OutCol(aliases[j]))
            outs = new
        self._cte_busy.discard(key)
        self._cte_memo[key] = outs
        return outs

    def conservative_cte(self, name, ctedef, sctx):
        cte = ctedef.cte
        tabs = []
        for n in walk(cte.body):
            if isinstance(n, Table) and n.name not in tabs \
                    and n.name not in ctedef.env:
                tabs.append(n.name)
        v, r = {}, {}
        for t in tabs:
            self.tables.add(t)
            cols = self.schema.get(t)
            ev = [self.ev(sctx.label, cte.line, cte.col,
                          "RECURSIVE CTE " + name, "value",
                          note="conservative")]
            if cols is None:
                v[BC(t, "*")] = list(ev)
                r[BC(t, "*")] = list(ev)
            else:
                for c in cols:
                    v[BC(t, c)] = list(ev)
                    r[BC(t, c)] = list(ev)
        names = cte.col_aliases or ["*"]
        self.report(
            "unsupported",
            "recursive CTE %r is not supported" % name,
            cte.line, cte.col,
            conservative="its output columns depend (value+row) on all "
                         "columns of: %s" % ", ".join(tabs))
        return [OutCol(nm, dict(v), dict(r)) for nm in names]

    # ---------------------------------------------------------- expressions
    def resolve_expr(self, e, ctx, kind, deps):
        if e is None:
            return
        if isinstance(e, Col):
            d = self.resolve_col(e, ctx)
            if d is None:
                self.report(
                    "unresolved_column",
                    "column %r cannot be resolved in any enclosing scope"
                    % col_text(e), e.line, e.col,
                    conservative="no dependencies recorded (no tables in "
                                 "scope)")
                return
            ev = self.ev(ctx.label, e.line, e.col, col_text(e), kind)
            for bc, evs in d.items():
                deps.setdefault(bc, []).append(ev)
                deps[bc].extend(evs)
            return
        if isinstance(e, Star):
            for bc in self.scope_base_cols(ctx):
                deps.setdefault(bc, []).append(
                    self.ev(ctx.label, e.line, e.col, "COUNT(*)", kind,
                            note="row-count dependency"))
            return
        if isinstance(e, ScalarSubq) or isinstance(e, Exists):
            self.resolve_subquery(e.query, ctx, kind, deps, e)
            return
        if isinstance(e, InOp):
            self.resolve_expr(e.e, ctx, kind, deps)
            if e.query is not None:
                self.resolve_subquery(e.query, ctx, kind, deps, e)
            if e.items:
                for it in e.items:
                    self.resolve_expr(it, ctx, kind, deps)
            return
        if isinstance(e, Func):
            for a in e.args:
                self.resolve_expr(a, ctx, kind, deps)
            if e.over is not None:
                for p in (e.over.partition or []):
                    self.resolve_expr(p, ctx, kind, deps)
                for o in (e.over.order or []):
                    self.resolve_expr(o.expr, ctx, kind, deps)
            return
        for child in walk_children(e):
            self.resolve_expr(child, ctx, kind, deps)

    def resolve_subquery(self, q, ctx, kind, deps, node):
        ctx.sub_count += 1
        sub_ctx = Ctx(self, "%s.sub%d" % (ctx.label, ctx.sub_count),
                      outer=ctx, ctes=ctx.ctes)
        outs = self.resolve_query(q, sub_ctx)
        ev = self.ev(ctx.label, node.line, node.col,
                     type(node).__name__, kind, note="via subquery")
        for oc in outs:
            for bc, evs in oc.v.items():
                deps.setdefault(bc, []).append(ev)
                deps[bc].extend(evs)
            for bc, evs in oc.r.items():
                deps.setdefault(bc, []).append(ev)
                deps[bc].extend(evs)

    def scope_base_cols(self, ctx):
        bcs = []
        seen = set()
        for s in ctx.sources:
            if s.kind == "table":
                key = (s.table, s.cols is None)
                if key in seen:
                    continue
                seen.add(key)
                if s.cols is None:
                    bcs.append(BC(s.table, "*"))
                else:
                    bcs.append(BC(s.table, "*", True))
            else:
                for oc in s.outcols:
                    for bc in list(oc.v) + list(oc.r):
                        k = ("q", bc.table)
                        if k not in seen:
                            seen.add(k)
                            bcs.append(BC(bc.table, "*", True))
        return bcs

    # ---------------------------------------------------------- columns
    def resolve_col(self, col, ctx):
        """Resolve a column reference. Returns dict BC -> [evidence] or
        None when nothing in any scope can provide it."""
        name, qual = col.name, col.table
        if qual is not None:
            for src in ctx.sources:
                if src.alias == qual or (src.kind == "table"
                                         and src.table == qual):
                    return self.col_from_source(src, col)
            if ctx.outer is not None:
                d = self.resolve_col(col, ctx.outer)
                if d is not None:
                    return self.mark_correlated(d, ctx)
            self.report(
                "unknown_qualifier",
                "no table, subquery or CTE named %r is visible here" % qual,
                col.line, col.col,
                conservative="dependency recorded as stated: %s.%s"
                             % (qual, name))
            return dict([(BC(qual, name), [])])
        cands = [s for s in ctx.sources
                 if s.cols is not None and name in s.cols]
        if len(cands) == 1:
            return self.col_from_source(cands[0], col)
        if len(cands) > 1:
            self.report(
                "ambiguous",
                "unqualified column %r matches %d sources in scope: %s"
                % (name, len(cands), ", ".join(
                    "%s.%s" % (s.table or s.alias, name) for s in cands)),
                col.line, col.col,
                conservative="depends on ALL candidate columns")
            deps = {}
            for s in cands:
                merge(deps, self.col_from_source(s, col))
            return deps
        unknown = [s for s in ctx.sources if s.cols is None]
        if unknown:
            self.report(
                "unresolved_column",
                "column %r: schema of %s is unknown, cannot verify"
                % (name, ", ".join(s.table for s in unknown)),
                col.line, col.col,
                conservative="depends on all columns of: %s"
                             % ", ".join(s.table for s in unknown))
            return dict((BC(s.table, "*"), []) for s in unknown)
        if ctx.outer is not None:
            d = self.resolve_col(col, ctx.outer)
            if d is not None:
                return self.mark_correlated(d, ctx)
        if ctx.sources:
            self.report(
                "unresolved_column",
                "column %r not found in any source of this scope" % name,
                col.line, col.col,
                conservative="depends on all columns of all sources in "
                             "scope")
            deps = {}
            for s in ctx.sources:
                if s.cols is None:
                    deps[BC(s.table, "*")] = []
                elif s.kind == "table":
                    for c in s.cols:
                        deps[BC(s.table, c)] = []
                else:
                    for oc in s.outcols:
                        merge(deps, oc.v)
            return deps
        return None

    def col_from_source(self, src, col):
        if src.kind == "table":
            if src.cols is None:
                return dict([(BC(src.table, col.name), [])])
            if col.name in src.cols:
                return dict([(BC(src.table, col.name), [])])
            self.report(
                "missing_column",
                "column %r does not exist in table %r"
                % (col.name, src.table), col.line, col.col,
                conservative="depends on all columns of %s" % src.table)
            return dict([(BC(src.table, "*"), [])])
        for oc in src.outcols:
            if oc.name == col.name:
                d = {}
                for bc, evs in oc.v.items():
                    d[bc] = list(evs)
                return d
        self.report(
            "missing_column",
            "column %r is not produced by subquery/CTE %r"
            % (col.name, src.alias), col.line, col.col,
            conservative="depends on every output column of %s" % src.alias)
        d = {}
        for oc in src.outcols:
            merge(d, oc.v)
        return d

    def mark_correlated(self, d, ctx):
        out = {}
        for bc, evs in d.items():
            out[bc] = [self.ev(ctx.label, 0, 0, "", "row",
                               note="correlated reference")] + list(evs)
        return out

    # ---------------------------------------------------------- star
    def expand_star(self, outs, sctx, qual, node):
        matched = False
        for src in sctx.sources:
            if qual is not None and not (
                    src.alias == qual
                    or (src.kind == "table" and src.table == qual)):
                continue
            matched = True
            if src.kind == "table":
                if src.cols is None:
                    oc = OutCol(src.alias + ".*")
                    oc.v[BC(src.table, "*")] = [
                        self.ev(sctx.label, node.line, node.col,
                                src.alias + ".*", "value",
                                note="schema unknown, conservative")]
                    outs.append(oc)
                else:
                    for c in src.cols:
                        oc = OutCol(c)
                        oc.v[BC(src.table, c)] = [
                            self.ev(sctx.label, node.line, node.col,
                                    "%s.%s" % (src.alias, c), "value")]
                        outs.append(oc)
            else:
                for o in src.outcols:
                    oc = OutCol(o.name)
                    ev = self.ev(sctx.label, node.line, node.col,
                                 "%s.*" % src.alias, "value")
                    for bc, evs in o.v.items():
                        oc.v[bc] = [ev] + list(evs)
                    outs.append(oc)
        if not matched:
            if qual is not None:
                self.report(
                    "unknown_qualifier",
                    "no table, subquery or CTE named %r is visible here"
                    % qual, node.line, node.col,
                    conservative="star expands to nothing")
            elif not sctx.sources:
                self.report("unsupported",
                            "SELECT * without FROM", node.line, node.col,
                            conservative="no output columns")


def col_text(col):
    if col.table:
        return "%s.%s" % (col.table, col.name)
    return col.name


def walk_children(node):
    for v in vars(node).values():
        if isinstance(v, Node):
            yield v
        elif isinstance(v, (list, tuple)):
            for it in v:
                if isinstance(it, Node):
                    yield it
                elif isinstance(it, (list, tuple)):
                    for it2 in it:
                        if isinstance(it2, Node):
                            yield it2
