import hashlib
import re
from .ast import *
from .types import BUILTIN_IDS, BUILTIN_NAMES
from .syntax import canon, KEYWORDS

MAGIC = b"PNB\x01"
OPS = ["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=", "&&", "||", "++"]
TYS = {"Int": 0, "Bool": 1, "Str": 2, "Unit": 3, "Ui": 5}
TYN = {v: k for k, v in TYS.items()}
EFF = ["io"]
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
T_INT, T_TRUE, T_FALSE, T_STR, T_UNIT, T_VAR, T_CALL, T_PRIM, T_BIN, T_NEG, T_NOT, T_IF, T_LET, T_LIST, T_LAM = range(0x10, 0x1F)


class BinErr(Exception):
    def __init__(self, code, msg, offset):
        super().__init__(msg)
        self.code, self.msg, self.offset = code, msg, offset

    def to_json(self):
        return {"code": self.code, "message": self.msg, "offset": self.offset}


def uleb(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def zig(n):
    return n * 2 if n >= 0 else -n * 2 - 1


def unzig(n):
    return n // 2 if n % 2 == 0 else -(n + 1) // 2


class Enc:
    def __init__(self, prog):
        self.prog = canon(prog)
        self.findex = {f.name: i for i, f in enumerate(self.prog.funcs)}
        self.names = []

    def s(self, txt):
        b = txt.encode("utf-8")
        return uleb(len(b)) + b

    def ty(self, t):
        if t.kind == "List":
            return bytes([4]) + self.ty(t.arg)
        return bytes([TYS[t.kind]])

    def e(self, x, scope):
        t = type(x)
        if t is Lit:
            v = x.val
            if v is None: return bytes([T_UNIT])
            if v is True: return bytes([T_TRUE])
            if v is False: return bytes([T_FALSE])
            if isinstance(v, str): return bytes([T_STR]) + self.s(v)
            return bytes([T_INT]) + uleb(zig(v))
        if t is Var:
            if x.name not in scope:
                raise BinErr("E-UNBOUND", "unknown variable " + x.name, 0)
            return bytes([T_VAR]) + uleb(len(scope) - 1 - max(i for i, n in enumerate(scope) if n == x.name))
        if t is Call:
            body = b"".join(self.e(a, scope) for a in x.args)
            if x.fn in self.findex:
                return bytes([T_CALL]) + uleb(self.findex[x.fn]) + uleb(len(x.args)) + body
            if x.fn in BUILTIN_IDS:
                return bytes([T_PRIM]) + uleb(BUILTIN_IDS[x.fn]) + uleb(len(x.args)) + body
            raise BinErr("E-UNBOUND", "unknown function " + x.fn, 0)
        if t is Bin:
            return bytes([T_BIN, OPS.index(x.op)]) + self.e(x.a, scope) + self.e(x.b, scope)
        if t is Un:
            return bytes([T_NEG if x.op == "-" else T_NOT]) + self.e(x.a, scope)
        if t is If:
            return bytes([T_IF]) + self.e(x.c, scope) + self.e(x.t, scope) + self.e(x.e, scope)
        if t is Let:
            v = self.e(x.val, scope)
            self.names.append(x.name)
            return bytes([T_LET]) + v + self.e(x.body, scope + [x.name])
        if t is ListE:
            return bytes([T_LIST]) + uleb(len(x.items)) + b"".join(self.e(a, scope) for a in x.items)
        if t is Lam:
            out = bytes([T_LAM]) + uleb(len(x.params)) + b"".join(self.ty(ty) for _, ty in x.params)
            self.names.extend(n for n, _ in x.params)
            return out + self.e(x.body, scope + [n for n, _ in x.params])
        raise TypeError(x)

    def func(self, f):
        out = bytearray(self.s(f.name))
        out += uleb(len(f.params)) + b"".join(self.ty(t) for _, t in f.params) + self.ty(f.ret)
        out += uleb(sum(1 << EFF.index(x) for x in f.effects))
        out += uleb((f.pre is not None) | (f.post is not None) << 1 | (f.dec is not None) << 2)
        self.names.extend(n for n, _ in f.params)
        sc = [n for n, _ in f.params]
        if f.pre is not None: out += self.e(f.pre, sc)
        if f.post is not None: out += self.e(f.post, sc + ["result"])
        if f.dec is not None: out += self.e(f.dec, sc)
        out += self.e(f.body, sc)
        return bytes(out)

    def run(self):
        fs = [self.func(f) for f in self.prog.funcs]
        code = uleb(len(fs)) + b"".join(uleb(len(b)) + b for b in fs)
        names = uleb(len(self.names)) + b"".join(self.s(n) for n in self.names)
        return MAGIC + uleb(len(code)) + code + names, code, fs


def encode(prog):
    return Enc(prog).run()[0]


def hashes(prog):
    data, code, fs = Enc(prog).run()
    p = canon(prog)
    return {
        "hash": "sha256:" + hashlib.sha256(data).hexdigest(),
        "semantic_hash": "sha256:" + hashlib.sha256(code).hexdigest(),
        "functions": {f.name: "sha256:" + hashlib.sha256(b).hexdigest() for f, b in zip(p.funcs, fs)},
        "bytes": len(data),
    }


class Dec:
    def __init__(self, data):
        self.d = data
        self.i = 0
        self.names = []
        self.ni = 0

    def fail(self, code, msg):
        raise BinErr(code, msg, self.i)

    def byte(self):
        if self.i >= len(self.d):
            self.fail("B-EOF", "unexpected end of data")
        b = self.d[self.i]
        self.i += 1
        return b

    def uleb(self):
        start, n, shift = self.i, 0, 0
        while True:
            b = self.byte()
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
            if shift > 7 * 700:
                self.fail("B-LEB", "integer too long")
        if self.i - start > 1 and b == 0:
            self.i = start
            self.fail("B-NONCANON", "non-minimal LEB128")
        return n

    def s(self):
        n = self.uleb()
        if self.i + n > len(self.d):
            self.fail("B-EOF", "string past end")
        raw = self.d[self.i:self.i + n]
        self.i += n
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            self.fail("B-UTF8", "invalid UTF-8")

    def ty(self):
        b = self.byte()
        if b == 4:
            return lst(self.ty())
        if b not in TYN:
            self.fail("B-TAG", "unknown type tag")
        return Ty(TYN[b])

    def name(self):
        if self.ni >= len(self.names):
            self.fail("B-NAMES", "names section too short")
        n = self.names[self.ni]
        self.ni += 1
        return n

    def e(self, scope):
        tag = self.byte()
        if tag == T_INT: return Lit(val=unzig(self.uleb()))
        if tag == T_TRUE: return Lit(val=True)
        if tag == T_FALSE: return Lit(val=False)
        if tag == T_UNIT: return Lit(val=None)
        if tag == T_STR: return Lit(val=self.s())
        if tag == T_VAR:
            k = self.uleb()
            if k >= len(scope):
                self.fail("B-VAR", "variable index out of scope")
            return Var(name=scope[len(scope) - 1 - k])
        if tag in (T_CALL, T_PRIM):
            idx = self.uleb()
            pool = self.fnames if tag == T_CALL else BUILTIN_NAMES
            if idx >= len(pool):
                self.fail("B-REF", "function index out of range")
            argc = self.uleb()
            return Call(fn=pool[idx], args=[self.e(scope) for _ in range(argc)])
        if tag == T_BIN:
            op = self.byte()
            if op >= len(OPS):
                self.fail("B-TAG", "unknown operator")
            return Bin(op=OPS[op], a=self.e(scope), b=self.e(scope))
        if tag in (T_NEG, T_NOT):
            return Un(op="-" if tag == T_NEG else "!", a=self.e(scope))
        if tag == T_IF:
            return If(c=self.e(scope), t=self.e(scope), e=self.e(scope))
        if tag == T_LET:
            v = self.e(scope)
            n = self.name()
            return Let(name=n, val=v, body=self.e(scope + [n]))
        if tag == T_LIST:
            return ListE(items=[self.e(scope) for _ in range(self.uleb())])
        if tag == T_LAM:
            k = self.uleb()
            tys = [self.ty() for _ in range(k)]
            ps = [(self.name(), t) for t in tys]
            return Lam(params=ps, body=self.e(scope + [n for n, _ in ps]))
        self.fail("B-TAG", "unknown node tag 0x%02x" % tag)

    def run(self):
        d = self.d
        if d[:4] != MAGIC:
            self.fail("B-MAGIC", "not a .pnb file (magic PNB\\x01)")
        self.i = 4
        clen = self.uleb()
        cstart = self.i
        self.i = cstart + clen
        if self.i > len(d):
            self.fail("B-EOF", "code section past end")
        self.names = [self.s() for _ in range(self.uleb())]
        for n in self.names:
            if not IDENT.match(n) or n in KEYWORDS:
                self.fail("B-NAME", "invalid identifier in names section")
        if self.i != len(d):
            self.fail("B-TRAIL", "trailing bytes")
        self.i = cstart
        nf = self.uleb()
        heads = []
        for _ in range(nf):
            ln = self.uleb()
            heads.append((self.i, ln))
            self.i += ln
        if self.i != cstart + clen:
            self.fail("B-LEN", "code section length mismatch")
        self.fnames = []
        for off, ln in heads:
            self.i = off
            self.fnames.append(self.s())
        if self.fnames != sorted(set(self.fnames)):
            self.fail("B-ORDER", "functions must be unique and sorted by name")
        funcs = []
        for off, ln in heads:
            self.i = off
            name = self.s()
            if not IDENT.match(name) or name in KEYWORDS:
                self.fail("B-NAME", "invalid function name")
            tys = [self.ty() for _ in range(self.uleb())]
            ret = self.ty()
            em = self.uleb()
            if em >> len(EFF):
                self.fail("B-EFF", "unknown effect bits")
            effects = tuple(x for k, x in enumerate(EFF) if em >> k & 1)
            fl = self.uleb()
            if fl >> 3:
                self.fail("B-FLAGS", "unknown flags")
            params = [(self.name(), t) for t in tys]
            sc = [n for n, _ in params]
            pre = self.e(sc) if fl & 1 else None
            post = self.e(sc + ["result"]) if fl & 2 else None
            dec = self.e(sc) if fl & 4 else None
            body = self.e(sc)
            if self.i != off + ln:
                self.fail("B-LEN", "function length mismatch")
            funcs.append(Func(name, params, ret, effects, pre, post, dec, body))
        if self.ni != len(self.names):
            self.fail("B-NAMES", "unused names in names section")
        prog = Program(funcs)
        try:
            again = encode(prog)
        except BinErr:
            again = None
        if again != bytes(d):
            raise BinErr("B-NONCANON", "binary is not in canonical form", 0)
        return prog


def decode(data):
    return Dec(bytes(data)).run()
