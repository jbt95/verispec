package bank_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	bank "verispec.dev/adapters/go/bank"
)

// TestTheTraceRecorderWritesTheCanonicalFormat checks that a trace recorded
// from the adapter is accepted by `verispec check-trace`.
func TestTheTraceRecorderWritesTheCanonicalFormat(t *testing.T) {
	adapter := bank.New()
	adapter.Reset()

	steps := []map[string]any{
		{"action": "init", "params": map[string]any{}, "state": adapter.Snapshot()},
	}
	adapter.Apply("transfer_a_to_b", map[string]any{"amount": 20})
	steps = append(steps, map[string]any{
		"action": "transfer_a_to_b",
		"params": map[string]any{"amount": 20},
		"state":  adapter.Snapshot(),
	})

	data, err := json.MarshalIndent(steps, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "trace.json")
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}

	var parsed []map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	if len(parsed) != 2 {
		t.Fatalf("expected 2 steps, got %d", len(parsed))
	}
	if parsed[0]["action"] != "init" {
		t.Fatalf("the first step must be the init step, got %v", parsed[0]["action"])
	}
	state, _ := parsed[1]["state"].(map[string]any)
	if state["balance_a"] != float64(30) {
		t.Fatalf("unexpected state after the transfer: %v", state)
	}
}
