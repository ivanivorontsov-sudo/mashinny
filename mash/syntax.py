import re
from .ast import *

KEYWORDS = {"fn", "let", "if", "then", "else", "pre", "post", "dec", "true", "false", "unit"}
TYPES = {"Int": INT, "Bool": BOOL, "Str": STR, "Unit": UNIT, "Ui": Ty("Ui")}
EFFECTS = ("io",)

TOKEN_RE = re.compile(r'''
 (?P<ws>[ \t\r\n]+)
|(?P<int>[0-9]+)
|(?P<str>"(?:[^"\\\n]|\\["\\nt])*")
|(?P<id>[A-Za-z_][A-Za-z0-9_]*)
|(?P<op>->|==|!=|<=|>=|&&|\|\||\+\+|[-+*/%<>!=(),;:\[\]|])
''', re.X)


class SyntaxErr(Exception):
    def __init__(self, code, msg, pos, expected=None, got=None):
        super().__init__(msg)
        self.code, self.msg, self.pos, self.expected, self.got = code, msg, pos, expected, got

    def to_json(self):
        d = {"code": self.code, "message": self.msg, "at": {"line": self.pos[0], "col": self.pos[1]}}
        if self.expected is not None:
            d["expected"] = self.expected
        if self.got is not None:
            d["got"] = self.got
        return d


def lex(src):
    out, i, line, col = [], 0, 1, 1
    while i < len(src):
        m = TOKEN_RE.match(src, i)
        if not m:
            raise SyntaxErr("E-LEX", "unexpected character", (line, col), got=src[i])
        kind, text = m.lastgroup, m.group()
        if kind != "ws":
            if kind == "id" and text in KEYWORDS:
                kind = "kw"
            out.append((kind, text, (line, col)))
        nl = text.count("\n")
        if nl:
            line += nl
            col = len(text) - text.rfind("\n")
        else:
            col += len(text)
        i = m.end()
    out.append(("eof", "", (line, col)))
    return out


BINPREC = {"||": 1, "&&": 2, "==": 3, "!=": 3, "<": 3, "<=": 3, ">": 3, ">=": 3, "++": 4, "+": 5, "-": 5, "*": 6, "/": 6, "%": 6}


def unesc(s):
    return re.sub(r'\\(["\\nt])', lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s[1:-1])


def esc(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


class Parser:
    def __init__(self, src):
        self.t = lex(src)
        self.i = 0

    def peek(self, k=0):
        return self.t[self.i + k]

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, text):
        tok = self.next()
        if tok[1] != text or tok[0] in ("str",):
            raise SyntaxErr("E-PARSE", "expected " + repr(text), tok[2], expected=text, got=tok[1] or "<eof>")
        return tok

    def ident(self):
        tok = self.next()
        if tok[0] != "id":
            raise SyntaxErr("E-PARSE", "expected identifier", tok[2], expected="identifier", got=tok[1] or "<eof>")
        return tok

    def program(self):
        funcs = []
        while self.peek()[0] != "eof":
            funcs.append(self.func())
        return Program(funcs)

    def ty(self):
        tok = self.next()
        if tok[1] == "[":
            t = self.ty()
            self.expect("]")
            return lst(t)
        if tok[0] == "id" and tok[1] in TYPES:
            return TYPES[tok[1]]
        raise SyntaxErr("E-PARSE", "expected type", tok[2], expected="Int|Bool|Str|Unit|Ui|[T]", got=tok[1] or "<eof>")

    def func(self):
        pos = self.expect("fn")[2]
        name = self.ident()[1]
        self.expect("(")
        params = []
        if self.peek()[1] != ")":
            while True:
                pn = self.ident()[1]
                self.expect(":")
                params.append((pn, self.ty()))
                if self.peek()[1] != ",":
                    break
                self.next()
        self.expect(")")
        self.expect("->")
        ret = self.ty()
        effects = []
        while self.peek()[1] == "!":
            self.next()
            e = self.ident()
            if e[1] not in EFFECTS:
                raise SyntaxErr("E-PARSE", "unknown effect", e[2], expected="|".join(EFFECTS), got=e[1])
            effects.append(e[1])
        if effects != sorted(set(effects)):
            raise SyntaxErr("E-CANON", "effects must be sorted and unique", pos)
        pre = post = dec = None
        if self.peek()[1] == "pre":
            self.next(); pre = self.expr()
        if self.peek()[1] == "post":
            self.next(); post = self.expr()
        if self.peek()[1] == "dec":
            self.next(); dec = self.expr()
        self.expect("=")
        body = self.expr()
        return Func(name, params, ret, tuple(effects), pre, post, dec, body, pos)

    def expr(self):
        tok = self.peek()
        if tok[1] == "let" and tok[0] == "kw":
            self.next()
            n = self.ident()[1]
            self.expect("=")
            v = self.expr()
            self.expect(";")
            return Let(tok[2], n, v, self.expr())
        if tok[1] == "if" and tok[0] == "kw":
            self.next()
            c = self.expr(); self.expect("then")
            t = self.expr(); self.expect("else")
            return If(tok[2], c, t, self.expr())
        if tok[1] == "|":
            self.next()
            ps = []
            while True:
                pn = self.ident()[1]
                self.expect(":")
                ps.append((pn, self.ty()))
                if self.peek()[1] != ",":
                    break
                self.next()
            self.expect("|")
            return Lam(tok[2], ps, self.expr())
        return self.binop(1)

    def binop(self, minp):
        a = self.unary()
        while True:
            tok = self.peek()
            p = BINPREC.get(tok[1]) if tok[0] == "op" else None
            if p is None or p < minp:
                return a
            self.next()
            a = Bin(tok[2], tok[1], a, self.binop(p + 1))

    def unary(self):
        tok = self.peek()
        if tok[0] == "op" and tok[1] in ("-", "!"):
            self.next()
            if tok[1] == "-" and self.peek()[0] == "int" and self.peek()[2] == (tok[2][0], tok[2][1] + 1):
                return Lit(tok[2], -int(self.next()[1]))
            return Un(tok[2], tok[1], self.unary())
        return self.atom()

    def atom(self):
        tok = self.next()
        k, s, pos = tok
        if k == "int":
            if len(s) > 1 and s[0] == "0":
                raise SyntaxErr("E-CANON", "leading zeros are not canonical", pos, got=s)
            return Lit(pos, int(s))
        if k == "str":
            return Lit(pos, unesc(s))
        if k == "kw" and s in ("true", "false"):
            return Lit(pos, s == "true")
        if k == "kw" and s == "unit":
            return Lit(pos, None)
        if s == "(" and k == "op":
            e = self.expr()
            self.expect(")")
            return e
        if s == "[" and k == "op":
            items = []
            if self.peek()[1] != "]":
                while True:
                    items.append(self.expr())
                    if self.peek()[1] != ",":
                        break
                    self.next()
            self.expect("]")
            return ListE(pos, items)
        if k == "id":
            if self.peek()[1] == "(" and self.peek()[0] == "op":
                self.next()
                args = []
                if self.peek()[1] != ")":
                    while True:
                        args.append(self.expr())
                        if self.peek()[1] != ",":
                            break
                        self.next()
                self.expect(")")
                return Call(pos, s, args)
            return Var(pos, s)
        raise SyntaxErr("E-PARSE", "expected expression", pos, expected="expression", got=s or "<eof>")


