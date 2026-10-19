package bank_test

import (
	"testing"

	bank "verispec.dev/adapters/go/bank"
)

// TestTheBuggyAdapterIsDetected proves the conformance suite has teeth: the
// generated test must FAIL on the broken adapter.
func TestTheBuggyAdapterIsDetected(t *testing.T) {
	old := veriMakeAdapter
	veriMakeAdapter = func() veriAdapter { return &bank.BuggyAdapter{} }
	defer func() { veriMakeAdapter = old }()

	if err := veriRunConformance(); err == nil {
		t.Fatal("the generated conformance test passed on the broken adapter: the suite proves nothing")
	}
}
