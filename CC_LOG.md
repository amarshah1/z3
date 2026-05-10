# Pure-CC benchmark dumping (`smt.cc_log`)

This fork adds a pair of options to z3's legacy SMT core that dump
**pure congruence-closure** SMT-LIB benchmarks while solving any input
problem. The dumps contain only the SAT trail's
equality / disequality / `distinct` literals, with all other Boolean
structure dropped.

The intended use is to harvest realistic CC stress benchmarks from
real-world solver runs (Verus / SMT-COMP / etc.) for evaluating
standalone congruence-closure implementations.

## Options

Both are smt-module symbol options:

| Option | Default | Meaning |
|---|---|---|
| `smt.cc_log` | `""` (disabled) | Directory where dumped `cc_<N>.smt2` files are written. The directory must already exist; z3 does not create it. |
| `smt.cc_log_mode` | `instantiation` | When to dump. Possible values: `conflict`, `instantiation`, `both`. |

### Modes

- **`conflict`** — dump on every entry to `context::resolve_conflict()`.
  This fires on *every* SAT conflict — including pure-Boolean conflicts
  that have nothing to do with CC. Expect thousands of dumps per source
  benchmark. Many dumps will be tiny.
- **`instantiation`** *(default)* — dump on every entry to
  `context::final_check()`. By that point the SAT solver has fully
  propagated everything (including the literals introduced by the most
  recent quantifier-instantiation round), so the trail at this moment
  reflects the full equality fragment of the model the solver almost
  has. Lower-frequency, larger dumps.
- **`both`** — fire on both events.

## Usage

Build z3 from this branch (standard CMake build, no extra flags):

```bash
mkdir -p build && cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
make -j$(nproc) shell
```

Then point the solver at any SMT-LIB file:

```bash
mkdir -p /tmp/cc_dumps
./z3 -smt2 \
     smt.cc_log=/tmp/cc_dumps \
     smt.cc_log_mode=instantiation \
     -T:600 \
     <input.smt2>
```

Output files: `/tmp/cc_dumps/cc_1.smt2`, `/tmp/cc_dumps/cc_2.smt2`, ...
Each contains:

```text
(set-info :source |z3 cc_log dump (...), trail size=..., filtered=...|)
(declare-sort ...)
(declare-fun ...)
...
(assert (= ...))
(assert (not (= ...)))
(assert (distinct ...))
...
(check-sat)
```

The set of `(declare-sort ...)` / `(declare-fun ...)` lines is the
transitive closure of the symbols that appear in the kept assertions,
collected via z3's existing `ast_pp_util`.

## What gets kept

For each literal `lit` on the SAT trail:

```text
let e = literal2expr(lit)
let atom = is_not(e) ? e.arg(0) : e
keep iff:
    atom is (= a b)            // equality
  | atom is (distinct ts...)   // distinct (only positive polarity)
```

Anything else — Bool-valued UF applications, theory atoms,
quantifiers, ITEs — is dropped. This deliberately makes the dump
**weaker** than the full in-solver state: the dumped file is not
guaranteed unsat even when the solver is in conflict, because the
dropped literals may have been the load-bearing ones. The dump
represents the *equality fragment* of the trail at the dump moment.

## Caveats

1. **Lets are preserved.** z3's pretty-printer re-introduces `(let
   ...)` bindings to share common subterms. If a downstream consumer
   needs let-free files, post-process with the bundled
   [`scripts/cc_log_inline_lets.py`](scripts/cc_log_inline_lets.py),
   which uses the z3 Python bindings to parse a dumped file, walk
   the AST in tree form, and re-emit it without lets. Usage:

   ```bash
   pip install z3-solver
   python3 scripts/cc_log_inline_lets.py <input.smt2> <output.smt2>
   ```

   The expanded file can be 5-10x larger than the input on benchmarks
   with heavy subterm sharing.

2. **Theory equalities are mixed in.** z3's E-graph receives equalities
   from `theory_arith`, `theory_array`, `theory_bv`, `theory_datatype`,
   `theory_seq`, etc. via `propagate_eq`/`assign_eq`. These show up on
   the SAT trail as ordinary `(= ...)` atoms and end up in the dump.
   That's *not* the pure-CC fragment in the strict sense; it's "every
   equality the solver knows about, regardless of which theory derived
   it". For Verus / SMT-COMP inputs that's usually what you want.

3. **Disk pressure.** With `cc_log_mode=instantiation` and a 600s
   timeout, runs on hard Verus benchmarks have produced 4,000+ files
   per source totaling ~8 GB. Use a fast scratch filesystem with
   plenty of space.

## Implementation

The patch is small, ~150 lines across:

- `src/params/smt_params_helper.pyg` — adds the two SYMBOL options.
- `src/params/smt_params.{h,cpp}` — fields `m_cc_log` and `m_cc_log_mode`,
  read by `smt_params::updt_local_params`.
- `src/smt/smt_context.h` — new method `dump_cc_state(const char* tag)`
  and a counter `m_cc_log_counter`.
- `src/smt/smt_context_pp.cpp` — implementation of `dump_cc_state`,
  modeled on the existing `display_lemma_as_smt_problem`.
- `src/smt/smt_context.cpp` — call sites at the top of
  `final_check()` and the top of `resolve_conflict()`.

The `final_check`-mode hook fires once per "complete SAT model"
moment. The first call's trail has no quantifier-derived equalities;
subsequent calls' trails grow by whatever the most recent
instantiation round propagated. So later dumps in a run are typically
larger than earlier ones.
