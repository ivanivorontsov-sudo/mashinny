from .ast import *
from .interp import Machine, Trap, chk, fdiv, fmod, show, from_json, DEFAULT_FUEL

PUSH, LOAD, BIN, NEG, NOT, JMPF, JMP, AND, OR, BIND, UNBIND, LIST, CALL, PRIM, HOF, RET, TICK = range(17)


class Code:
    def __init__(self):
        self.ins = []

    def emit(self, op, a=None, b=None, cost=0):
        self.ins.append([op, a, b, cost])
        return len(self.ins) - 1


class Compiler:
    def __init__(self, prog):
        self.prog = prog
        self.fidx = {f.name: i for i, f in enumerate(prog.funcs)}
        self.blocks = []

    def block(self, e, scope):
        c = Code()
        self.ex(e, scope, c)
        c.emit(RET)
        self.blocks.append(c.ins)
        return len(self.blocks) - 1

    def ex(self, e, scope, c):
        start = len(c.ins)
        c.emit(TICK, cost=1)
        t = type(e)
        if t is Lit:
            c.emit(PUSH, e.val)
        elif t is Var:
            c.emit(LOAD, max(i for i, n in enumerate(scope) if n == e.name))
        elif t is Bin:
            if e.op in ("&&", "||"):
                self.ex(e.a, scope, c)
                j = c.emit(AND if e.op == "&&" else OR)
                self.ex(e.b, scope, c)
                c.ins[j][1] = len(c.ins)
            else:
                self.ex(e.a, scope, c)
                self.ex(e.b, scope, c)
                c.emit(BIN, e.op)
        elif t is Un:
            self.ex(e.a, scope, c)
            c.emit(NEG if e.op == "-" else NOT)
        elif t is If:
            self.ex(e.c, scope, c)
            j = c.emit(JMPF)
            self.ex(e.t, scope, c)
            k = c.emit(JMP)
            c.ins[j][1] = len(c.ins)
            self.ex(e.e, scope, c)
            c.ins[k][1] = len(c.ins)
        elif t is Let:
            self.ex(e.val, scope, c)
            c.emit(BIND)
            self.ex(e.body, scope + [e.name], c)
            c.emit(UNBIND)
        elif t is ListE:
            for x in e.items:
                self.ex(x, scope, c)
            c.emit(LIST, len(e.items))
        elif t is Call:
            if e.fn in ("map", "filter", "fold"):
                for a in e.args[:-1]:
                    self.ex(a, scope, c)
                lam = e.args[-1]
                b = self.block(lam.body, scope + [n for n, _ in lam.params])
                c.emit(HOF, e.fn, b)
            else:
                for a in e.args:
                    self.ex(a, scope, c)
                if e.fn in self.fidx:
                    c.emit(CALL, self.fidx[e.fn], len(e.args))
                else:
                    c.emit(PRIM, e.fn, len(e.args))
        else:
            raise TypeError(e)
        if c.ins[start][0] == TICK and len(c.ins) > start + 1:
            c.ins[start + 1][3] += 1
            del c.ins[start]
            for ins in c.ins[start:]:
                if ins[0] in (JMPF, JMP, AND, OR) and ins[1] is not None and ins[1] > start:
                    ins[1] -= 1

    def run(self):
        self.funcs = []
        for f in self.prog.funcs:
            sc = [n for n, _ in f.params]
            self.funcs.append({
                "name": f.name, "f": f, "n": len(f.params),
                "pre": self.block(f.pre, sc) if f.pre is not None else None,
                "dec": self.block(f.dec, sc) if f.dec is not None else None,
                "body": self.block(f.body, sc),
                "post": self.block(f.post, sc + ["result"]) if f.post is not None else None,
            })
        return self


def compile_program(prog):
    return Compiler(prog).run()


