import random
from mash.ast import *

OPS_I = ["+", "-", "*", "/", "%"]
OPS_C = ["==", "!=", "<", "<=", ">", ">="]


class Gen:
    def __init__(self, seed):
        self.r = random.Random(seed)
        self.n = 0

    def name(self):
        self.n += 1
        return "v%d" % self.n

    def ex(self, t, env, d):
        r = self.r
        vs = [n for n, ty in env.items() if ty == t]
        if d <= 0 or r.random() < 0.15:
            if vs and r.random() < 0.6:
                return Var(name=r.choice(vs))
            return self.leaf(t)
        k = r.random()
        if k < 0.12:
            n = self.name()
            vt = r.choice([INT, BOOL, STR, lst(INT)])
            v = self.ex(vt, env, d - 1)
            e2 = dict(env); e2[n] = vt
            return Let(name=n, val=v, body=self.ex(t, e2, d - 1))
        if k < 0.24:
            return If(c=self.ex(BOOL, env, d - 1), t=self.ex(t, env, d - 1), e=self.ex(t, env, d - 1))
        if t == INT:
            c = r.randrange(9)
            if c < 4:
                return Bin(op=r.choice(OPS_I), a=self.ex(INT, env, d - 1), b=self.ex(INT, env, d - 1))
            if c == 4:
                return Un(op="-", a=self.ex(INT, env, d - 1))
            if c == 5:
                return Call(fn=r.choice(["len"]), args=[self.ex(r.choice([STR, lst(INT)]), env, d - 1)])
            if c == 6:
                return Call(fn=r.choice(["sum"]), args=[self.ex(lst(INT), env, d - 1)])
            if c == 7:
                n1, n2 = self.name(), self.name()
                e2 = dict(env); e2[n1] = INT; e2[n2] = INT
                return Call(fn="fold", args=[self.ex(lst(INT), env, d - 1), self.ex(INT, env, d - 1), Lam(params=[(n1, INT), (n2, INT)], body=self.ex(INT, e2, d - 2))])
            return Call(fn=r.choice(["min", "max"]), args=[self.ex(INT, env, d - 1), self.ex(INT, env, d - 1)]) if r.random() < 0.5 else Call(fn="get", args=[self.ex(lst(INT), env, d - 1), self.ex(INT, env, d - 1)])
        if t == BOOL:
            c = r.randrange(4)
            if c == 0:
                return Bin(op=r.choice(OPS_C), a=self.ex(INT, env, d - 1), b=self.ex(INT, env, d - 1))
            if c == 1:
                return Bin(op=r.choice(["&&", "||"]), a=self.ex(BOOL, env, d - 1), b=self.ex(BOOL, env, d - 1))
            if c == 2:
                return Un(op="!", a=self.ex(BOOL, env, d - 1))
            tt = r.choice([STR, lst(INT)])
            return Bin(op=r.choice(["==", "!="]), a=self.ex(tt, env, d - 1), b=self.ex(tt, env, d - 1))
        if t == STR:
            c = r.randrange(4)
            if c == 0:
                return Bin(op="++", a=self.ex(STR, env, d - 1), b=self.ex(STR, env, d - 1))
            if c == 1:
                return Call(fn="str", args=[self.ex(INT, env, d - 1)])
            if c == 2:
                return Call(fn="slice", args=[self.ex(STR, env, d - 1), self.ex(INT, env, d - 1), self.ex(INT, env, d - 1)])
            return Call(fn="rev", args=[self.ex(STR, env, d - 1)])
        c = r.randrange(6)
        if c == 0:
            return ListE(items=[self.ex(INT, env, d - 1) for _ in range(r.randrange(1, 4))])
        if c == 1:
            return Call(fn="range", args=[self.leaf(INT, 5), self.leaf(INT, 12)])
        if c == 2:
            n1 = self.name()
            e2 = dict(env); e2[n1] = INT
            return Call(fn="map", args=[self.ex(t, env, d - 1), Lam(params=[(n1, INT)], body=self.ex(INT, e2, d - 2))])
        if c == 3:
            n1 = self.name()
            e2 = dict(env); e2[n1] = INT
            return Call(fn="filter", args=[self.ex(t, env, d - 1), Lam(params=[(n1, INT)], body=self.ex(BOOL, e2, d - 2))])
        if c == 4:
            return Call(fn=r.choice(["sort", "rev"]), args=[self.ex(t, env, d - 1)])
        return Bin(op="++", a=self.ex(t, env, d - 1), b=Call(fn="push", args=[self.ex(t, env, d - 1), self.ex(INT, env, d - 1)]))

    def leaf(self, t, m=None):
        r = self.r
        if t == INT:
            return Lit(val=r.randrange(-(m or 20), (m or 20)) if r.random() < 0.9 else r.randrange(-10 ** 25, 10 ** 25))
        if t == BOOL:
            return Lit(val=r.random() < 0.5)
        if t == STR:
            return Lit(val=r.choice(["", "a", "ж", "Ёлка", "x\"y", "t\\n", "\n", "hello"]))
        return ListE(items=[Lit(val=r.randrange(-9, 9)) for _ in range(r.randrange(1, 5))])

    def program(self):
        r = self.r
        funcs = []
        ps = [("p", INT), ("q", BOOL)]
        h = Func("helper", ps, INT, (), Bin(op=">", a=Var(name="p"), b=Lit(val=-1000)) if r.random() < 0.5 else None,
                 None, None, self.ex(INT, dict(ps), 3))
        funcs.append(h)
        rec = Func("down", [("n", INT)], INT, (), Bin(op=">=", a=Var(name="n"), b=Lit(val=0)), None, Var(name="n"),
                   If(c=Bin(op="<=", a=Var(name="n"), b=Lit(val=0)), t=Lit(val=0), e=Bin(op="+", a=Var(name="n"), b=Call(fn="down", args=[Bin(op="-", a=Var(name="n"), b=Lit(val=1))]))))
        funcs.append(rec)
        env = {}
        body = self.ex(r.choice([INT, BOOL, STR, lst(INT)]), env, 5)
        n = self.name()
        body = Let(name=n, val=Call(fn="print", args=[Call(fn="str", args=[Call(fn="helper", args=[self.ex(INT, env, 2), self.ex(BOOL, env, 2)])])]),
                   body=Let(name=self.name(), val=Call(fn="down", args=[Call(fn="abs", args=[self.leaf(INT, 30)])]), body=body))
        ret = None
        main = Func("main", [], INT, ("io",), None, None, None, body)
        return Program(funcs + [main])
