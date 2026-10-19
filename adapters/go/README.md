# VeriSpec adapter for Go

The Go adapter has two halves:

- the **generated conformance test**, which the VeriSpec CLI produces from
  your spec, and
- the **adapter**, three methods that expose your real implementation to it.

There is no third-party dependency: the generated test uses the standard
library only.

## The adapter contract

Go has no keyword arguments, and the generated test lives in a different
package, so the methods are exported and take a map:

| Method | Meaning |
| --- | --- |
| `Reset()` | Put the implementation in the spec's initial state. |
| `Apply(action string, params map[string]any)` | Apply one action of the spec. |
| `Snapshot() map[string]any` | The current state, with exactly the spec's variables. |

The adapter must be constructible with no arguments, and it must not clamp or
repair values: the conformance test is looking for exactly that kind of lie.

## The generated test

```console
$ verispec gen-tests ../../examples/bank.yaml --lang go \
      --package bank_test --adapter verispec.dev/adapters/go/bank.BankAdapter \
      -o bank/bank_verispec_test.go
```

| Flag | Meaning |
| --- | --- |
| `--package` | The package name of the generated file. Use the external test package (`<pkg>_test`) when the file lives next to the code it tests. |
| `--adapter` | `<import path>.<Type>`. The generated file imports it and builds it. |

The generated file contains a `TestVeriSpecConformance` function, so it runs
with the standard runner:

```console
$ go test ./...
```

## The conformance suite

```console
$ make test
```

The suite checks the four things every adapter has to pass:

1. the generated property test passes on the correct adapter,
2. it **fails** on the deliberately broken adapter,
3. the trace recorder writes the canonical format,
4. `verispec check-trace` accepts the recorded trace.

The broken adapter (`bank.BuggyAdapter`) loses one unit on every transfer, so
the conformance test must fail on it. A suite that stays green on it is not
testing anything.

## The trace recorder

The trace format is the canonical JSON list of
`{action, params, state}`. A Go recorder looks like this:

```go
package bank

import (
	"encoding/json"
	"os"
)

type Step struct {
	Action string         `json:"action"`
	Params map[string]any `json:"params"`
	State  map[string]any `json:"state"`
}

// RecordTrace drives the adapter and writes the canonical trace.
func RecordTrace(a *Adapter, actions []struct {
	Name   string
	Params map[string]any
}, path string) error {
	steps := []Step{}
	a.Reset()
	steps = append(steps, Step{Action: "init", Params: map[string]any{}, State: a.Snapshot()})
	for _, act := range actions {
		a.Apply(act.Name, act.Params)
		steps = append(steps, Step{Action: act.Name, Params: act.Params, State: a.Snapshot()})
	}
	data, err := json.MarshalIndent(steps, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}
```

Then:

```console
$ verispec check-trace ../../examples/bank.yaml trace.json
trace OK: 2 steps conform to spec Bank
```

## License

Apache-2.0.
