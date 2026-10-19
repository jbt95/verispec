package bank

// BuggyAdapter is the deliberately broken Bank adapter: it leaks one unit on
// every transfer_a_to_b.
//
// The conformance suite must FAIL on it. A suite that stays green on this
// adapter is not testing anything.
type BuggyAdapter struct {
	state map[string]any
}

// NewBuggy returns the broken adapter in its initial state.
func NewBuggy() *BuggyAdapter {
	return &BuggyAdapter{state: map[string]any{}}
}

// Reset puts the implementation in the initial state of the spec.
func (a *BuggyAdapter) Reset() {
	a.state = map[string]any{"balance_a": 50, "balance_b": 50}
}

// Apply applies one action of the spec, losing one unit on every transfer.
func (a *BuggyAdapter) Apply(action string, params map[string]any) {
	amount, _ := params["amount"].(int)
	switch action {
	case "transfer_a_to_b":
		bal, _ := a.state["balance_a"].(int)
		a.state["balance_a"] = bal - amount
		other, _ := a.state["balance_b"].(int)
		a.state["balance_b"] = other + amount - 1
	case "transfer_b_to_a":
		bal, _ := a.state["balance_b"].(int)
		a.state["balance_b"] = bal - amount
		other, _ := a.state["balance_a"].(int)
		a.state["balance_a"] = other + amount
	default:
		panic("unknown action " + action)
	}
}

// Snapshot returns the current state, with exactly the spec's variables.
func (a *BuggyAdapter) Snapshot() map[string]any {
	out := map[string]any{}
	for k, v := range a.state {
		out[k] = v
	}
	return out
}
