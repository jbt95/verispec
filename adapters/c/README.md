# The VeriSpec adapter SDK for C

`verispec.h` and `verispec.c` are the C side of VeriSpec: the runtime the
generated test uses, a small JSON codec, and the trace recorder that writes
the canonical trace JSON.

There is nothing to install: copy `include/verispec.h` and `src/verispec.c`
into your project, or point the compiler at them.

```console
$ cc -std=c11 -Iadapters/c/include -o my_test my_test.c my_adapter.c adapters/c/src/verispec.c
```

## The adapter contract

The C adapter supports **scalar specs only**: `int`, `bool`, `str` and
`enum[...]`. A spec with a map, a set, a sequence or a record needs the Rust
adapter; the C generator refuses such a spec with a clear error instead of
mistranslating it.

An adapter provides three functions:

```c
#include "verispec.h"

/* Put the implementation in the initial state of the spec. */
void veri_reset(void);

/* Apply one action.  params_json is a JSON object with the action's
 * parameters, for example {"amount": 20}. */
void veri_apply(const char *action, const char *params_json);

/* Write the current state as a JSON object with exactly the spec's
 * variables, for example {"balance_a":50,"balance_b":50}. */
void veri_snapshot(char *out, size_t out_len);
```

The rules that do not change:

- `veri_snapshot` writes **every** variable of the spec and nothing else.
- A map is a JSON object with stringified keys, a set and a sequence are
  JSON arrays, a record is a JSON object keyed by field name, and an enum
  value is a string.
- The adapter reports the truth. It does not clamp or repair a value.
- `veri_apply` may report a failed precondition. The generated test only
  calls enabled actions, so a failure there is a defect in the
  implementation, not a legitimate outcome.

A complete adapter:

```c
/* bank_adapter.c */
#include <stdio.h>
#include <string.h>

#include "verispec.h"

static long long balance_a = 50;
static long long balance_b = 50;

void veri_reset(void) {
    balance_a = 50;
    balance_b = 50;
}

void veri_apply(const char *action, const char *params_json) {
    long long amount = 0;
    veri_value *params = veri_json_parse(params_json);
    if (params != NULL) {
        const veri_value *value = veri_object_get(params, "amount");
        if (value != NULL && value->kind == VERI_INT) {
            amount = value->i;
        }
        veri_value_free(params);
    }
    if (strcmp(action, "transfer_a_to_b") == 0) {
        if (!(amount > 0 && balance_a >= amount)) {
            fprintf(stderr, "precondition failed\n");
            return;
        }
        balance_a -= amount;
        balance_b += amount;
    } else {
        fprintf(stderr, "unknown action %s\n", action);
    }
}

void veri_snapshot(char *out, size_t out_len) {
    snprintf(out, out_len, "{\"balance_a\":%lld,\"balance_b\":%lld}", balance_a, balance_b);
}
```

The types of the spec map to C like this:

| Spec type | C type |
| --- | --- |
| `int` | `long long` |
| `bool` | `int` (`0` or `1`) |
| `str` | `const char *` |
| `enum[...]` | `const char *` |

## Generating the test

```console
$ verispec gen-tests spec.verispec.yaml --lang c -o conformance/verispec_conformance.c
$ cc -std=c11 -Iadapters/c/include -o conformance/verispec_conformance \
    conformance/verispec_conformance.c my_adapter.c adapters/c/src/verispec.c
$ ./conformance/verispec_conformance
verispec conformance: OK
```

The generated file:

- transpiles the spec expressions from the same AST the Z3 checker uses, so
  the expected behaviour is the behaviour of the spec and not of a second,
  hand written interpreter;
- drives the adapter with random actions and parameters, and compares the
  state after every step with what the spec prescribes;
- checks every invariant in every state, including the initial one;
- skips an action whose preconditions do not hold, like the other backends;
- is deterministic: the same seed always produces the same sequence of
  actions, so a failure is reproducible.

The generated file declares a default `veri_make_adapter()` that does
nothing, so the conformance run calls `veri_reset()` before it uses the
adapter. If your adapter needs a setup step, edit that body. If you provide
your own `veri_make_adapter()` in another translation unit, compile the
generated file with `-DVERISPEC_NO_DEFAULT_FACTORY` so the two do not clash.

The `main()` reports the first mismatch on stderr and exits with `1`, so the
binary can be used directly in CI.

## The runtime

`verispec.h` exports the `veri_*` helpers the generated code calls. They
have the same names and the same semantics as in the other backends:

| Helper | Meaning |
| --- | --- |
| `veri_len(v)`, `veri_str_len(s)` | The length of a collection or a string. |
| `veri_map_get(m, k, def)` | Read a map key; a missing key reads as the value type default. |
| `veri_map_set(m, k, v, domain)` | A new map with the key set. A key outside the domain is ignored. |
| `veri_set_add(s, v, domain)` | A new set with the element added. An element outside the domain is ignored. |
| `veri_set_has(s, v)` | Element membership. |
| `veri_seq_get(q, i, def)` | The element at an index, or the default when it is out of range. |
| `veri_seq_append(q, v)` | A new sequence. |
| `veri_keys(m)`, `veri_values(m, def)` | The keys and the values of a map. |
| `veri_contains_key(m, k)`, `veri_contains(l, v)` | Key and element membership. |
| `veri_floordiv(a, b)`, `veri_mod(a, b)` | Floor division and the remainder, like the spec language. |
| `veri_json_parse(text)`, `veri_json_write(v)` | The JSON codec. |

The `veri_value` variant is public, so an adapter can read a parsed value
directly (`value->i`, `value->s`, `value->kind`).

## The trace recorder

`veri_trace` writes the canonical trace JSON: a list of
`{"action", "params", "state"}`, with the initial state first.

```c
veri_trace *trace = veri_trace_new();
char state[256];

veri_reset();
veri_snapshot(state, sizeof state);
veri_trace_record(trace, "init", "{}", state);

veri_apply("transfer_a_to_b", "{\"amount\": 20}");
veri_snapshot(state, sizeof state);
veri_trace_record(trace, "transfer_a_to_b", "{\"amount\": 20}", state);

veri_trace_write(trace, "trace.json");
veri_trace_free(trace);
```

```console
$ verispec check-trace spec.verispec.yaml trace.json
trace OK: 2 steps conform to spec Bank
```

The trace is the language agnostic bridge: a third party can replay it
against the spec without running the implementation.

## The conformance suite

```console
$ cd adapters/c && make test
```

- `tests/test_bank.c` is the *generated* test, produced by
  `verispec gen-tests ../../examples/bank.yaml --lang c`.
- The Makefile builds it twice: against `tests/bank_adapter.c` (which must
  pass) and against `tests/bank_adapter_buggy.c` (which must fail). The
  broken adapter loses one unit on every transfer, so the total is not
  conserved.
- `tests/test_trace.c` checks that the trace recorder writes the canonical
  format.

A suite that stays green on the broken adapter is not testing anything.

## What this SDK does not claim

The runtime mirrors the semantics of the spec format, and the trace recorder
produces the canonical trace format. Neither is a proof. The evidence a
VeriSpec run produces is **conformance-tested**: the implementation agreed
with the spec on the states the run reached, and on nothing more.
