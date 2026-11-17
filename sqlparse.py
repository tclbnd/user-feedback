"""Parser and printer for the SELECT subset shared by both dialects."""
import re

TOKEN = re.compile(r"\s*(?:'((?:[^']|'')*)'|(-?\d+)|([A-Za-z_][A-Za-z_0-9]*)|(\S))")


def tokenize(sql):
    out = []
    for s, n, w, p in TOKEN.findall(sql.strip().rstrip(";")):
        out.append(("str", s.replace("''", "'")) if s or (not n and not w and not p) else
                   ("num", int(n)) if n else ("word", w) if w else ("punct", p))
    return out


class Parser:
    def __init__(self, sql):
        self.toks, self.i = tokenize(sql), 0

    def peek(self, word=None):
        tok = self.toks[self.i] if self.i < len(self.toks) else (None, None)
        return tok if word is None else (tok[0] == "word" and tok[1].upper() == word)

    def take(self, word=None):
        tok = self.peek()
        if word and not self.peek(word) and tok[1] != word:
            raise ValueError(f"expected {word}, got {tok[1]!r}")
        self.i += 1
        return tok

    def many(self, item, sep=","):
        out = [item()]
        while self.peek()[1] == sep or self.peek(sep):
            self.take()
            out.append(item())
        return out

    def expr(self):
        kind, value = self.take()
        if kind in ("str", "num"):
            return ("lit", value)
        if self.peek()[1] != "(":
            return ("col", value)
        self.take("(")
        args = self.many(self.expr)
        self.take(")")
        return ("fn", value.upper(), args)

    def item(self):
        e = self.expr()
        if self.peek("AS"):
            self.take()
            return e, self.take()[1]
        return e, e[1] if e[0] == "col" else "expr"

    def condition(self):
        left = self.expr()
        self.take("=")
        return left, self.expr()

    def key(self):
        e, desc, nulls = self.expr(), False, None
        if self.peek("ASC") or self.peek("DESC"):
            desc = self.take()[1].upper() == "DESC"
        if self.peek("NULLS"):
            self.take()
            nulls = self.take()[1].upper()
        return e, desc, nulls

    def parse(self):
        q = {"where": [], "order": [], "limit": None, "offset": 0}
        self.take("SELECT")
        q["items"] = self.many(self.item)
        self.take("FROM")
        q["table"] = self.take()[1]
        if self.peek("WHERE"):
            self.take()
            q["where"] = self.many(self.condition, "AND")
        if self.peek("ORDER"):
            self.take(), self.take("BY")
            q["order"] = self.many(self.key)
        if self.peek("LIMIT"):
            self.take()
            first = self.take()[1]
            if self.peek()[1] == ",":
                # legacy `LIMIT offset, count`
                self.take(",")
                q["limit"] = self.take()[1]
                q["offset"] = first
            else:
                q["limit"] = first
                if self.peek("OFFSET"):
                    self.take()
                    q["offset"] = self.take()[1]
        return q


def show(e):
    if e[0] == "lit":
        return str(e[1]) if isinstance(e[1], int) else "'" + e[1].replace("'", "''") + "'"
    return e[1] if e[0] == "col" else f"{e[1]}({', '.join(show(a) for a in e[2])})"


def render(q):
    sql = "SELECT " + ", ".join(show(e) + ("" if e == ("col", a) else f" AS {a}") for e, a in q["items"])
    sql += f" FROM {q['table']}"
    if q["where"]:
        sql += " WHERE " + " AND ".join(f"{show(a)} = {show(b)}" for a, b in q["where"])
    if q["order"]:
        sql += " ORDER BY " + ", ".join(show(e) + (" DESC" if d else " ASC") + (f" NULLS {n}" if n else "")
                                        for e, d, n in q["order"])
    if q["limit"] is not None:
        sql += f" LIMIT {q['limit']} OFFSET {q['offset']}"
    return sql
