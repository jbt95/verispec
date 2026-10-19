# The VeriSpec adapter SDK for Rust

`verispec-adapter` is the Rust side of VeriSpec: the trait an adapter
implements, the runtime the generated test uses, and the trace recorder that
writes the canonical trace JSON.

```console
$ cargo add verispec-adapter
$ cargo add --dev serde_json
```

The crate has one dependency, `serde_json`. The generated test needs no
property-testing crate: the random driver is a deterministic 64 bit LCG
written into the generated file, so a failing run is reproducible and
nothing reaches the network.

## The adapter contract

```rust
use serde_json::Value;
use std::collections::HashMap;
use verispec_adapter::VeriSpecAdapter;

pub trait VeriSpecAdapter {
    /// Put the implementation in the initial state of the spec.
    fn reset(&mut self);
    /// Apply one action.  The generated test only calls an action whose
    /// preconditions hold.
    fn apply(&mut self, action: &str, params: &HashMap<String, Value>);
    /// The current state: a JSON object with exactly the spec's variables.
    fn snapshot(&self) -> Value;
}
```

The rules that do not change:

- `snapshot()` returns **every** variable of the spec and nothing else.
- A map is a JSON object with stringified keys, a set and a sequence are JSON
  arrays, a record is a JSON object keyed by field name, and an enum value
  is a string.
- The adapter reports the truth. It does not clamp or repair a value.
- `apply` may panic when a precondition does not hold. The generated test
  only calls enabled actions, so a panic there is a defect in the
  implementation, not a legitimate outcome.

A complete adapter:

```rust
// src/adapter.rs
use serde_json::{json, Value};
use std::collections::HashMap;
use verispec_adapter::VeriSpecAdapter;

pub struct BankAdapter {
    balance_a: i64,
    balance_b: i64,
}

impl BankAdapter {
    pub fn new() -> Self {
        BankAdapter { balance_a: 50, balance_b: 50 }
    }
}

impl VeriSpecAdapter for BankAdapter {
    fn reset(&mut self) {
        self.balance_a = 50;
        self.balance_b = 50;
    }

    fn apply(&mut self, action: &str, params: &HashMap<String, Value>) {
        let amount = params.get("amount").and_then(|v| v.as_i64()).unwrap_or(0);
        match action {
            "transfer_a_to_b" => {
                assert!(amount > 0 && self.balance_a >= amount, "precondition");
                self.balance_a -= amount;
                self.balance_b += amount;
            }
            "transfer_b_to_a" => {
                assert!(amount > 0 && self.balance_b >= amount, "precondition");
                self.balance_b -= amount;
                self.balance_a += amount;
            }
            other => panic!("unknown action {other}"),
        }
    }

    fn snapshot(&self) -> Value {
        json!({ "balance_a": self.balance_a, "balance_b": self.balance_b })
    }
}
```

## Generating the test

```console
$ verispec gen-tests spec.verispec.yaml --lang rust \
    --adapter my_crate::BankAdapter -o tests/verispec_conformance.rs
$ cargo test --test verispec_conformance
```

`--adapter` is the path of the adapter type. The generated factory is

```rust
pub fn veri_make_adapter() -> Box<dyn VeriSpecAdapter> {
    Box::new(my_crate::BankAdapter::new())
}
```

so the type must have a `new()` constructor. Without `--adapter` the factory
is a `todo!()` with an explicit message, and the test fails until you fill
it in.

The generated file:

- transpiles the spec expressions from the same AST the Z3 checker uses, so
  the expected behaviour is the behaviour of the spec and not of a second,
  hand written interpreter;
- drives the adapter with random actions and parameters, and compares the
  state after every step with what the spec prescribes;
- checks every invariant in every state, including the initial one;
- skips an action whose preconditions do not hold, like the other backends;
- is deterministic: the same seed always produces the same sequence of
  actions.

The file also exposes `run_conformance`, so a harness can drive it with
another adapter:

```rust
let result = run_conformance(|| Box::new(BankAdapter::new()));
assert!(result.is_ok(), "{:?}", result.err());
```

## The runtime

The crate exports the `veri_*` helpers the generated code calls. They have
the same names and the same semantics as in the other backends:

| Helper | Meaning |
| --- | --- |
| `veri_get(obj, key)` | Read a field of a JSON object. A missing field reads as `null`. |
| `veri_as_int(v, default)` | Read an integer; a value of the wrong type reads as the default. |
| `veri_as_bool(v, default)` | Read a boolean, with the same fallback. |
| `veri_as_str(v, default)` | Read a string, with the same fallback. |
| `veri_len(v)`, `veri_str_len(s)` | The length of a collection or a string. |
| `veri_map_get(m, k, def)` | Read a map key; a missing key reads as the value type default. |
| `veri_map_set(m, k, v, domain)` | A new map with the key set. A key outside the domain is ignored. |
| `veri_set_add(s, v, domain)` | A new set with the element added. An element outside the domain is ignored. |
| `veri_set_has(s, v)` | Element membership. |
| `veri_seq_get(q, i, def)` | The element at an index, or the default when it is out of range. |
| `veri_seq_append(q, v)` | A new sequence. |
| `veri_keys(m)`, `veri_values(m, def)` | The keys and the values of a map. |
| `veri_contains_key(m, k)`, `veri_contains(l, v)` | Key and element membership. |
| `veri_equal(a, b)`, `veri_value_eq(a, b)` | Deep equality. |

A value of the wrong type never panics: the typed readers fall back to the
default of the type, exactly like the spec says a missing map key reads as
the value type default.

## The trace recorder

`TraceRecorder` writes the canonical trace JSON: a list of
`{"action", "params", "state"}`, with the initial state first.

```rust
use std::collections::HashMap;
use verispec_adapter::TraceRecorder;

let mut trace = TraceRecorder::new();
adapter.reset();
trace.record_init(&adapter.snapshot());

let mut params = HashMap::new();
params.insert("amount".to_string(), serde_json::json!(20));
adapter.apply("transfer_a_to_b", &params);
trace.record("transfer_a_to_b", &params, &adapter.snapshot());

trace.write_to("trace.json")?;
```

```console
$ verispec check-trace spec.verispec.yaml trace.json
trace OK: 2 steps conform to spec Bank
```

The trace is the language agnostic bridge: a third party can replay it
against the spec without running the implementation.

## The conformance suite

This crate is its own conformance suite:

```console
$ cd adapters/rust && cargo test
```

- `tests/bank_conformance.rs` is the *generated* test, produced by
  `verispec gen-tests ../../examples/bank.yaml --lang rust`.
- `tests/conformance.rs` runs it against `BankAdapter` (which must pass) and
  against `BankAdapterBuggy` (which must fail). The two reference adapters
  live in `verispec_adapter::examples`.

A suite that stays green on the broken adapter is not testing anything.

## What this crate does not claim

The runtime mirrors the semantics of the spec format, and the trace recorder
produces the canonical trace format. Neither is a proof. The evidence a
VeriSpec run produces is **conformance-tested**: the implementation agreed
with the spec on the states the run reached, and on nothing more.