class VM:
    def __init__(self, comp, fuel=DEFAULT_FUEL, live_io=False):
        self.c = comp
        self.m = Machine(comp.prog, fuel, live_io)
        self.depth = 0

    def exec_block(self, b, locs):
        m = self.m
        ins = self.c.blocks[b]
        st = []
        pc = 0
        while True:
            op, a, b2, cost = ins[pc]
            if cost:
                m.steps += cost
                if m.steps > m.fuel:
                    raise Trap("T-FUEL", "step limit exhausted", {"fuel": m.fuel})
            pc += 1
            if op == PUSH:
                st.append(a)
            elif op == LOAD:
                st.append(locs[a])
            elif op == BIN:
                y = st.pop(); x = st.pop()
                if a == "+": r = chk(x + y)
                elif a == "-": r = chk(x - y)
                elif a == "*":
                    if x.bit_length() + y.bit_length() > 4097:
                        raise Trap("T-BIG", "integer exceeds 4096 bits")
                    r = chk(x * y)
                elif a == "/": r = fdiv(x, y)
                elif a == "%": r = fmod(x, y)
                elif a == "==": r = x == y
                elif a == "!=": r = x != y
                elif a == "<": r = x < y
                elif a == "<=": r = x <= y
                elif a == ">": r = x > y
                elif a == ">=": r = x >= y
                else:
                    m.tick(len(x) + len(y)); r = x + y
                st.append(r)
            elif op == NEG:
                st.append(chk(-st.pop()))
            elif op == NOT:
                st.append(not st.pop())
            elif op == JMPF:
                if not st.pop():
                    pc = a
            elif op == JMP:
                pc = a
            elif op == AND:
                if not st[-1]:
                    pc = a
                else:
                    st.pop()
            elif op == OR:
                if st[-1]:
                    pc = a
                else:
                    st.pop()
            elif op == BIND:
                locs = locs + [st.pop()]
            elif op == UNBIND:
                locs = locs[:-1]
            elif op == LIST:
                vals = tuple(st[len(st) - a:]) if a else ()
                del st[len(st) - a:]
                st.append(vals)
            elif op == CALL:
                args = st[len(st) - b2:] if b2 else []
                del st[len(st) - b2:]
                st.append(self.call(a, list(args)))
            elif op == PRIM:
                args = st[len(st) - b2:] if b2 else []
                del st[len(st) - b2:]
                st.append(m.prim(a, list(args)))
            elif op == HOF:
                if a == "map":
                    xs = st.pop()
                    st.append(tuple(self.exec_block(b2, locs + [x]) for x in xs))
                elif a == "filter":
                    xs = st.pop()
                    st.append(tuple(x for x in xs if self.exec_block(b2, locs + [x])))
                else:
                    acc = st.pop(); xs = st.pop()
                    for x in xs:
                        acc = self.exec_block(b2, locs + [acc, x])
                    st.append(acc)
            elif op == RET:
                return st.pop()

    def call(self, i, args):
        fi = self.c.funcs[i]
        f = fi["f"]
        self.depth += 1
        if self.depth > 10000:
            raise Trap("T-DEPTH", "call depth exceeds 10000")
        if fi["pre"] is not None and not self.exec_block(fi["pre"], args):
            raise Trap("T-PRE", "precondition failed", {"fn": f.name, "args": [show(v) for v in args]})
        if fi["dec"] is not None:
            d0 = self.exec_block(fi["dec"], args)
            if d0 < 0:
                raise Trap("T-DEC", "dec measure negative", {"fn": f.name, "dec": d0})
        r = self.exec_block(fi["body"], args)
        if fi["post"] is not None and not self.exec_block(fi["post"], args + [r]):
            raise Trap("T-POST", "postcondition failed", {"fn": f.name, "result": show(r)})
        self.depth -= 1
        return r


def execute_vm(prog, entry="main", args=(), fuel=DEFAULT_FUEL, live_io=False):
    comp = compile_program(prog)
    vm = VM(comp, fuel, live_io)
    names = [f.name for f in prog.funcs]
    if entry not in names:
        return {"status": "error", "error": {"code": "E-ENTRY", "message": "entry function not found", "got": entry}}
    f = prog.funcs[names.index(entry)]
    try:
        vals = [from_json(a, t) for a, (_, t) in zip(args, f.params)]
        if len(vals) != len(f.params):
            raise ValueError("wrong number of arguments")
    except (ValueError, KeyError, TypeError) as ex:
        return {"status": "error", "error": {"code": "E-ARGS", "message": str(ex)}}
    res = {"entry": entry}
    try:
        v = vm.call(names.index(entry), vals)
        res.update(status="ok", value=show(v))
        res["_raw"] = v
    except Trap as t:
        res.update(status="trap", trap=t.to_json())
    except RecursionError:
        res.update(status="trap", trap={"code": "T-DEPTH", "message": "host recursion limit"})
    res["steps"] = vm.m.steps
    res["fuel"] = fuel
    res["effects"] = vm.m.effects
    return res
