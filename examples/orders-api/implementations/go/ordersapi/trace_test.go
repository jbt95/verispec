package ordersapi_test

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"verispec.dev/examples/orders-api/ordersapi"
)

// tracePath is the committed trace of this implementation.
const tracePath = "../../../traces/orders-session-go.json"

// TestTheCommittedTraceIsUpToDate regenerates the trace and compares it with
// the committed one, so the two implementations cannot drift apart.
func TestTheCommittedTraceIsUpToDate(t *testing.T) {
	recorder, err := ordersapi.RecordSession()
	if err != nil {
		t.Fatalf("record the session: %v", err)
	}
	data, err := recorder.JSON()
	if err != nil {
		t.Fatalf("write the trace: %v", err)
	}
	committed, err := os.ReadFile(tracePath)
	if err != nil {
		t.Fatalf("read the committed trace: %v", err)
	}
	if !bytes.Equal(data, committed) {
		t.Fatalf("the committed trace is out of date:\n--- committed ---\n%s\n--- recorded ---\n%s",
			committed, data)
	}
}

// TestTheTraceHasTheCanonicalFormat checks the shape of the trace file: a
// list of steps, the first one being the initial state, every state holding
// exactly the variables of the spec.
func TestTheTraceHasTheCanonicalFormat(t *testing.T) {
	recorder, err := ordersapi.RecordSession()
	if err != nil {
		t.Fatalf("record the session: %v", err)
	}
	var steps []map[string]any
	data, err := recorder.JSON()
	if err != nil {
		t.Fatalf("write the trace: %v", err)
	}
	if err := json.Unmarshal(data, &steps); err != nil {
		t.Fatalf("decode the trace %s: %v", data, err)
	}
	if len(steps) != 8 {
		t.Fatalf("expected 8 steps, got %d", len(steps))
	}
	if steps[0]["action"] != "init" {
		t.Fatalf("the first step must be the init step, got %v", steps[0]["action"])
	}
	if params, ok := steps[0]["params"].(map[string]any); !ok || len(params) != 0 {
		t.Fatalf("the init step has no parameters, got %v", steps[0]["params"])
	}
	variables := []string{
		"stock", "reserved", "order_product", "order_quantity",
		"order_state", "order_amount", "paid_keys", "revenue", "orders_created",
	}
	for i, step := range steps {
		state, ok := step["state"].(map[string]any)
		if !ok {
			t.Fatalf("step %d has no state: %v", i, step)
		}
		if len(state) != len(variables) {
			t.Fatalf("step %d holds %d variables, expected %d: %v", i, len(state), len(variables), state)
		}
		for _, name := range variables {
			if _, ok := state[name]; !ok {
				t.Fatalf("step %d does not report %q: %v", i, name, state)
			}
		}
	}
	// The maps are keyed by the stringified id, and the set of paid keys is
	// a sorted array.
	last := steps[len(steps)-1]["state"].(map[string]any)
	paidKeys, ok := last["paid_keys"].([]any)
	if !ok || len(paidKeys) != 2 || paidKeys[0] != "k1" || paidKeys[1] != "k2" {
		t.Fatalf("unexpected paid keys: %v", last["paid_keys"])
	}
	orderState, ok := last["order_state"].(map[string]any)
	if !ok || orderState["0"] != "shipped" || orderState["1"] != "paid" {
		t.Fatalf("unexpected order states: %v", last["order_state"])
	}
}

// TestATraceThatOversellsIsRejected shows that the trace path catches what
// the property test cannot.
//
// The property test only takes actions the spec allows, so it never drives
// the implementation into the illegal path.  A trace does: when the
// implementation reserves more stock than there is, the recorded state
// violates `no_overselling`.
//
// The invariant is checked here the way the spec states it:
//
//	map_get(reserved, 0) <= map_get(stock, 0) and map_get(reserved, 1) <= map_get(stock, 1)
func TestATraceThatOversellsIsRejected(t *testing.T) {
	recorder := ordersapi.NewTraceRecorder(&ordersapi.OversellingOrdersApiAdapter{})
	recorder.Reset()
	recorder.Apply("place_order", []ordersapi.Param{
		{Name: "order_id", Value: 0},
		{Name: "product", Value: 0},
		{Name: "quantity", Value: 4},
	})
	recorder.Apply("reserve", []ordersapi.Param{
		{Name: "order_id", Value: 0},
		{Name: "product", Value: 0},
	})
	recorder.Apply("place_order", []ordersapi.Param{
		{Name: "order_id", Value: 1},
		{Name: "product", Value: 0},
		{Name: "quantity", Value: 4},
	})
	recorder.Apply("reserve", []ordersapi.Param{
		{Name: "order_id", Value: 1},
		{Name: "product", Value: 0},
	})

	data, err := recorder.JSON()
	if err != nil {
		t.Fatalf("write the trace: %v", err)
	}
	var steps []map[string]any
	if err := json.Unmarshal(data, &steps); err != nil {
		t.Fatalf("decode the trace %s: %v", data, err)
	}
	last := steps[len(steps)-1]["state"].(map[string]any)
	stock := last["stock"].(map[string]any)
	reserved := last["reserved"].(map[string]any)
	for _, id := range []string{"0", "1"} {
		if reserved[id].(float64) > stock[id].(float64) {
			return // the invariant is violated, as it must be
		}
	}
	t.Fatalf("the recorded state does not oversell: %v", last)
}

// TestTheTraceIsWrittenToDisk checks that the recorder writes the file the
// way the trace format expects: indented JSON with a trailing newline.
func TestTheTraceIsWrittenToDisk(t *testing.T) {
	recorder, err := ordersapi.RecordSession()
	if err != nil {
		t.Fatalf("record the session: %v", err)
	}
	path := filepath.Join(t.TempDir(), "orders-session.json")
	if err := recorder.Write(path); err != nil {
		t.Fatalf("write the trace: %v", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read the trace: %v", err)
	}
	if !bytes.HasSuffix(data, []byte("\n")) {
		t.Fatal("the trace does not end with a newline")
	}
	if !bytes.HasPrefix(data, []byte("[\n  {\n    \"action\": \"init\",")) {
		t.Fatalf("unexpected trace header: %s", data[:60])
	}
}
