from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Ty:
    kind: str
    arg: Optional["Ty"] = None

    def __str__(self):
        if self.kind == "List":
            return "[" + str(self.arg) + "]"
        return self.kind


INT = Ty("Int")
BOOL = Ty("Bool")
STR = Ty("Str")
UNIT = Ty("Unit")


def lst(t):
    return Ty("List", t)


@dataclass(eq=False)
class Node:
    pos: tuple = field(default=(0, 0), compare=False, repr=False)


@dataclass(eq=False)
class Lit(Node):
    val: object = None


@dataclass(eq=False)
class Var(Node):
    name: str = ""


@dataclass(eq=False)
class Call(Node):
    fn: str = ""
    args: list = field(default_factory=list)


@dataclass(eq=False)
class Bin(Node):
    op: str = ""
    a: Node = None
    b: Node = None


@dataclass(eq=False)
class Un(Node):
    op: str = ""
    a: Node = None


@dataclass(eq=False)
class If(Node):
    c: Node = None
    t: Node = None
    e: Node = None


@dataclass(eq=False)
class Let(Node):
    name: str = ""
    val: Node = None
    body: Node = None


@dataclass(eq=False)
class ListE(Node):
    items: list = field(default_factory=list)


@dataclass(eq=False)
class Lam(Node):
    params: list = field(default_factory=list)
    body: Node = None


@dataclass(eq=False)
class Func:
    name: str
    params: list
    ret: Ty
    effects: tuple
    pre: Optional[Node]
    post: Optional[Node]
    dec: Optional[Node]
    body: Node
    pos: tuple = (0, 0)


@dataclass(eq=False)
class Program:
    funcs: list


def same(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, Lit):
        return type(a.val) is type(b.val) and a.val == b.val
    if isinstance(a, Var):
        return a.name == b.name
    if isinstance(a, Call):
        return a.fn == b.fn and len(a.args) == len(b.args) and all(same(x, y) for x, y in zip(a.args, b.args))
    if isinstance(a, Bin):
        return a.op == b.op and same(a.a, b.a) and same(a.b, b.b)
    if isinstance(a, Un):
        return a.op == b.op and same(a.a, b.a)
    if isinstance(a, If):
        return same(a.c, b.c) and same(a.t, b.t) and same(a.e, b.e)
    if isinstance(a, Let):
        return a.name == b.name and same(a.val, b.val) and same(a.body, b.body)
    if isinstance(a, ListE):
        return len(a.items) == len(b.items) and all(same(x, y) for x, y in zip(a.items, b.items))
    if isinstance(a, Lam):
        return a.params == b.params and same(a.body, b.body)
    if isinstance(a, Func):
        return (a.name == b.name and a.params == b.params and a.ret == b.ret and a.effects == b.effects
                and opt_same(a.pre, b.pre) and opt_same(a.post, b.post) and opt_same(a.dec, b.dec) and same(a.body, b.body))
    if isinstance(a, Program):
        return len(a.funcs) == len(b.funcs) and all(same(x, y) for x, y in zip(a.funcs, b.funcs))
    return False


def opt_same(a, b):
    if a is None or b is None:
        return a is None and b is None
    return same(a, b)
