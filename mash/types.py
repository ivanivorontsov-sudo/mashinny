from .ast import *

UI = Ty("Ui")
A = Ty("?a")
B = Ty("?b")

BUILTINS = {
    "print": ([STR], UNIT, ("io",)),
    "str": ([INT], STR, ()),
    "abs": ([INT], INT, ()),
    "min": ([INT, INT], INT, ()),
    "max": ([INT, INT], INT, ()),
    "wrap64": ([INT], INT, ()),
    "len": None,
    "get": None,
    "push": None,
    "slice": None,
    "rev": None,
    "range": ([INT, INT], lst(INT), ()),
    "sum": ([lst(INT)], INT, ()),
    "sort": ([lst(INT)], lst(INT), ()),
    "join": ([lst(STR), STR], STR, ()),
    "chars": ([STR], lst(STR), ()),
    "map": None,
    "filter": None,
    "fold": None,
    "rect": ([STR, INT, INT, INT], UI, ()),
    "text": ([STR, STR, INT, INT], UI, ()),
    "button": ([STR, STR], UI, ()),
    "col": ([INT, INT, INT, lst(UI)], UI, ()),
    "row": ([INT, INT, INT, lst(UI)], UI, ()),
    "stack": ([lst(UI)], UI, ()),
    "at": ([INT, INT, UI], UI, ()),
    "clip": ([STR, INT, INT, UI], UI, ()),
}
BUILTIN_IDS = {n: i for i, n in enumerate(BUILTINS)}
BUILTIN_NAMES = list(BUILTINS)
EQ_TYPES = ("Int", "Bool", "Str", "Unit", "List")


class TypeErr(Exception):
    def __init__(self, code, msg, pos, path, expected=None, got=None, fix=None):
        super().__init__(msg)
        self.d = {"code": code, "message": msg, "at": {"line": pos[0], "col": pos[1]}, "path": path}
        if expected is not None:
            self.d["expected"] = expected
        if got is not None:
            self.d["got"] = got
        if fix is not None:
            self.d["fix"] = fix

    def to_json(self):
        return self.d


