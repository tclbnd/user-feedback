"""Minimal SQL front-end for the SQLite SELECT dialect.

Every AST node carries the 1-based (line, col) of its first token.
Anything outside the supported grammar raises ParseError with the
position of the offending token (surfaced upstream as unsupported syntax).
"""


class ParseError(Exception):
    def __init__(self, msg, line, col):
        super().__init__("%s at line %d, col %d" % (msg, line, col))
        self.msg, self.line, self.col = msg, line, col


class Tok(object):
    __slots__ = ("kind", "val", "line", "col")

    def __init__(self, kind, val, line, col):
        self.kind, self.val, self.line, self.col = kind, val, line, col

    def __repr__(self):
        return "Tok(%s,%r,%d,%d)" % (self.kind, self.val, self.line, self.col)


_OPS2 = ("<=", ">=", "<>", "!=", "||")
_OPS1 = set("(),.;+-*/%=<>")


def tokenize(sql):
    toks, i, line, col, n = [], 0, 1, 1, len(sql)
    while i < n:
        ch = sql[i]
        if ch in " \t\r":
            i += 1
            col += 1
            continue
        if ch == "\n":
            i += 1
            line += 1
            col = 1
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j < 0:
                raise ParseError("unterminated comment", line, col)
            seg = sql[i:j + 2]
            line += seg.count("\n")
            if "\n" in seg:
                col = len(seg) - seg.rfind("\n")
            else:
                col += len(seg)
            i = j + 2
            continue
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise ParseError("unterminated string", line, col)
            toks.append(Tok("string", sql[i:j + 1], line, col))
            col += j + 1 - i
            i = j + 1
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and sql[i + 1].isdigit()):
            j = i
            while j < n and (sql[j].isdigit() or sql[j] == "."):
                j += 1
            toks.append(Tok("number", sql[i:j], line, col))
            col += j - i
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (sql[j].isalnum() or sql[j] in "_$"):
                j += 1
            toks.append(Tok("ident", sql[i:j], line, col))
            col += j - i
            i = j
            continue
        if ch in "\"`":
            j = sql.find(ch, i + 1)
            if j < 0:
                raise ParseError("unterminated quoted identifier", line, col)
            toks.append(Tok("ident", sql[i + 1:j], line, col))
            col += j + 1 - i
            i = j + 1
            continue
        two = sql[i:i + 2]
        if two in _OPS2:
            toks.append(Tok("op", two, line, col))
            i += 2
            col += 2
            continue
        if ch in _OPS1:
            toks.append(Tok("op", ch, line, col))
            i += 1
            col += 1
            continue
        raise ParseError("unexpected character %r" % ch, line, col)
    toks.append(Tok("eof", "", line, col))
    return toks


class Node(object):
    def __init__(self, line, col, **kw):
        self.line, self.col = line, col
        self.__dict__.update(kw)


class Select(Node):
    pass  # distinct, items, from_, where, group, having


class SetOp(Node):
    pass  # op, all, left, right


class With(Node):
    pass  # ctes, recursive, body


class QueryTail(Node):
    pass  # inner, order, limit


class CTE(Node):
    pass  # name, col_aliases, body


class Table(Node):
    pass  # name, alias


class Subq(Node):
    pass  # query, alias


class Join(Node):
    pass  # kind, left, right, on, using


class Col(Node):
    pass  # table (may be None), name, text


class Star(Node):
    pass


class QStar(Node):
    pass  # table


class Item(Node):
    pass  # expr, alias


class Func(Node):
    pass  # name, args, distinct, over


class Over(Node):
    pass  # partition, order


class Case(Node):
    pass  # operand, whens [(cond, res)], else_


class Lit(Node):
    pass  # value


class Bin(Node):
    pass  # op, l, r


class Un(Node):
    pass  # op, e


class IsNull(Node):
    pass  # e, negated


class InOp(Node):
    pass  # e, items or query, negated


class Exists(Node):
    pass  # query


class Between(Node):
    pass  # e, lo, hi, negated


class Cast(Node):
    pass  # e, type_name


class ScalarSubq(Node):
    pass  # query


class OrderItem(Node):
    pass  # expr, desc


RESV = set((
    "SELECT FROM WHERE GROUP HAVING ORDER LIMIT OFFSET BY AS ON USING JOIN "
    "INNER LEFT RIGHT FULL CROSS OUTER UNION ALL DISTINCT INTERSECT EXCEPT "
    "AND OR NOT NULL CASE WHEN THEN ELSE END IN EXISTS BETWEEN LIKE IS CAST "
    "ASC DESC WITH RECURSIVE OVER PARTITION NATURAL TRUE FALSE"
).split())


