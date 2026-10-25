from .ast import *
from .syntax import parse, parse_expr, fmt, SyntaxErr
from .types import check, BUILTINS, suggest


def candidates(f):
    out = []
    for n, t in f.params:
        if t == INT:
            out += [n, "max(%s, 0)" % n, "abs(%s)" % n]
        elif t == STR or t.kind == "List":
            out.append("len(%s)" % n)
    return out


def find_measure(prog, fname, timeout_ms=3000):
    from .prove import prove
    f0 = next(f for f in prog.funcs if f.name == fname)
    tries = [(None, c) for c in candidates(f0)]
    tries += [("%s >= 0" % n, n) for n, t in f0.params if t == INT]
    for extra, c in tries:
        pre = f0.pre
        if extra is not None:
            pre = parse_expr(extra) if pre is None else Bin(op="&&", a=pre, b=parse_expr(extra))
        fs = [Func(f.name, f.params, f.ret, f.effects, pre, f.post, parse_expr(c), f.body, f.pos) if f.name == fname else f for f in prog.funcs]
        r = prove(Program(fs), timeout_ms)
        if all(o["status"] == "proved" for o in r["obligations"] if o["fn"] == fname and (o["kind"].startswith("dec") or o["kind"] == "callee_pre " + fname)):
            return c, pre, extra
    return None


def rename(e, old, new, bound=()):
    t = type(e)
    if t is Var and e.name == old and old not in bound:
        e.name = new
    elif t is Call:
        if e.fn == old and old not in bound:
            e.fn = new
        for a in e.args:
            rename(a, old, new, bound)
    elif t is Bin:
        rename(e.a, old, new, bound); rename(e.b, old, new, bound)
    elif t is Un:
        rename(e.a, old, new, bound)
    elif t is If:
        rename(e.c, old, new, bound); rename(e.t, old, new, bound); rename(e.e, old, new, bound)
    elif t is Let:
        rename(e.val, old, new, bound); rename(e.body, old, new, bound + (e.name,))
    elif t is ListE:
        for a in e.items:
            rename(a, old, new, bound)
    elif t is Lam:
        rename(e.body, old, new, bound + tuple(n for n, _ in e.params))


def autofix(prog, rounds=20):
    applied = []
    for _ in range(rounds):
        errs = check(prog)
        if not errs:
            break
        changed = False
        for e in errs:
            fn = e.get("path", "").split("/")[0][3:]
            f = next((x for x in prog.funcs if x.name == fn), None)
            if f is None:
                continue
            if e["code"] == "E-NODEC":
                m = find_measure(prog, fn)
                if m:
                    f.dec = parse_expr(m[0])
                    f.pre = m[1]
                    applied.append({"fn": fn, "code": e["code"], "action": "add dec " + m[0] + ("" if m[2] is None else " and pre " + m[2])})
                    changed = True
            elif e["code"] == "E-DECNOTREC":
                f.dec = None
                applied.append({"fn": fn, "code": e["code"], "action": "remove dec"})
                changed = True
            elif e["code"] == "E-UNBOUND" and e.get("fix", "").startswith("replace with `"):
                new = e["fix"][len("replace with `"):-1]
                for part in (f.pre, f.post, f.dec, f.body):
                    if part is not None:
                        rename(part, e["got"], new)
                applied.append({"fn": fn, "code": e["code"], "action": "rename %s -> %s" % (e["got"], new)})
                changed = True
            if changed:
                break
        if not changed:
            break
    return prog, applied, check(prog)