def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def suggest(name, pool):
    best = sorted((lev(name, p), p) for p in pool)
    if best and best[0][0] <= (1 if len(name) < 4 else 2 + len(name) // 6):
        return best[0][1]
    return None


class Checker:
    def __init__(self, prog):
        self.prog = prog
        self.funcs = {}
        self.errors = []
        self.calls = {}
        self.types = {}

    def err(self, *a, **k):
        raise TypeErr(*a, **k)

    def run(self):
        for f in self.prog.funcs:
            if f.name in self.funcs:
                self.errors.append(TypeErr("E-DUP", "duplicate function", f.pos, "fn " + f.name, got=f.name).to_json())
            elif f.name in BUILTINS:
                self.errors.append(TypeErr("E-SHADOW", "function name shadows builtin", f.pos, "fn " + f.name, got=f.name, fix="rename function").to_json())
            self.funcs.setdefault(f.name, f)
        for f in self.prog.funcs:
            self.cur = f
            self.calls[f.name] = set()
            try:
                self.check_func(f)
            except TypeErr as e:
                self.errors.append(e.to_json())
        for f in self.prog.funcs:
            for g in self.calls.get(f.name, ()):
                if g != f.name and f.name in self.reach(g):
                    self.errors.append(TypeErr("E-MUTREC", "mutual recursion is not allowed", f.pos, "fn " + f.name, got=f.name + " <-> " + g, fix="merge into one self-recursive function with dec").to_json())
                    break
            if f.name in self.calls.get(f.name, ()) and f.dec is None:
                self.errors.append(TypeErr("E-NODEC", "recursive function needs a dec measure", f.pos, "fn " + f.name, expected="dec <Int expression decreasing at each recursive call>", fix="add `dec <param>`").to_json())
        return self.errors

    def reach(self, start):
        seen, todo = set(), [start]
        while todo:
            x = todo.pop()
            for y in self.calls.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    todo.append(y)
        return seen

    def check_func(self, f):
        names = [n for n, _ in f.params]
        if len(set(names)) != len(names):
            self.err("E-DUP", "duplicate parameter", f.pos, "fn " + f.name)
        env = dict(f.params)
        p = "fn " + f.name
        for n in names:
            self.binder(n, f.pos, p)
        if f.pre is not None:
            self.expect(self.ex(f.pre, env, p + "/pre", False), BOOL, f.pre, p + "/pre")
        rt = self.ex(f.body, env, p + "/body", "io" in f.effects)
        self.expect(rt, f.ret, f.body, p + "/body")
        if f.post is not None:
            e2 = dict(env); e2["result"] = f.ret
            self.expect(self.ex(f.post, e2, p + "/post", False), BOOL, f.post, p + "/post")
        if f.dec is not None:
            self.expect(self.ex(f.dec, env, p + "/dec", False), INT, f.dec, p + "/dec")

    def expect(self, got, want, node, path):
        if got != want:
            self.err("E-TYPE", "type mismatch", node.pos, path, expected=str(want), got=str(got))

    def ex(self, e, env, path, io):
        t = self.ex0(e, env, path, io)
        self.types[id(e)] = t
        return t

    def binder(self, name, pos, path):
        if name == "result":
            self.err("E-RESERVED", "`result` is reserved for postconditions", pos, path, got=name, fix="rename binder")

    def ex0(self, e, env, path, io):
        if isinstance(e, Lit):
            v = e.val
            return UNIT if v is None else BOOL if isinstance(v, bool) else STR if isinstance(v, str) else INT
        if isinstance(e, Var):
            if e.name not in env:
                s = suggest(e.name, list(env))
                self.err("E-UNBOUND", "unknown variable", e.pos, path, got=e.name, fix=("replace with `" + s + "`") if s else None)
            return env[e.name]
        if isinstance(e, ListE):
            if not e.items:
                self.err("E-EMPTYLIST", "empty list literal has unknown type; use slice of a typed list or avoid", e.pos, path, fix="use `slice([x], 0, 0)` where x has the element type")
            ts = [self.ex(x, env, path + "/item" + str(i), io) for i, x in enumerate(e.items)]
            for i, t in enumerate(ts):
                if t != ts[0]:
                    self.err("E-TYPE", "list items must share one type", e.items[i].pos, path + "/item" + str(i), expected=str(ts[0]), got=str(t))
            return lst(ts[0])
        if isinstance(e, Un):
            t = self.ex(e.a, env, path + "/arg", io)
            want = INT if e.op == "-" else BOOL
            self.expect(t, want, e.a, path + "/arg")
            return want
        if isinstance(e, Bin):
            ta = self.ex(e.a, env, path + "/left", io)
            tb = self.ex(e.b, env, path + "/right", io)
            op = e.op
            if op in ("+", "-", "*", "/", "%", "<", "<=", ">", ">="):
                self.expect(ta, INT, e.a, path + "/left"); self.expect(tb, INT, e.b, path + "/right")
                return INT if op in "+-*/%" else BOOL
            if op in ("&&", "||"):
                self.expect(ta, BOOL, e.a, path + "/left"); self.expect(tb, BOOL, e.b, path + "/right")
                return BOOL
            if op in ("==", "!="):
                if ta.kind not in EQ_TYPES:
                    self.err("E-NOEQ", "type has no equality", e.pos, path, got=str(ta))
                self.expect(tb, ta, e.b, path + "/right")
                return BOOL
            if op == "++":
                if ta != STR and ta.kind != "List":
                    self.err("E-TYPE", "++ needs Str or list", e.a.pos, path + "/left", expected="Str|[T]", got=str(ta))
                self.expect(tb, ta, e.b, path + "/right")
                return ta
        if isinstance(e, If):
            self.expect(self.ex(e.c, env, path + "/cond", io), BOOL, e.c, path + "/cond")
            tt = self.ex(e.t, env, path + "/then", io)
            te = self.ex(e.e, env, path + "/else", io)
            self.expect(te, tt, e.e, path + "/else")
            return tt
        if isinstance(e, Let):
            self.binder(e.name, e.pos, path)
            t = self.ex(e.val, env, path + "/let " + e.name, io)
            e2 = dict(env); e2[e.name] = t
            return self.ex(e.body, e2, path + "/in", io)
        if isinstance(e, Lam):
            self.err("E-LAMBDA", "lambda allowed only as argument of map/filter/fold", e.pos, path)
        if isinstance(e, Call):
            return self.call(e, env, path + "/" + e.fn, io)
        raise TypeError(e)

    def lam(self, e, ptys, env, path):
        if not isinstance(e, Lam):
            self.err("E-TYPE", "expected lambda", e.pos, path, expected="|x: T| expr")
        if [t for _, t in e.params] != ptys:
            self.err("E-TYPE", "lambda parameter types", e.pos, path, expected=", ".join(map(str, ptys)), got=", ".join(str(t) for _, t in e.params))
        for n, _ in e.params:
            self.binder(n, e.pos, path)
        e2 = dict(env)
        e2.update(dict(e.params))
        return self.ex(e.body, e2, path + "/lambda", False)

    def call(self, e, env, path, io):
        n, args = e.fn, e.args
        if n in self.funcs and n not in BUILTINS:
            f = self.funcs[n]
            self.calls[self.cur.name].add(n)
            if len(args) != len(f.params):
                self.err("E-ARITY", "wrong number of arguments", e.pos, path, expected=len(f.params), got=len(args))
            for i, (a, (_, t)) in enumerate(zip(args, f.params)):
                self.expect(self.ex(a, env, path + "/arg" + str(i), io), t, a, path + "/arg" + str(i))
            if f.effects and not io:
                self.err("E-EFFECT", "effectful call in pure context", e.pos, path, expected="pure", got="!" + " !".join(f.effects), fix="add `!io` to the enclosing function")
            return f.ret
        if n not in BUILTINS:
            s = suggest(n, list(self.funcs) + BUILTIN_NAMES)
            self.err("E-UNBOUND", "unknown function", e.pos, path, got=n, fix=("replace with `" + s + "`") if s else None)
        sig = BUILTINS[n]
        if n in ("map", "filter", "fold"):
            k = 3 if n == "fold" else 2
            if len(args) != k:
                self.err("E-ARITY", "wrong number of arguments", e.pos, path, expected=k, got=len(args))
            tl = self.ex(args[0], env, path + "/arg0", io)
            if tl.kind != "List":
                self.err("E-TYPE", "expected list", args[0].pos, path + "/arg0", expected="[T]", got=str(tl))
            if n == "map":
                return lst(self.lam(args[1], [tl.arg], env, path + "/arg1"))
            if n == "filter":
                r = self.lam(args[1], [tl.arg], env, path + "/arg1")
                self.expect(r, BOOL, args[1], path + "/arg1")
                return tl
            ta = self.ex(args[1], env, path + "/arg1", io)
            r = self.lam(args[2], [ta, tl.arg], env, path + "/arg2")
            self.expect(r, ta, args[2], path + "/arg2")
            return ta
        ts = [self.ex(a, env, path + "/arg" + str(i), io) for i, a in enumerate(args)]
        if sig is None:
            want = {"len": 1, "get": 2, "push": 2, "slice": 3, "rev": 1}[n]
            if len(ts) != want:
                self.err("E-ARITY", "wrong number of arguments", e.pos, path, expected=want, got=len(ts))
            t0 = ts[0]
            if t0.kind != "List" and not (t0 == STR and n in ("len", "slice", "rev")):
                self.err("E-TYPE", "expected list", args[0].pos, path + "/arg0", expected="[T]" + ("|Str" if n in ("len", "slice", "rev") else ""), got=str(t0))
            if n == "len":
                return INT
            if n == "get":
                self.expect(ts[1], INT, args[1], path + "/arg1"); return t0.arg
            if n == "push":
                self.expect(ts[1], t0.arg, args[1], path + "/arg1"); return t0
            if n == "slice":
                self.expect(ts[1], INT, args[1], path + "/arg1"); self.expect(ts[2], INT, args[2], path + "/arg2"); return t0
            return t0
        params, ret, eff = sig
        if len(ts) != len(params):
            self.err("E-ARITY", "wrong number of arguments", e.pos, path, expected=len(params), got=len(ts))
        for i, (t, w) in enumerate(zip(ts, params)):
            self.expect(t, w, args[i], path + "/arg" + str(i))
        if eff and not io:
            self.err("E-EFFECT", "effectful call in pure context", e.pos, path, expected="pure", got="!io", fix="add `!io` to the enclosing function")
        return ret


def check(prog):
    return Checker(prog).run()
