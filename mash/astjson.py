from .ast import *
from .syntax import TYPES


def ty_j(t):
    return {"list": ty_j(t.arg)} if t.kind == "List" else t.kind


def j_ty(j):
    if isinstance(j, dict):
        return lst(j_ty(j["list"]))
    return TYPES[j]


def e_j(e):
    t = type(e)
    if t is Lit:
        v = e.val
        if v is None: return {"k": "unit"}
        if isinstance(v, bool): return {"k": "bool", "v": v}
        if isinstance(v, str): return {"k": "str", "v": v}
        return {"k": "int", "v": v}
    if t is Var: return {"k": "var", "name": e.name}
    if t is Call: return {"k": "call", "fn": e.fn, "args": [e_j(a) for a in e.args]}
    if t is Bin: return {"k": "bin", "op": e.op, "a": e_j(e.a), "b": e_j(e.b)}
    if t is Un: return {"k": "un", "op": e.op, "a": e_j(e.a)}
    if t is If: return {"k": "if", "c": e_j(e.c), "t": e_j(e.t), "e": e_j(e.e)}
    if t is Let: return {"k": "let", "name": e.name, "val": e_j(e.val), "body": e_j(e.body)}
    if t is ListE: return {"k": "list", "items": [e_j(a) for a in e.items]}
    if t is Lam: return {"k": "lam", "params": [[n, ty_j(x)] for n, x in e.params], "body": e_j(e.body)}


def j_e(j):
    k = j["k"]
    if k == "unit": return Lit(val=None)
    if k in ("bool", "str", "int"):
        v = j["v"]
        ok = {"bool": lambda: isinstance(v, bool), "str": lambda: isinstance(v, str), "int": lambda: isinstance(v, int) and not isinstance(v, bool)}[k]()
        if not ok:
            raise ValueError("bad literal for " + k)
        return Lit(val=v)
    if k == "var": return Var(name=j["name"])
    if k == "call": return Call(fn=j["fn"], args=[j_e(a) for a in j["args"]])
    if k == "bin": return Bin(op=j["op"], a=j_e(j["a"]), b=j_e(j["b"]))
    if k == "un": return Un(op=j["op"], a=j_e(j["a"]))
    if k == "if": return If(c=j_e(j["c"]), t=j_e(j["t"]), e=j_e(j["e"]))
    if k == "let": return Let(name=j["name"], val=j_e(j["val"]), body=j_e(j["body"]))
    if k == "list": return ListE(items=[j_e(a) for a in j["items"]])
    if k == "lam": return Lam(params=[(n, j_ty(x)) for n, x in j["params"]], body=j_e(j["body"]))
    raise ValueError("unknown node " + k)


def prog_j(p):
    out = []
    for f in p.funcs:
        d = {"name": f.name, "params": [[n, ty_j(t)] for n, t in f.params], "ret": ty_j(f.ret), "effects": list(f.effects)}
        for a in ("pre", "post", "dec"):
            if getattr(f, a) is not None:
                d[a] = e_j(getattr(f, a))
        d["body"] = e_j(f.body)
        out.append(d)
    return {"funcs": out}


def j_prog(j):
    fs = []
    for d in j["funcs"]:
        fs.append(Func(d["name"], [(n, j_ty(t)) for n, t in d["params"]], j_ty(d["ret"]), tuple(sorted(set(d.get("effects", [])))),
                       *(j_e(d[a]) if a in d else None for a in ("pre", "post", "dec")), j_e(d["body"])))
    return Program(fs)