class Parser(object):
    def __init__(self, toks):
        self.toks, self.pos = toks, 0

    def tok(self, k=0):
        i = self.pos + k
        return self.toks[i] if i < len(self.toks) else self.toks[-1]

    def next(self):
        t = self.toks[self.pos]
        self.pos = min(self.pos + 1, len(self.toks) - 1)
        return t

    def at_op(self, v):
        return self.tok().kind == "op" and self.tok().val == v

    def accept(self, v):
        if self.at_op(v):
            return self.next()
        return None

    def expect(self, v):
        t = self.accept(v)
        if t is None:
            raise ParseError("expected %r, found %r" % (v, self.tok().val),
                             self.tok().line, self.tok().col)
        return t

    def at_kw(self, kw):
        t = self.tok()
        return t.kind == "ident" and t.val.upper() == kw

    def accept_kw(self, kw):
        if self.at_kw(kw):
            return self.next()
        return None

    def expect_kw(self, kw):
        t = self.accept_kw(kw)
        if t is None:
            raise ParseError("expected %s, found %r" % (kw, self.tok().val),
                             self.tok().line, self.tok().col)
        return t

    def expect_ident(self):
        t = self.tok()
        if t.kind != "ident" or t.val.upper() in RESV:
            raise ParseError("expected identifier, found %r" % t.val,
                             t.line, t.col)
        return self.next()

    def parse(self):
        q = self.parse_query()
        self.accept(";")
        if self.tok().kind != "eof":
            raise ParseError("unexpected trailing input %r" % self.tok().val,
                             self.tok().line, self.tok().col)
        return q

    def parse_query(self):
        ctes, rec, wt = None, False, None
        if self.at_kw("WITH"):
            wt = self.next()
            rec = bool(self.accept_kw("RECURSIVE"))
            ctes = []
            while True:
                nt = self.expect_ident()
                aliases = None
                if self.accept("("):
                    aliases = [self.expect_ident().val]
                    while self.accept(","):
                        aliases.append(self.expect_ident().val)
                    self.expect(")")
                self.expect_kw("AS")
                self.expect("(")
                body = self.parse_query()
                self.expect(")")
                ctes.append(CTE(nt.line, nt.col, name=nt.val,
                                col_aliases=aliases, body=body))
                if not self.accept(","):
                    break
        body = self.parse_body()
        if ctes:
            return With(wt.line, wt.col, ctes=ctes, recursive=rec, body=body)
        return body

    def parse_body(self):
        left = self.parse_core()
        while self.at_kw("UNION") or self.at_kw("INTERSECT") \
                or self.at_kw("EXCEPT"):
            op = self.next().val.upper()
            all_ = False
            if self.accept_kw("ALL"):
                all_ = True
            else:
                self.accept_kw("DISTINCT")
            right = self.parse_core()
            left = SetOp(left.line, left.col, op=op, all=all_,
                         left=left, right=right)
        order, limit = None, None
        if self.at_kw("ORDER"):
            self.next()
            self.expect_kw("BY")
            order = [self.parse_order_item()]
            while self.accept(","):
                order.append(self.parse_order_item())
        if self.at_kw("LIMIT"):
            self.next()
            limit = self.parse_expr()
            if self.at_kw("OFFSET"):
                self.next()
                self.parse_expr()
            elif self.accept(","):
                self.parse_expr()
        if order or limit:
            return QueryTail(left.line, left.col, inner=left,
                             order=order, limit=limit)
        return left

    def parse_order_item(self):
        e = self.parse_expr()
        desc = False
        if self.accept_kw("DESC"):
            desc = True
        else:
            self.accept_kw("ASC")
        return OrderItem(e.line, e.col, expr=e, desc=desc)

    def parse_core(self):
        if self.at_op("("):
            self.next()
            q = self.parse_query()
            self.expect(")")
            return q
        st = self.expect_kw("SELECT")
        distinct = False
        if self.accept_kw("DISTINCT"):
            distinct = True
        else:
            self.accept_kw("ALL")
        items = [self.parse_item()]
        while self.accept(","):
            items.append(self.parse_item())
        from_ = None
        if self.at_kw("FROM"):
            self.next()
            from_ = self.parse_from()
        where = None
        if self.at_kw("WHERE"):
            self.next()
            where = self.parse_expr()
        group = None
        if self.at_kw("GROUP"):
            self.next()
            self.expect_kw("BY")
            group = [self.parse_expr()]
            while self.accept(","):
                group.append(self.parse_expr())
        having = None
        if self.at_kw("HAVING"):
            self.next()
            having = self.parse_expr()
        return Select(st.line, st.col, distinct=distinct, items=items,
                      from_=from_, where=where, group=group, having=having)

    def parse_item(self):
        t = self.tok()
        if self.accept("*"):
            return Item(t.line, t.col, expr=Star(t.line, t.col), alias=None)
        if t.kind == "ident" and self.tok(1).val == "." \
                and self.tok(2).val == "*":
            self.next()
            self.next()
            self.next()
            return Item(t.line, t.col, expr=QStar(t.line, t.col, table=t.val),
                        alias=None)
        e = self.parse_expr()
        alias = None
        if self.accept_kw("AS"):
            alias = self.expect_ident().val
        elif self.tok().kind == "ident" and self.tok().val.upper() not in RESV:
            alias = self.next().val
        return Item(e.line, e.col, expr=e, alias=alias)

    def parse_from(self):
        left = self.parse_factor()
        while True:
            kind = None
            if self.at_op(","):
                self.next()
                right = self.parse_factor()
                left = Join(left.line, left.col, kind="inner", left=left,
                            right=right, on=None, using=None)
                continue
            if self.at_kw("NATURAL"):
                t = self.tok()
                raise ParseError("NATURAL JOIN is not supported",
                                 t.line, t.col)
            if self.at_kw("INNER"):
                self.next()
                self.expect_kw("JOIN")
                kind = "inner"
            elif self.at_kw("LEFT"):
                self.next()
                self.accept_kw("OUTER")
                self.expect_kw("JOIN")
                kind = "left"
            elif self.at_kw("RIGHT"):
                self.next()
                self.accept_kw("OUTER")
                self.expect_kw("JOIN")
                kind = "right"
            elif self.at_kw("FULL"):
                self.next()
                self.accept_kw("OUTER")
                self.expect_kw("JOIN")
                kind = "full"
            elif self.at_kw("CROSS"):
                self.next()
                self.expect_kw("JOIN")
                kind = "cross"
            elif self.at_kw("JOIN"):
                self.next()
                kind = "inner"
            else:
                break
            right = self.parse_factor()
            on = using = None
            if self.accept_kw("ON"):
                on = self.parse_expr()
            elif self.accept_kw("USING"):
                self.expect("(")
                using = [self.expect_ident().val]
                while self.accept(","):
                    using.append(self.expect_ident().val)
                self.expect(")")
            left = Join(left.line, left.col, kind=kind, left=left,
                        right=right, on=on, using=using)
        return left

    def parse_factor(self):
        t = self.tok()
        if self.accept("("):
            if self.at_kw("SELECT") or self.at_kw("WITH"):
                q = self.parse_query()
                self.expect(")")
                alias = self.parse_opt_alias()
                return Subq(t.line, t.col, query=q, alias=alias)
            inner = self.parse_from()
            self.expect(")")
            return inner
        nt = self.expect_ident()
        alias = self.parse_opt_alias()
        return Table(nt.line, nt.col, name=nt.val, alias=alias)

    def parse_opt_alias(self):
        if self.accept_kw("AS"):
            return self.expect_ident().val
        t = self.tok()
        if t.kind == "ident" and t.val.upper() not in RESV:
            return self.next().val
        return None

    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        e = self.parse_and()
        while self.at_kw("OR"):
            t = self.next()
            e = Bin(t.line, t.col, op="OR", l=e, r=self.parse_and())
        return e

    def parse_and(self):
        e = self.parse_not()
        while self.at_kw("AND"):
            t = self.next()
            e = Bin(t.line, t.col, op="AND", l=e, r=self.parse_not())
        return e

    def parse_not(self):
        if self.at_kw("NOT"):
            t = self.next()
            return Un(t.line, t.col, op="NOT", e=self.parse_not())
        return self.parse_cmp()

    def parse_cmp(self):
        e = self.parse_add()
        t = self.tok()
        neg = False
        if self.at_kw("NOT") and self.tok(1).kind == "ident" \
                and self.tok(1).val.upper() in ("IN", "LIKE", "BETWEEN"):
            neg = True
            self.next()
            t = self.tok()
        if self.at_kw("IN"):
            self.next()
            self.expect("(")
            if self.at_kw("SELECT") or self.at_kw("WITH"):
                q = self.parse_query()
                self.expect(")")
                return InOp(t.line, t.col, e=e, items=None, query=q,
                            negated=neg)
            items = [self.parse_expr()]
            while self.accept(","):
                items.append(self.parse_expr())
            self.expect(")")
            return InOp(t.line, t.col, e=e, items=items, query=None,
                        negated=neg)
        if self.at_kw("LIKE"):
            self.next()
            return Bin(t.line, t.col, op="LIKE", l=e, r=self.parse_add())
        if self.at_kw("BETWEEN"):
            self.next()
            lo = self.parse_add()
            self.expect_kw("AND")
            hi = self.parse_add()
            return Between(t.line, t.col, e=e, lo=lo, hi=hi, negated=neg)
        if self.at_kw("IS"):
            self.next()
            neg2 = bool(self.accept_kw("NOT"))
            if self.at_kw("NULL"):
                self.next()
                return IsNull(t.line, t.col, e=e, negated=neg2)
            r = self.parse_add()
            return Bin(t.line, t.col, op="IS NOT" if neg2 else "IS",
                       l=e, r=r)
        if t.kind == "op" and t.val in ("=", "<", ">", "<=", ">=", "<>", "!="):
            self.next()
            return Bin(t.line, t.col, op=t.val, l=e, r=self.parse_add())
        return e

    def parse_add(self):
        e = self.parse_mul()
        while self.at_op("+") or self.at_op("-") or self.at_op("||"):
            t = self.next()
            e = Bin(t.line, t.col, op=t.val, l=e, r=self.parse_mul())
        return e

    def parse_mul(self):
        e = self.parse_unary()
        while self.at_op("*") or self.at_op("/") or self.at_op("%"):
            t = self.next()
            e = Bin(t.line, t.col, op=t.val, l=e, r=self.parse_unary())
        return e

    def parse_unary(self):
        if self.at_op("-") or self.at_op("+"):
            t = self.next()
            return Un(t.line, t.col, op=t.val, e=self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        t = self.tok()
        if t.kind == "number":
            self.next()
            return Lit(t.line, t.col, value=t.val)
        if t.kind == "string":
            self.next()
            return Lit(t.line, t.col, value=t.val)
        if self.at_kw("NULL"):
            self.next()
            return Lit(t.line, t.col, value="NULL")
        if self.at_kw("TRUE") or self.at_kw("FALSE"):
            self.next()
            return Lit(t.line, t.col, value=t.val.upper())
        if self.at_kw("CASE"):
            self.next()
            operand = None
            if not self.at_kw("WHEN"):
                operand = self.parse_expr()
            whens = []
            while self.accept_kw("WHEN"):
                c = self.parse_expr()
                self.expect_kw("THEN")
                r = self.parse_expr()
                whens.append((c, r))
            else_ = None
            if self.accept_kw("ELSE"):
                else_ = self.parse_expr()
            self.expect_kw("END")
            return Case(t.line, t.col, operand=operand, whens=whens,
                        else_=else_)
        if self.at_kw("EXISTS"):
            self.next()
            self.expect("(")
            q = self.parse_query()
            self.expect(")")
            return Exists(t.line, t.col, query=q)
        if self.at_kw("CAST"):
            self.next()
            self.expect("(")
            e = self.parse_expr()
            self.expect_kw("AS")
            tn = self.expect_ident().val
            self.expect(")")
            return Cast(t.line, t.col, e=e, type_name=tn)
        if self.accept("("):
            if self.at_kw("SELECT") or self.at_kw("WITH"):
                q = self.parse_query()
                self.expect(")")
                return ScalarSubq(t.line, t.col, query=q)
            e = self.parse_expr()
            self.expect(")")
            return e
        if t.kind == "ident":
            self.next()
            parts = [t.val]
            while self.at_op("."):
                self.next()
                parts.append(self.expect_ident().val)
            if self.at_op("("):
                return self.parse_func(t, parts)
            if len(parts) == 1:
                return Col(t.line, t.col, table=None, name=parts[0],
                           text=parts[0])
            return Col(t.line, t.col, table=parts[-2], name=parts[-1],
                       text=".".join(parts[-2:]))
        raise ParseError("unexpected token %r" % t.val, t.line, t.col)

    def parse_func(self, t, parts):
        name = parts[-1]
        self.expect("(")
        args = []
        distinct = False
        if self.at_op(")"):
            self.next()
        else:
            if self.at_kw("DISTINCT"):
                self.next()
                distinct = True
            if self.at_op("*"):
                st = self.next()
                args.append(Star(st.line, st.col))
            else:
                args.append(self.parse_expr())
                while self.accept(","):
                    args.append(self.parse_expr())
            self.expect(")")
        over = None
        if self.at_kw("OVER"):
            self.next()
            self.expect("(")
            part = None
            order = None
            if self.at_kw("PARTITION"):
                self.next()
                self.expect_kw("BY")
                part = [self.parse_expr()]
                while self.accept(","):
                    part.append(self.parse_expr())
            if self.at_kw("ORDER"):
                self.next()
                self.expect_kw("BY")
                order = [self.parse_order_item()]
                while self.accept(","):
                    order.append(self.parse_order_item())
            while not self.at_op(")"):
                if self.tok().kind == "eof":
                    raise ParseError("unterminated OVER clause",
                                     self.tok().line, self.tok().col)
                self.next()
            self.expect(")")
            over = Over(t.line, t.col, partition=part, order=order)
        return Func(t.line, t.col, name=name, args=args, distinct=distinct,
                    over=over)
