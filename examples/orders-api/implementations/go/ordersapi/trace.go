package ordersapi

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// stateKeyOrder is the order of the spec's variables.
//
// The canonical trace format writes the members of a state object in this
// order, which is the order the spec declares them in.  Go's encoding/json
// sorts the members of a map, so the state is written through an ordered
// object instead: the trace of this implementation has to be byte for byte
// the trace of the Python one.
var stateKeyOrder = []string{
	"stock",
	"reserved",
	"order_product",
	"order_quantity",
	"order_state",
	"order_amount",
	"paid_keys",
	"revenue",
	"orders_created",
}

// Param is one parameter of an action.
type Param struct {
	// Name is the parameter name, as the spec declares it.
	Name string
	// Value is the value of the parameter.
	Value any
}

// orderedObject is a JSON object that keeps the order of its members.
type orderedObject struct {
	keys   []string
	values map[string]any
}

// newOrderedObject returns an empty ordered object.
func newOrderedObject() *orderedObject {
	return &orderedObject{keys: []string{}, values: map[string]any{}}
}

// Set adds a member, or replaces the value of a member that is already there.
func (o *orderedObject) Set(key string, value any) {
	if _, ok := o.values[key]; !ok {
		o.keys = append(o.keys, key)
	}
	o.values[key] = value
}

// MarshalJSON writes the members in the order they were added.
func (o *orderedObject) MarshalJSON() ([]byte, error) {
	var buf bytes.Buffer
	buf.WriteByte('{')
	for i, key := range o.keys {
		if i > 0 {
			buf.WriteByte(',')
		}
		name, err := marshalJSONValue(key)
		if err != nil {
			return nil, err
		}
		buf.Write(name)
		buf.WriteByte(':')
		value, err := marshalJSONValue(o.values[key])
		if err != nil {
			return nil, err
		}
		buf.Write(value)
	}
	buf.WriteByte('}')
	return buf.Bytes(), nil
}

// marshalJSONValue writes one value as compact JSON.
//
// The HTML escaping of encoding/json is turned off, so the output is the same
// as the one of the Python implementation, character for character.
func marshalJSONValue(value any) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimRight(buf.Bytes(), "\n"), nil
}

// stateObject writes a state in the canonical order.
func stateObject(state map[string]any) *orderedObject {
	out := newOrderedObject()
	for _, key := range stateKeyOrder {
		value, ok := state[key]
		if !ok {
			panic(fmt.Sprintf("the adapter did not report the variable %q", key))
		}
		out.Set(key, value)
	}
	return out
}

// TraceStep is one step of a trace.
type TraceStep struct {
	// Action is the name of the action, or "init" for the first step.
	Action string `json:"action"`
	// Params are the parameters the action was called with.
	Params *orderedObject `json:"params"`
	// State is the full state after the step.
	State *orderedObject `json:"state"`
}

// TraceRecorder records the steps an adapter takes, in the canonical trace
// format.
//
// The recorder calls the adapter and records the state it reports, so the
// trace cannot drift from what the implementation did.
type TraceRecorder struct {
	adapter VeriSpecAdapter
	steps   []TraceStep
}

// NewTraceRecorder returns a recorder over the given adapter.
func NewTraceRecorder(adapter VeriSpecAdapter) *TraceRecorder {
	return &TraceRecorder{adapter: adapter}
}

// Steps returns the recorded steps.
func (r *TraceRecorder) Steps() []TraceStep { return r.steps }

// Reset resets the adapter and records the initial state.
func (r *TraceRecorder) Reset() {
	r.adapter.Reset()
	r.steps = []TraceStep{{
		Action: "init",
		Params: newOrderedObject(),
		State:  stateObject(r.adapter.Snapshot()),
	}}
}

// Apply applies an action through the adapter and records the step.
func (r *TraceRecorder) Apply(action string, params []Param) {
	calls := map[string]any{}
	ordered := newOrderedObject()
	for _, p := range params {
		calls[p.Name] = p.Value
		ordered.Set(p.Name, p.Value)
	}
	r.adapter.Apply(action, calls)
	r.steps = append(r.steps, TraceStep{
		Action: action,
		Params: ordered,
		State:  stateObject(r.adapter.Snapshot()),
	})
}

// JSON returns the trace as a JSON document.
func (r *TraceRecorder) JSON() ([]byte, error) {
	data, err := json.MarshalIndent(r.steps, "", "  ")
	if err != nil {
		return nil, fmt.Errorf("write the trace: %w", err)
	}
	return append(data, '\n'), nil
}

// Write writes the trace to a file.
func (r *TraceRecorder) Write(path string) error {
	data, err := r.JSON()
	if err != nil {
		return err
	}
	if dir := filepath.Dir(path); dir != "" {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return fmt.Errorf("create %s: %w", dir, err)
		}
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	return nil
}

// SessionStep is one action of a recorded session.
type SessionStep struct {
	// Action is the name of the action.
	Action string
	// Params are the parameters, in the order they are written in the trace.
	Params []Param
}

// CanonicalSession is the session the committed trace records.
//
// It is the same session the Python implementation records, step for step.
func CanonicalSession() []SessionStep {
	return []SessionStep{
		{Action: "place_order", Params: []Param{
			{Name: "order_id", Value: 0},
			{Name: "product", Value: 0},
			{Name: "quantity", Value: 3},
		}},
		{Action: "reserve", Params: []Param{
			{Name: "order_id", Value: 0},
			{Name: "product", Value: 0},
		}},
		{Action: "pay", Params: []Param{
			{Name: "order_id", Value: 0},
			{Name: "key", Value: "k1"},
		}},
		{Action: "ship", Params: []Param{
			{Name: "order_id", Value: 0},
		}},
		{Action: "place_order", Params: []Param{
			{Name: "order_id", Value: 1},
			{Name: "product", Value: 1},
			{Name: "quantity", Value: 1},
		}},
		{Action: "reserve", Params: []Param{
			{Name: "order_id", Value: 1},
			{Name: "product", Value: 1},
		}},
		{Action: "pay", Params: []Param{
			{Name: "order_id", Value: 1},
			{Name: "key", Value: "k2"},
		}},
	}
}

// RecordSession records the canonical session with a fresh adapter.
func RecordSession() (*TraceRecorder, error) {
	recorder := NewTraceRecorder(NewOrdersApiAdapter())
	recorder.Reset()
	for _, step := range CanonicalSession() {
		recorder.Apply(step.Action, step.Params)
	}
	return recorder, nil
}
