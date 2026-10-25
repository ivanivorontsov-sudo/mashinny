import os
import shutil
import subprocess
import tempfile
from .ast import *

RT = os.path.join(os.path.dirname(__file__), "runtime", "rt.h")
BIN = {"+": "iadd", "-": "isub", "*": "bmul", "/": "idiv", "%": "imod"}
CMP = {"<": "<", "<=": "<=", ">": ">", ">=": ">="}
SIMPLE = {"print": "p_print", "get": "p_get", "push": "p_push", "slice": "p_slice", "rev": "p_rev", "range": "p_range",
          "sum": "p_sum", "sort": "p_sort", "join": "p_join", "chars": "p_chars", "str": "istr", "abs": "iabs", "wrap64": "iwrap64"}
UI = ("rect", "text", "button", "col", "row", "stack", "at", "clip")


def cstr(s):
    out = []
    for b in s.encode("utf-8"):
        out.append("\\%03o" % b if b < 32 or b > 126 or b in (34, 92, 63) else chr(b))
    return '"' + "".join(out) + '"'


class Gen:
    def __init__(self, prog):
        self.prog = prog
        self.k = 0
        self.lits = []

    def tmp(self):
        self.k += 1
        return "t%d" % self.k

    def lit(self, init):
        self.lits.append(init)
        return "L%d" % (len(self.lits) - 1)

    def seq(self, parts, final):
        return "({ tick(1); " + " ".join(parts) + " " + final + "; })"

    def ex(self, e, env):
        t = type(e)
        if t is Lit:
            v = e.val
            if v is None:
                return "({ tick(1); unitv(); })"
            if isinstance(v, bool):
                return "({ tick(1); boolv(%d); })" % v
            if isinstance(v, str):
                return "({ tick(1); %s; })" % self.lit("strutf8(%s)" % cstr(v))
            return "({ tick(1); %s; })" % self.lit("iparse(\"%d\")" % v)
        if t is Var:
            return "({ tick(1); %s; })" % env[e.name]
        if t is Un:
            a = self.ex(e.a, env)
            return self.seq([], "chkbig(ineg(%s))" % a if e.op == "-" else "boolv(!(%s)->n)" % a)
        if t is Bin:
            op = e.op
            a, b = self.ex(e.a, env), self.ex(e.b, env)
            if op == "&&":
                x = self.tmp()
                return self.seq(["V *%s = %s;" % (x, a)], "(%s->n ? %s : %s)" % (x, b, x))
            if op == "||":
                x = self.tmp()
                return self.seq(["V *%s = %s;" % (x, a)], "(%s->n ? %s : %s)" % (x, x, b))
            x, y = self.tmp(), self.tmp()
            pre = ["V *%s = %s;" % (x, a), "V *%s = %s;" % (y, b)]
            if op in BIN:
                fin = "%s(%s, %s)" % (BIN[op], x, y)
                if op in "+-":
                    fin = "chkbig(%s)" % fin
            elif op in CMP:
                fin = "boolv(icmp(%s, %s) %s 0)" % (x, y, CMP[op])
            elif op == "==":
                fin = "boolv(veq(%s, %s))" % (x, y)
            elif op == "!=":
                fin = "boolv(!veq(%s, %s))" % (x, y)
            else:
                fin = "concat(%s, %s)" % (x, y)
            return self.seq(pre, fin)
        if t is If:
            return self.seq([], "(%s->n ? %s : %s)" % (self.ex(e.c, env), self.ex(e.t, env), self.ex(e.e, env)))
        if t is Let:
            x = self.tmp()
            e2 = dict(env); e2[e.name] = x
            return self.seq(["V *%s = %s;" % (x, self.ex(e.val, env))], self.ex(e.body, e2))
        if t is ListE:
            xs = [self.tmp() for _ in e.items]
            arr = self.tmp()
            pre = ["V *%s = %s;" % (x, self.ex(i, env)) for x, i in zip(xs, e.items)]
            pre.append("V **%s = xm(%d * sizeof(V *));" % (arr, max(1, len(xs))))
            pre += ["%s[%d] = %s;" % (arr, i, x) for i, x in enumerate(xs)]
            return self.seq(pre, "listv(%s, %d)" % (arr, len(xs)))
        if t is Call:
            return self.call(e, env)
        raise TypeError(e)

    def call(self, e, env):
        n = e.fn
        if n in ("map", "filter", "fold"):
            lam = e.args[-1]
            l, i, r, cap = self.tmp(), self.tmp(), self.tmp(), self.tmp()
            pre = ["V *%s = %s;" % (l, self.ex(e.args[0], env))]
            if n == "fold":
                acc = self.tmp()
                pre.append("V *%s = %s;" % (acc, self.ex(e.args[1], env)))
                e2 = dict(env); e2[lam.params[0][0]] = acc; e2[lam.params[1][0]] = "items(%s)[%s]" % (l, i)
                pre.append("for (int64_t %s = 0; %s < %s->n; %s++) %s = %s;" % (i, i, l, i, acc, self.ex(lam.body, e2)))
                return self.seq(pre, acc)
            e2 = dict(env); e2[lam.params[0][0]] = "items(%s)[%s]" % (l, i)
            pre.append("V *%s = listv(0, 0); int64_t %s = 0;" % (r, cap))
            body = self.ex(lam.body, e2)
            if n == "map":
                pre.append("for (int64_t %s = 0; %s < %s->n; %s++) grow(%s, &%s, %s);" % (i, i, l, i, r, cap, body))
            else:
                pre.append("for (int64_t %s = 0; %s < %s->n; %s++) if ((%s)->n) grow(%s, &%s, items(%s)[%s]);" % (i, i, l, i, body, r, cap, l, i))
            return self.seq(pre, r)
        xs = [self.tmp() for _ in e.args]
        pre = ["V *%s = %s;" % (x, self.ex(a, env)) for x, a in zip(xs, e.args)]
        if n in self.fn:
            return self.seq(pre, "f_%s(%s)" % (n, ", ".join(xs)))
        if n in SIMPLE:
            return self.seq(pre, "%s(%s)" % (SIMPLE[n], ", ".join(xs)))
        if n == "len":
            return self.seq(pre, "intv(%s->n)" % xs[0])
        if n in ("min", "max"):
            return self.seq(pre, "(icmp(%s, %s) %s 0 ? %s : %s)" % (xs[0], xs[1], "<=" if n == "min" else ">=", xs[0], xs[1]))
        if n in UI:
            arr = self.tmp()
            pre.append("V *%s[%d] = {%s};" % (arr, len(xs), ", ".join(xs)))
            return self.seq(pre, "mkui(\"%s\", %d, %s)" % (n, len(xs), arr))
        raise ValueError("aot: unsupported builtin " + n)

    def func(self, f):
        env = {n: "a_" + n for n, _ in f.params}
        sig = "static V *f_%s(%s)" % (f.name, ", ".join("V *a_" + n for n, _ in f.params) or "void")
        body = ["enter();"]
        if f.pre is not None:
            body.append("if (!(%s)->n) trap(\"T-PRE\");" % self.ex(f.pre, env))
        if f.dec is not None:
            body.append("if ((%s)->sign < 0) trap(\"T-DEC\");" % self.ex(f.dec, env))
        body.append("V *res = %s;" % self.ex(f.body, env))
        if f.post is not None:
            e2 = dict(env); e2["result"] = "res"
            body.append("if (!(%s)->n) trap(\"T-POST\");" % self.ex(f.post, e2))
        body.append("g_depth--; return res;")
        return sig, sig + " {\n    " + "\n    ".join(body) + "\n}\n"

    def run(self, entry):
        self.fn = {f.name: f for f in self.prog.funcs}
        ent = self.fn[entry]
        if any(t != INT for _, t in ent.params):
            raise ValueError("aot: entry parameters must be Int")
        parts = [self.func(f) for f in self.prog.funcs]
        out = [open(RT).read()]
        out += ["static V *L%d;" % i for i in range(len(self.lits))]
        out += [s + ";" for s, _ in parts]
        out += [b for _, b in parts]
        init = "\n    ".join("L%d = %s;" % (i, x) for i, x in enumerate(self.lits))
        out.append('''int main(int argc, char **argv) {
    g_live = 1;
    V *av[%d];
    int na = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--json")) { g_json = 1; g_live = 0; }
        else if (!strcmp(argv[i], "--fuel") && i + 1 < argc) g_fuel = atoll(argv[++i]);
        else if (na < %d) av[na++] = iparse(argv[i]);
    }
    if (na != %d) { fputs("expected %d integer arguments\\n", stderr); return 64; }
    %s
    if (setjmp(g_jb)) { finish(0, 0); return 2; }
    V *r = f_%s(%s);
    finish(1, r);
    return 0;
}
''' % (max(1, len(ent.params)), len(ent.params), len(ent.params), len(ent.params), init, entry, ", ".join("av[%d]" % i for i in range(len(ent.params)))))
        return "\n".join(out)


def to_c(prog, entry="main"):
    return Gen(prog).run(entry)


def build(prog, out, entry="main", cc=None, target=None):
    src = to_c(prog, entry)
    cc = cc or os.environ.get("CC") or ("x86_64-w64-mingw32-gcc" if target == "windows" else "gcc")
    if not shutil.which(cc):
        raise RuntimeError("C compiler not found: " + cc)
    with tempfile.TemporaryDirectory() as d:
        c = os.path.join(d, "prog.c")
        open(c, "w").write(src)
        r = subprocess.run([cc, "-O2", "-w", "-o", out, c], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr[-4000:])
    return out
