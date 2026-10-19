# Writing an adapter

An adapter is the bridge between a VeriSpec spec (a YAML file owned by
humans) and a real implementation (code owned by whoever wrote it). It is the
only language-specific piece of the whole system, and it is deliberately
small.

- [What an adapter is](#what-an-adapter-is)
- [The three methods](#the-three-methods)
- [Mapping the state](#mapping-the-state)
- [The rules](#the-rules)
- [The conformance suite](#the-conformance-suite)
- [Adding a new language](#adding-a-new-language)
- [Common mistakes](#common-mistakes)
- [Review checklist](#review-checklist)

---

## What an adapter is

An adapter exposes a real implementation to the VeriSpec tooling. It does
three things: it puts the implementation in the initial state, it applies one
action, and it reports the current state.

```python
class BankAdapter:
    def reset(self):
        self.state = {"balance_a": 50, "balance_b": 50}

    def apply(self, action, **params):
        amount = params.get("amount", 0)
        if action == "transfer_a_to_b":
            if not (amount > 0 and self.state["balance_a"] >= amount):
                raise ValueError("precondition")
            self.state["balance_a"] -= amount
            self.state["balance_b"] += amount
        elif action == "transfer_b_to_a":
            if not (amount > 0 and self.state["balance_b"] >= amount):
                raise ValueError("precondition")
            self.state["balance_b"] -= amount
            self.state["balance_a"] += amount
        else:
            raise ValueError(action)

    def snapshot(self):
        return dict(self.state)
```

## What an adapter is not

- **It is not a second implementation of the logic.** The adapter should call
  the real code, not reimplement it. If the adapter reimplements the business
  rule, the conformance test proves that the adapter is correct, which is not
  the point.
- **It is not a mock.** A mock that returns whatever the test expects passes
  every test and proves nothing. An adapter that drives the real code and
  reports what it really did is the only thing worth having.
- **It is not a place to hide state.** Every value the spec declares must be
  observable through `snapshot()`. A value the implementation keeps but never
  reports is a value the checker cannot verify.
- **It is not where the invariants live.** The spec holds the invariants. The
  adapter does not enforce them a second time; if it did, the conformance test
  would be checking the adapter against itself.

## The three methods

| Method | Contract |
| --- | --- |
| `reset()` | Put the implementation in exactly the state the spec's `init` block prescribes. |
| `apply(action, params)` | Apply exactly one action of the spec. |
| `snapshot()` | Return the current state, with exactly the spec's variables. |

### `reset()`

After `reset()`, `snapshot()` must equal the spec's initial state, key for
key and value for value. The generated test asserts this before anything
else, because an adapter that starts in the wrong state makes every later
assertion meaningless.

`reset()` is called once per test case, so it must not leak state between
calls.

### `apply(action, params)`

- `action` is the action name, spelled exactly as in the spec.
- `params` carries the action's parameters, keyed by the spec's parameter
  names.
- The generated test only calls an action whose preconditions hold in the
  current state. An action that is not enabled cannot be taken.
- An action the implementation does not know is a programming error in the
  adapter: throw (or panic, or fail the test).
- A precondition that does not hold is a bug in the implementation: throw.

### `snapshot()`

- Returns a map from variable name to value.
- Contains **exactly** the spec's variables: no more, no fewer. A missing
  variable is an error in the trace checker; an extra variable is a state the
  spec does not describe, and the checker rejects it.
- Must be a copy, not a reference to the implementation's internal state, if
  the caller keeps it around.

## Mapping the state

| Spec type | Python | TypeScript | Go | Java | Rust | C |
| --- | --- | --- | --- | --- | --- | --- |
| `int` | `int` | `number` | `int` / `int64` | `Integer` / `Long` | `i64` | `long long` |
| `bool` | `bool` | `boolean` | `bool` | `Boolean` | `bool` | `int` (0 or 1) |
| `str` | `str` | `string` | `string` | `String` | `String` | `const char *` |
| `enum[...]` | `str` | `string` | `string` | `String` | `String` | `const char *` |
| `map` | `dict` | `Record<string, any>` | `map[string]any` | `Map<String, Object>` | `serde_json::Value` object | JSON object |
| `set` | `set` | `any[]` | `[]any` | `Set<Object>` | JSON array | JSON array |
| `seq` | `list` | `any[]` | `[]any` | `List<Object>` | JSON array | JSON array |
| `record` | `dict` | `Record<string, any>` | `map[string]any` | `Map<String, Object>` | JSON object | JSON object |

Two rules that catch people out:

- **Map keys are stringified on the wire.** A map with integer keys is
  `{"0": 10}` in JSON, not `{0: 10}`. The JSON encoder of your language will
  usually do this for you; the trace checker requires it.
- **A set is an array in JSON, and the order does not matter.** The checker
  compares sets as sets. A sequence is an array too, and the order does
  matter.

### A worked example

[`examples/orders-api/`](../examples/orders-api/) has a complete adapter in
three languages, over a REST API with a database.  The Python one is the
reference:

```python
class OrdersApiAdapter:
    def reset(self):
        self.service.reset()

    def apply(self, action, **params):
        if action == "place_order":
            self.service.place_order(params["product"], params["quantity"])
        ...

    def snapshot(self):
        # the state is read back from the database
        return {"stock": {...}, "order_state": {...}, ...}
```

Note what it does *not* do: it does not reimplement the business rules.  It
calls the same service methods the HTTP routes call, and it reads the state
back from the database.  The TypeScript and Go adapters in the same example
do exactly the same thing, and the three record byte-identical traces.

## The rules

**Small.** An adapter is a translation layer, not a place for logic. If the
adapter is longer than the code it wraps, something is wrong: the spec
probably describes the wrong thing, or the implementation has a shape the
spec does not capture.

**Reviewable.** A reviewer must be able to read the adapter and see, without
running it, that it reports the truth. That means: no clever shortcuts, no
state kept outside the implementation, no conditionals that hide a mismatch.

**Honest.** The adapter reports what the implementation actually did. If the
implementation produced `balance_a = -1`, `snapshot()` returns `-1`. An
adapter that clamps, rounds or "fixes" the value is a lie, and it makes the
conformance test worthless.

## The conformance suite

Every adapter must pass the same suite. It has four parts, and the third is
the one people forget.

### 1. The generated property test must pass

```console
$ verispec gen-tests specs/bank.yaml --lang python -o tests/test_bank_verispec.py
$ VERISPEC_ADAPTER=bank_adapter:BankAdapter pytest -q tests/test_bank_verispec.py
.                                                                        [100%]
1 passed in 2.03s
```

The generated test drives the adapter with random action sequences and checks
three things at every step:

1. After `reset()`, the state equals the spec's initial state.
2. After an action, the state equals what the spec's `effects` prescribe.
3. Every state, including the initial one, satisfies every invariant.

Actions whose preconditions do not hold are skipped, not applied: an action
that is not enabled cannot be taken.

### 2. The trace check must pass

Record a trace of the adapter and replay it:

```console
$ verispec check-trace specs/bank.yaml traces/bank-valid.json
trace OK: 3 steps conform to spec Bank
```

In Python, the recorder ships with the tool:

```python
from verispec.model import load_spec
from verispec.sdk import TraceRecorder

spec = load_spec("specs/bank.yaml")
recorder = TraceRecorder(spec, adapter=BankAdapter())
recorder.reset()
recorder.apply("transfer_a_to_b", amount=20)
recorder.write("traces/bank-valid.json")
```

The recorder calls the adapter and records the state it reports, so the trace
cannot drift from what the implementation did.

The trace checker is stricter than the property test in one way: it checks
the *exact* state after every step, including the actions it skips. The
property test checks the same states, but only the ones it happens to
generate.

### 3. The generated property test must FAIL on a broken adapter

This is the part that proves the suite has teeth. Every adapter ships a
deliberately broken variant, and the conformance test must fail on it:

```console
$ VERISPEC_ADAPTER=bank_adapter_buggy:BankAdapterBuggy pytest -q tests/test_bank_verispec.py
E           AssertionError: step 0 action transfer_a_to_b {'amount': 1}: expected effects {'balance_a': 49, 'balance_b': 51}, got {'balance_a': 49, 'balance_b': 50} (prev={'balance_a': 50, 'balance_b': 50})
1 failed in 0.67s
```

The broken adapter leaks one unit on every transfer. A suite that stays green
on it is not testing anything.

The broken adapter must break the contract in a way the spec actually
constrains. Do not make it fail for an unrelated reason (a typo in the
variable name, a missing key) because then a green run of the *correct*
adapter would be the only thing distinguishing them, and a broken harness
would look fine.

The same holds for the trace: keep one conforming trace and one
non-conforming trace, and assert both outcomes.

### 4. The corpus cases must reproduce

The corpus is the source of truth for the format. Every adapter is exercised
against it:

```console
$ verispec corpus corpus --bound 8
ok       bank
ok       bank-buggy
ok       collections
ok       ledger
ok       ledger-buggy
ok       orders-api
ok       trace-invalid
ok       trace-valid
8 case(s), 0 failure(s)
```

A new adapter must register its spec in `corpus/`, so the generated property
tests and the trace conformance checks are exercised in CI.

A case may declare the bound it should be checked at, because the cost of the
search is exponential in the bound:

```json
{
  "bound": 6,
  "check": {"status": "ok", "violated": []},
  "trace": {"ok": true}
}
```

### The suite at a glance

| # | Check | Expected result |
| --- | --- | --- |
| 1 | Generated property test, correct adapter | Pass. |
| 2 | `verispec check-trace` on a recorded trace | `trace OK`. |
| 3 | Generated property test, deliberately broken adapter | **Fail.** |
| 4 | `verispec corpus corpus` | No failures. |

### What the generated test cannot catch

The generated property test only takes actions the spec **allows**: before
each action it checks the preconditions, and skips the action when they do
not hold. That is the right thing to do (an action that is not enabled cannot
be taken), but it has a consequence worth knowing:

> A generated test cannot catch an implementation that *accepts* an action the
> spec forbids, because it never takes that path.

The classic case is a missing precondition: an implementation that reserves
stock without checking that there is stock. The generated test never asks for
more than there is, so the two agree and the test stays green.

Three things cover that case, and an adapter suite should have all of them:

| Check | What it catches |
| --- | --- |
| The implementation's own tests | The endpoint refuses the illegal request (409, 422, ...). |
| A trace of the illegal path | The recorded state violates an invariant; `verispec check-trace` names it. |
| A deterministic comparison against the spec's effects | The implementation's state differs from what the spec says. |

The orders API example
([`examples/orders-api/`](../examples/orders-api/)) has all three: the
overselling implementation is caught by the trace path, and the
"leaks the reservation" implementation is caught by a deterministic
comparison with the spec's own effect functions.

## Adding a new language

Adding a language means adding the pieces the existing languages have. In
order:

### 1. The trace SDK

A small library that:

- defines the adapter interface for the language,
- converts the spec's value types to and from the language's JSON
  representation (stringified map keys, arrays for sets and sequences,
  objects for records),
- writes a trace file in the canonical JSON format.

This is the part that is worth writing first, because it works for every
language and it is what makes the implementation checkable at all. Look at
`adapters/java/verispec-adapter` for the shape: an interface, a value
converter, a JSON writer and a trace recorder.

### 2. A reference adapter and a broken adapter

One adapter that conforms, one that does not, both written against the same
spec. They are the fixtures of the conformance suite.

### 3. A generated test, or an equivalent

Either a generator that emits a property test in the language
(`verispec gen-tests --lang <lang>`), or a hand-written test that follows the
same shape as the generated ones. The generator is better: it stays in sync
with the spec automatically. The hand-written one is acceptable when the
language has no property-testing library, as long as it implements the same
four checks.

If you add a generator, the expression transpiler for the language goes in
`src/verispec/codegen.py` (or `codegen_ext.py`): it turns the spec's
expression AST into the target language, from the same AST the Z3 checker
uses. The transpiler must implement the same semantics, including the
collection functions and the default values.

### 4. Wiring into CI

Add a job that runs the generated test and the trace check for the new
language, and register the adapter's directory in `.github/CODEOWNERS` so
the maintainers of that adapter get the review requests.

### 5. Registering in the corpus

Add a case under `corpus/` with the spec, the trace and the expected
results.

## Common mistakes

| Mistake | What happens | How to avoid |
| --- | --- | --- |
| The adapter reimplements the business logic. | The test passes while the real code is broken. | Call the real code. |
| `snapshot()` returns extra keys. | The trace checker rejects the trace; the property test may not notice. | Return exactly the spec's variables. |
| `snapshot()` returns the internal map by reference. | Later mutations change recorded history. | Return a copy. |
| The adapter clamps out-of-range values. | The defect is hidden and the suite stays green. | Report the truth. |
| The adapter does not check preconditions. | A broken implementation is never noticed, because the test only calls enabled actions. | Enforce the precondition, and throw when it does not hold. |
| Integer map keys are not stringified. | The trace is rejected. | Let the JSON encoder do it, or convert explicitly. |
| The broken adapter fails for the wrong reason. | A broken harness looks like a passing one. | Break the contract the spec actually states. |
| The adapter is 200 lines long. | Nobody reviews it, and it hides a second implementation. | Keep it a translation layer. |

## Review checklist

Use this when reviewing an adapter, in any language.

- [ ] `reset()` produces exactly the spec's initial state.
- [ ] `snapshot()` contains exactly the spec's variables, and nothing else.
- [ ] Action names and parameter names match the spec character for character.
- [ ] The adapter calls the real implementation; it does not reimplement it.
- [ ] The adapter does not clamp, round or repair values.
- [ ] Preconditions are enforced, and a failure throws.
- [ ] Map keys are stringified in the JSON output.
- [ ] A broken variant exists, and the conformance test fails on it.
- [ ] The generated property test passes on the correct adapter.
- [ ] A trace is recorded, and `verispec check-trace` accepts it.
- [ ] A non-conforming trace exists, and `verispec check-trace` rejects it.
- [ ] The adapter's spec is registered in `corpus/`.
- [ ] The adapter directory is in `.github/CODEOWNERS`.