def parse(src):
    return Parser(src).program()


def parse_expr(src):
    p = Parser(src)
    e = p.expr()
    if p.peek()[0] != "eof":
        raise SyntaxErr("E-PARSE", "trailing input", p.peek()[2])
    return e


def canon(prog):
    return Program(sorted(prog.funcs, key=lambda f: f.name))


def fmt_ty(t):
    return str(t)


def fmt_e(e, ctx=0, ind=""):
    if isinstance(e, Lit):
        v = e.val
        if v is None:
            return "unit"
        if v is True:
            return "true"
        if v is False:
            return "false"
        if isinstance(v, str):
            return esc(v)
        if v < 0 and ctx >= 5:
            return "(" + str(v) + ")"
        return str(v)
    if isinstance(e, Var):
        return e.name
    if isinstance(e, Call):
        return e.fn + "(" + ", ".join(fmt_e(a, 0, ind) for a in e.args) + ")"
    if isinstance(e, ListE):
        return "[" + ", ".join(fmt_e(a, 0, ind) for a in e.items) + "]"
    if isinstance(e, Un):
        inner = fmt_e(e.a, 7, ind)
        if e.op == "-" and isinstance(e.a, Lit) and isinstance(e.a.val, int) and not isinstance(e.a.val, bool) and e.a.val >= 0:
            inner = "(" + inner + ")"
        if e.op == "-" and inner.startswith("-"):
            inner = "(" + inner + ")"
        return e.op + inner
    if isinstance(e, Bin):
        p = BINPREC[e.op]
        s = fmt_e(e.a, p, ind) + " " + e.op + " " + fmt_e(e.b, p + 1, ind)
        return "(" + s + ")" if p < ctx else s
    if isinstance(e, (If, Let, Lam)):
        if isinstance(e, If):
            s = "if " + fmt_e(e.c, 0, ind) + " then " + fmt_e(e.t, 0, ind) + " else " + fmt_e(e.e, 0, ind)
        elif isinstance(e, Let):
            s = "let " + e.name + " = " + fmt_e(e.val, 0, ind) + ";\n" + ind + fmt_e(e.body, 0, ind)
        else:
            s = "|" + ", ".join(n + ": " + fmt_ty(t) for n, t in e.params) + "| " + fmt_e(e.body, 0, ind)
        return "(" + s + ")" if ctx > 0 else s
    raise TypeError(e)


def fmt_func(f):
    head = "fn " + f.name + "(" + ", ".join(n + ": " + fmt_ty(t) for n, t in f.params) + ") -> " + fmt_ty(f.ret)
    head += "".join(" !" + x for x in f.effects)
    lines = [head]
    if f.pre is not None:
        lines.append("  pre " + fmt_e(f.pre, 0, "    "))
    if f.post is not None:
        lines.append("  post " + fmt_e(f.post, 0, "    "))
    if f.dec is not None:
        lines.append("  dec " + fmt_e(f.dec, 0, "    "))
    lines.append("  = " + fmt_e(f.body, 0, "    "))
    return "\n".join(lines) + "\n"


def fmt(prog):
    return "\n".join(fmt_func(f) for f in canon(prog).funcs)


WORDY = ("id", "kw", "int")


def compact(prog):
    out = []
    for f in canon(prog).funcs:
        toks = lex(fmt_func(f))[:-1]
        s = ""
        prev = None
        for k, t, _ in toks:
            if prev is not None:
                pk, pt = prev
                need = pk == "kw" or k == "kw" or (pk in WORDY and k in WORDY)
                if not need:
                    probe = lex(pt + t)
                    need = [(a, b) for a, b, _ in probe[:-1]] != [(pk, pt), (k, t)]
                s += " " if need else ""
            s += t
            prev = (k, t)
        out.append(s)
    return "\n".join(out) + "\n"
