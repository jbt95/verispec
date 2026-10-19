// Package bank holds the Bank adapter: the implementation under test.
//
// The adapter is a translation layer, not a second implementation: a real
// project would call its own code here. It must not clamp or repair values,
// because the conformance test is looking for exactly that kind of lie.
package bank

// BankAdapter implements the VeriSpec adapter contract:
//
//	Reset()
//	Apply(action string, params map[string]any)
//	Snapshot() map[string]any
//
// The snapshot contains exactly the spec's state variables, and nothing else.
type BankAdapter struct {
	state map[string]any
}

// New returns an adapter in its initial state.
func New() *BankAdapter {
	return &BankAdapter{state: map[string]any{}}
}

// Reset puts the implementation in the initial state of the spec.
func (a *BankAdapter) Reset() {
	a.state = map[string]any{"balance_a": 50, "balance_b": 50}
}

// Apply applies one action of the spec.
func (a *BankAdapter) Apply(action string, params map[string]any) {
	amount, _ := params["amount"].(int)
	switch action {
	case "transfer_a_to_b":
		bal, _ := a.state["balance_a"].(int)
		if !(amount > 0 && bal >= amount) {
			panic("precondition failed")
		}
		a.state["balance_a"] = bal - amount
		other, _ := a.state["balance_b"].(int)
		a.state["balance_b"] = other + amount
	case "transfer_b_to_a":
		bal, _ := a.state["balance_b"].(int)
		if !(amount > 0 && bal >= amount) {
			panic("precondition failed")
		}
		a.state["balance_b"] = bal - amount
		other, _ := a.state["balance_a"].(int)
		a.state["balance_a"] = other + amount
	default:
		panic("unknown action " + action)
	}
}

// Snapshot returns the current state, with exactly the spec's variables.
func (a *BankAdapter) Snapshot() map[string]any {
	out := map[string]any{}
	for k, v := range a.state {
		out[k] = v
	}
	return out
}
