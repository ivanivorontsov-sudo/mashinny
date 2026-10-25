import z3
from .ast import *

class Op:
    def __init__(self, name):
        self.name = name


OPAQUE = Op("?")


class Prover:
    def __init__(self, prog, timeout_ms=5000):
        self.funcs = {f.name: f for f in prog.funcs}
        self.timeout = timeout_ms
        self.k = 0

    def fresh(self, t, hint="v"):
        self.k += 1
        if t == INT:
            return z3.Int("%s!%d" % (hint, self.k))
        if t == BOOL:
            return z3.Bool("%s!%d" % (hint, self.k))
        return OPAQUE

    def tr(self, e, env, pc, obl, path, cur, collect):
        t = type(e)
        if t is Lit:
            v = e.val
            if isinstance(v, bool):
                return z3.BoolVal(v)
            if isinstance(v, int):
                return z3.IntVal(v)
            return OPAQUE
        if t is Var:
            return env[e.name]
        if t is Un:
            a = self.tr(e.a, env, pc, obl, path, cur, collect)
            return -a if e.op == "-" else z3.Not(a)
        if t is Bin:
            op = e.op
            a = self.tr(e.a, env, pc, obl, path + "/left", cur, collect)
            if op == "&&":
                b = self.tr(e.b, env, pc + [a], obl, path + "/right", cur, collect)
                return z3.And(a, b)
            if op == "||":
                b = self.tr(e.b, env, pc + [z3.Not(a)], obl, path + "/right", cur, collect)
                return z3.Or(a, b)
            b = self.tr(e.b, env, pc, obl, path + "/right", cur, collect)
            if op in ("==", "!="):
                if isinstance(a, (Op, tuple)) or isinstance(b, (Op, tuple)):
                    return self.fresh(BOOL, "eq")
                return a == b if op == "==" else a != b
            if op == "++":
                return OPAQUE
            if op in ("/", "%"):
                if collect:
                    obl.append(("nonzero_divisor", path, pc, b != 0))
                q = z3.If(b > 0, a / b, (-a) / (-b))
                return q if op == "/" else a - b * q
            return {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "<": lambda: a < b, "<=": lambda: a <= b,
                    ">": lambda: a > b, ">=": lambda: a >= b}[op]()
        if t is If:
            c = self.tr(e.c, env, pc, obl, path + "/cond", cur, collect)
            x = self.tr(e.t, env, pc + [c], obl, path + "/then", cur, collect)
            y = self.tr(e.e, env, pc + [z3.Not(c)], obl, path + "/else", cur, collect)
            if isinstance(x, (Op, tuple)) or isinstance(y, (Op, tuple)):
                return OPAQUE
            return z3.If(c, x, y)
        if t is Let:
            v = self.tr(e.val, env, pc, obl, path + "/let " + e.name, cur, collect)
            e2 = dict(env); e2[e.name] = v
            return self.tr(e.body, e2, pc, obl, path + "/in", cur, collect)
        if t is ListE:
            for i, x in enumerate(e.items):
                self.tr(x, env, pc, obl, path + "/item" + str(i), cur, collect)
            return ("list", len(e.items))
        if t is Lam:
            e2 = dict(env)
            for n, ty in e.params:
                e2[n] = self.fresh(ty, n)
            self.tr(e.body, e2, pc, obl, path + "/lambda", cur, collect)
            return OPAQUE
        if t is Call:
            args = [self.tr(a, env, pc, obl, path + "/" + e.fn + "/arg" + str(i), cur, collect) for i, a in enumerate(e.args)]
            if e.fn in self.funcs:
                g = self.funcs[e.fn]
                genv = {n: v for (n, _), v in zip(g.params, args)}
                if collect and g.pre is not None:
                    obl.append(("callee_pre " + g.name, path + "/" + e.fn, pc, self.tr(g.pre, genv, [], [], "", g, False)))
                if collect and g.name == cur.name and cur.dec is not None:
                    d_now = self.tr(cur.dec, self.penv, [], [], "", cur, False)
                    d_next = self.tr(cur.dec, genv, [], [], "", cur, False)
                    obl.append(("dec_decreases", path + "/" + e.fn, pc, d_next < d_now))
                r = self.fresh(g.ret, "r_" + g.name)
                if g.post is not None and not isinstance(r, Op):
                    genv["result"] = r
                    post = self.tr(g.post, genv, [], [], "", g, False)
                    self.assume.append(post)
                return r
            n = e.fn
            if n == "abs":
                return z3.If(args[0] >= 0, args[0], -args[0])
            if n == "min":
                return z3.If(args[0] <= args[1], args[0], args[1])
            if n == "max":
                return z3.If(args[0] >= args[1], args[0], args[1])
            if n == "len":
                if isinstance(args[0], tuple) and args[0][0] == "list":
                    return z3.IntVal(args[0][1])
                if isinstance(args[0], Op) and args[0] is not OPAQUE:
                    key = id(args[0])
                    if key not in self.lens:
                        self.lens[key] = z3.Int("len_" + args[0].name)
                        self.base.append(self.lens[key] >= 0)
                    return self.lens[key]
                r = self.fresh(INT, "len")
                self.assume.append(r >= 0)
                return r
            if n == "wrap64":
                r = self.fresh(INT, "w64")
                self.assume.append(z3.And(r >= -(1 << 63), r < (1 << 63)))
                return r
            if n in ("sum", "fold", "get"):
                return self.fresh(self.hint.get(id(e), UNIT), n)
            return OPAQUE
        raise TypeError(e)

    def check(self, goal, pcs, hyps, params):
        s = z3.Solver()
        s.set("timeout", self.timeout)
        for h in hyps + pcs:
            s.add(h)
        s.add(z3.Not(goal))
        r = s.check()
        if r == z3.unsat:
            return {"status": "proved"}
        if r == z3.sat:
            m = s.model()
            return {"status": "failed", "counterexample": {str(d): str(m[d]) for d in sorted(m.decls(), key=str) if str(d) in params}}
        return {"status": "unknown", "reason": s.reason_unknown()}

    def func(self, f, hint):
        self.hint = hint
        out = []
        self.assume = []
        self.lens = {}
        self.base = []
        self.penv = {n: (z3.Int(n) if t == INT else z3.Bool(n) if t == BOOL else Op(n)) for n, t in f.params}
        hyps = self.base
        if f.pre is not None:
            hyps.append(self.tr(f.pre, self.penv, [], [], "", f, False))
        obl = []
        body = self.tr(f.body, self.penv, [], obl, "fn " + f.name + "/body", f, True)
        if f.dec is not None:
            d = self.tr(f.dec, self.penv, [], [], "", f, False)
            obl.append(("dec_nonnegative", "fn " + f.name + "/dec", [], d >= 0))
        if f.post is not None and not isinstance(body, (Op, tuple)):
            env = dict(self.penv); env["result"] = body
            obl.append(("postcondition", "fn " + f.name + "/post", [], self.tr(f.post, env, [], [], "", f, False)))
        elif f.post is not None:
            out.append({"fn": f.name, "kind": "postcondition", "path": "fn " + f.name + "/post", "status": "unknown", "reason": "result type is not modelled (only Int/Bool)"})
        for kind, path, pc, goal in obl:
            if isinstance(goal, Op) or not isinstance(goal, z3.ExprRef):
                out.append({"fn": f.name, "kind": kind, "path": path, "status": "unknown", "reason": "not modelled"})
                continue
            r = self.check(goal, pc, hyps + self.assume, {n for n, _ in f.params})
            r.update(fn=f.name, kind=kind, path=path)
            out.append(r)
        return out


def prove(prog, timeout_ms=5000):
    from .types import Checker
    c = Checker(prog)
    c.run()
    hint = c.types
    p = Prover(prog, timeout_ms)
    res = []
    for f in sorted(prog.funcs, key=lambda f: f.name):
        res.extend(p.func(f, hint))
    summary = {s: sum(1 for r in res if r["status"] == s) for s in ("proved", "failed", "unknown")}
    total = {}
    for f in prog.funcs:
        rs = [r for r in res if r["fn"] == f.name and r["kind"].startswith("dec")]
        if rs:
            total[f.name] = "proved" if all(r["status"] == "proved" for r in rs) else "fuel-bounded"
        else:
            total[f.name] = "non-recursive"
    return {"obligations": res, "summary": summary, "termination": total, "ok": summary["failed"] == 0 and summary["unknown"] == 0}
