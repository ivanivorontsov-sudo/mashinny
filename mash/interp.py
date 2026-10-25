from .ast import *

MAXBITS = 4096
DEFAULT_FUEL = 1_000_000


class Trap(Exception):
    def __init__(self, code, msg, detail=None):
        super().__init__(msg)
        self.code, self.msg, self.detail = code, msg, detail

    def to_json(self):
        d = {"code": self.code, "message": self.msg}
        if self.detail is not None:
            d["detail"] = self.detail
        return d


def chk(v):
    if v.bit_length() > MAXBITS:
        raise Trap("T-BIG", "integer exceeds 4096 bits")
    return v


def fdiv(a, b):
    if b == 0:
        raise Trap("T-DIV0", "division by zero")
    return a // b


def fmod(a, b):
    if b == 0:
        raise Trap("T-DIV0", "division by zero")
    return a % b


def wrap64(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >= 1 << 63 else v


class Machine:
    def __init__(self, prog, fuel=DEFAULT_FUEL, live_io=False):
        self.funcs = {f.name: f for f in prog.funcs}
        self.fuel = fuel
        self.steps = 0
        self.effects = []
        self.live_io = live_io
        self.depth = 0

    def tick(self, n=1):
        self.steps += n
        if self.steps > self.fuel:
            self.steps = self.fuel
            raise Trap("T-FUEL", "step limit exhausted", {"fuel": self.fuel})

    def call_user(self, f, vals):
        self.depth += 1
        if self.depth > 10000:
            raise Trap("T-DEPTH", "call depth exceeds 10000")
        env = {n: v for (n, _), v in zip(f.params, vals)}
        if f.pre is not None and not self.ev(f.pre, env):
            raise Trap("T-PRE", "precondition failed", {"fn": f.name, "args": [show(v) for v in vals]})
        if f.dec is not None:
            d0 = self.ev(f.dec, env)
            if d0 < 0:
                raise Trap("T-DEC", "dec measure negative", {"fn": f.name, "dec": d0})
        r = self.ev(f.body, env)
        if f.post is not None:
            e2 = dict(env); e2["result"] = r
            if not self.ev(f.post, e2):
                raise Trap("T-POST", "postcondition failed", {"fn": f.name, "result": show(r)})
        self.depth -= 1
        return r

    def ev(self, e, env):
        self.tick()
        t = type(e)
        if t is Lit:
            return e.val
        if t is Var:
            return env[e.name]
        if t is Bin:
            op = e.op
            if op == "&&":
                return self.ev(e.a, env) and self.ev(e.b, env)
            if op == "||":
                return self.ev(e.a, env) or self.ev(e.b, env)
            a = self.ev(e.a, env)
            b = self.ev(e.b, env)
            if op == "+": return chk(a + b)
            if op == "-": return chk(a - b)
            if op == "*":
                if a.bit_length() + b.bit_length() > MAXBITS + 1:
                    raise Trap("T-BIG", "integer exceeds 4096 bits")
                return chk(a * b)
            if op == "/": return fdiv(a, b)
            if op == "%": return fmod(a, b)
            if op == "==": return a == b
            if op == "!=": return a != b
            if op == "<": return a < b
            if op == "<=": return a <= b
            if op == ">": return a > b
            if op == ">=": return a >= b
            if op == "++":
                self.tick(len(a) + len(b))
                return a + b
        if t is Un:
            v = self.ev(e.a, env)
            return chk(-v) if e.op == "-" else not v
        if t is If:
            return self.ev(e.t, env) if self.ev(e.c, env) else self.ev(e.e, env)
        if t is Let:
            v = self.ev(e.val, env)
            e2 = dict(env); e2[e.name] = v
            return self.ev(e.body, e2)
        if t is ListE:
            return tuple(self.ev(x, env) for x in e.items)
        if t is Call:
            if e.fn in self.funcs:
                return self.call_user(self.funcs[e.fn], [self.ev(a, env) for a in e.args])
            return self.builtin(e, env)
        raise TypeError(e)

    def lam(self, l, env, vals):
        e2 = dict(env)
        for (n, _), v in zip(l.params, vals):
            e2[n] = v
        return self.ev(l.body, e2)

    def builtin(self, e, env):
        n = e.fn
        if n in ("map", "filter", "fold"):
            xs = self.ev(e.args[0], env)
            if n == "map":
                return tuple(self.lam(e.args[1], env, [x]) for x in xs)
            if n == "filter":
                return tuple(x for x in xs if self.lam(e.args[1], env, [x]))
            acc = self.ev(e.args[1], env)
            for x in xs:
                acc = self.lam(e.args[2], env, [acc, x])
            return acc
        a = [self.ev(x, env) for x in e.args]
        return self.prim(n, a)

    def prim(self, n, a):
        if n == "print":
            self.effects.append({"effect": "io", "op": "print", "arg": a[0]})
            if self.live_io:
                print(a[0], flush=True)
            return None
        if n == "str": return str(a[0])
        if n == "abs": return abs(a[0])
        if n == "min": return min(a)
        if n == "max": return max(a)
        if n == "wrap64": return wrap64(a[0])
        if n == "len": return len(a[0])
        if n == "get":
            if not 0 <= a[1] < len(a[0]):
                raise Trap("T-INDEX", "index out of range", {"index": a[1], "len": len(a[0])})
            return a[0][a[1]]
        if n == "push":
            self.tick(len(a[0]))
            return a[0] + (a[1],)
        if n == "slice":
            s, lo, hi = a
            lo = max(0, min(lo, len(s))); hi = max(lo, min(hi, len(s)))
            self.tick(hi - lo)
            return s[lo:hi]
        if n == "rev":
            self.tick(len(a[0]))
            return a[0][::-1]
        if n == "range":
            k = max(0, a[1] - a[0])
            self.tick(k)
            return tuple(range(a[0], a[0] + k))
        if n == "sum":
            self.tick(len(a[0]))
            return chk(sum(a[0]))
        if n == "sort":
            self.tick(len(a[0]))
            return tuple(sorted(a[0]))
        if n == "join":
            self.tick(len(a[0]))
            return a[1].join(a[0])
        if n == "chars":
            self.tick(len(a[0]))
            return tuple(a[0])
        if n in ("rect", "text", "button", "col", "row", "stack", "at", "clip"):
            if n in ("col", "row", "stack"):
                self.tick(len(a[-1]))
            return ("ui", n) + tuple(a)
        raise Trap("T-INTERNAL", "unknown builtin " + n)


def show(v):
    if isinstance(v, tuple) and len(v) >= 2 and v[0] == "ui":
        return {"ui": v[1], "args": [show(x) for x in v[2:]]}
    if isinstance(v, tuple):
        return [show(x) for x in v]
    if isinstance(v, int) and not isinstance(v, bool) and abs(v) >= 1 << 53:
        return {"int": str(v)}
    return v


def from_json(v, t):
    if t.kind == "Int":
        if isinstance(v, dict):
            return int(v["int"])
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("expected Int")
        return v
    if t.kind == "Bool":
        if not isinstance(v, bool):
            raise ValueError("expected Bool")
        return v
    if t.kind == "Str":
        if not isinstance(v, str):
            raise ValueError("expected Str")
        return v
    if t.kind == "Unit":
        return None
    if t.kind == "List":
        return tuple(from_json(x, t.arg) for x in v)
    raise ValueError("unsupported argument type " + str(t))


def execute(prog, entry="main", args=(), fuel=DEFAULT_FUEL, live_io=False):
    m = Machine(prog, fuel, live_io)
    f = m.funcs.get(entry)
    if f is None:
        return {"status": "error", "error": {"code": "E-ENTRY", "message": "entry function not found", "got": entry}}
    try:
        vals = [from_json(a, t) for a, (_, t) in zip(args, f.params)]
        if len(vals) != len(f.params):
            raise ValueError("wrong number of arguments")
    except (ValueError, KeyError, TypeError) as ex:
        return {"status": "error", "error": {"code": "E-ARGS", "message": str(ex)}}
    res = {"entry": entry}
    try:
        v = m.call_user(f, vals)
        res.update(status="ok", value=show(v))
        res["_raw"] = v
    except Trap as t:
        res.update(status="trap", trap=t.to_json())
    except RecursionError:
        res.update(status="trap", trap={"code": "T-DEPTH", "message": "host recursion limit"})
    res["steps"] = m.steps
    res["fuel"] = fuel
    res["effects"] = m.effects
    return res
