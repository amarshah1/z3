#!/usr/bin/env python3
"""Read a cc_log dump produced by Z3, inline all (let ...) bindings, and
emit a new file containing only assertions whose top-level shape is
(= ...), (distinct ...), or (not (= ...)). Subterms inside those
assertions are emitted in tree form (no let-sharing).

Usage:
  cc_log_inline_lets.py <input.smt2> <output.smt2>
"""
import sys
import z3


def is_eq_diseq_distinct(e: z3.ExprRef) -> bool:
    if z3.is_eq(e):
        return True
    if z3.is_distinct(e):
        return True
    if z3.is_not(e):
        inner = e.children()[0]
        return z3.is_eq(inner)
    return False


# ----- tree-form printer (no let sharing) -----

def print_sort(s: z3.SortRef) -> str:
    return s.sexpr()  # sorts don't produce lets


def print_term_tree(e: z3.ExprRef) -> str:
    """Pretty-print e as an SMT-LIB term, expanding shared subterms (no lets).

    z3's own AstRef.sexpr() introduces lets when it sees DAG sharing; this
    routine forces tree form by walking children explicitly.
    """
    if z3.is_const(e):
        # Variable, function constant, numeral, true/false, bool constant
        return e.sexpr()

    decl = e.decl()
    name = decl.name()
    args = e.children()
    if not args:
        # nullary application
        return e.sexpr()

    # Handle the SMT-LIB indexed-identifier cases the printer needs
    # specially. Most z3 ops have a textual decl name that prints
    # correctly when wrapped as `(<name> <arg>...)`.
    parts = [name] + [print_term_tree(a) for a in args]
    return "(" + " ".join(parts) + ")"


# ----- declaration collection -----

def collect_decls(es):
    """Return (sorts, funcs) used in es. Sorts are SortRef; funcs are FuncDeclRef."""
    seen_sort = {}
    seen_func = {}

    def visit_sort(s):
        if s.get_id() in seen_sort:
            return
        seen_sort[s.get_id()] = s
        # Some sorts are parametric: descend if needed.
        # SMT-LIB sorts in the cc_log dumps are mostly opaque; nothing to recurse into.

    def visit(e):
        # Visit each subterm exactly once.
        stack = [e]
        seen_expr = set()
        while stack:
            n = stack.pop()
            if n.get_id() in seen_expr:
                continue
            seen_expr.add(n.get_id())
            try:
                visit_sort(n.sort())
            except z3.Z3Exception:
                pass
            d = n.decl()
            try:
                seen_func.setdefault(d.get_id(), d)
                # Function arity sorts
                for i in range(d.arity()):
                    visit_sort(d.domain(i))
                visit_sort(d.range())
            except z3.Z3Exception:
                pass
            for c in n.children():
                stack.append(c)

    for e in es:
        visit(e)
    return list(seen_sort.values()), list(seen_func.values())


def main():
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    src, dst = sys.argv[1], sys.argv[2]

    print(f"parsing {src} ...", file=sys.stderr, flush=True)
    asserts = z3.parse_smt2_file(src)
    print(f"  {len(asserts)} assertions parsed", file=sys.stderr, flush=True)

    kept = [a for a in asserts if is_eq_diseq_distinct(a)]
    print(f"  {len(kept)} assertions kept (eq/diseq/distinct)", file=sys.stderr, flush=True)

    sorts, funcs = collect_decls(kept)
    # Filter out built-in sorts/funcs (Bool, Int, Real, etc., and operators
    # like =, distinct, +, etc.). Heuristic: anything from a declared kind
    # that has no associated declare-* line is built-in. Z3's
    # FuncDeclRef.range().kind() and decl().kind() can be queried; the
    # simplest filter is "name is uppercase Bool/Int/Real/Array/Seq/etc"
    # for sorts, and "decl is uninterpreted" for funcs.

    builtin_sorts = {"Bool", "Int", "Real"}
    sort_decls = []
    for s in sorts:
        name = s.name() if hasattr(s, "name") else str(s)
        if str(name) in builtin_sorts:
            continue
        if s.kind() == z3.Z3_UNINTERPRETED_SORT:
            sort_decls.append(s)

    func_decls = []
    for f in funcs:
        if f.kind() == z3.Z3_OP_UNINTERPRETED:
            func_decls.append(f)

    print(f"  {len(sort_decls)} sort decls, {len(func_decls)} fun decls",
          file=sys.stderr, flush=True)

    print(f"writing {dst} ...", file=sys.stderr, flush=True)
    with open(dst, "w") as fh:
        fh.write("(set-info :source |cc_log dump with lets inlined and "
                 "boolean structure dropped|)\n")
        for s in sort_decls:
            fh.write(f"(declare-sort {s.sexpr()} 0)\n")
        for f in func_decls:
            params = " ".join(f.domain(i).sexpr() for i in range(f.arity()))
            fh.write(f"(declare-fun {f.name()} ({params}) {f.range().sexpr()})\n")
        for k in kept:
            fh.write(f"(assert {print_term_tree(k)})\n")
        fh.write("(check-sat)\n")
    print("done", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
