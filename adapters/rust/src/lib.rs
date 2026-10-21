//! The VeriSpec adapter SDK for Rust.
//!
//! A VeriSpec adapter connects a real implementation to a human owned spec.
//! The contract is three methods:
//!
//! ```ignore
//! impl VeriSpecAdapter for MyAdapter {
//!     fn reset(&mut self);
//!     fn apply(&mut self, action: &str, params: &HashMap<String, Value>);
//!     fn snapshot(&self) -> Value;   // a JSON object with exactly the spec's variables
//! }
//! ```
//!
//! This crate provides:
//!
//! * [`VeriSpecAdapter`], the trait above;
//! * the `veri_*` runtime helpers, the exact semantics of the spec format
//!   (a missing map key reads as the value type default, a `map_set` with a
//!   key outside the declared domain is ignored, ...);
//! * [`TraceRecorder`], which writes the canonical VeriSpec trace JSON;
//! * [`examples`], two reference adapters used by the conformance suite.
//!
//! The generated conformance test
//! (`verispec gen-tests spec.verispec.yaml --lang rust -o tests/spec.rs`)
//! needs this crate and `serde_json`.
//!
//! # What this crate does not claim
//!
//! The helpers mirror the semantics of the spec format, and the trace
//! recorder produces the canonical trace format.  Neither of them is a proof:
//! the evidence a VeriSpec run produces is *conformance-tested*, never
//! "proven".

use serde_json::{Map, Value};
use std::collections::HashMap;
use std::fs;
use std::path::Path;

/// The adapter contract.
///
/// An adapter exposes the state of a real implementation in the shape the
/// spec declares: `snapshot` returns a JSON object whose keys are exactly the
/// state variables of the spec, and nothing else.
pub trait VeriSpecAdapter {
    /// Put the implementation in the initial state of the spec.
    fn reset(&mut self);

    /// Apply one action.  The generated tests only call an action whose
    /// preconditions hold, so an adapter may assume the action is enabled.
    fn apply(&mut self, action: &str, params: &HashMap<String, Value>);

    /// The current state: a JSON object with exactly the spec's variables.
    fn snapshot(&self) -> Value;
}

/// The null value, used when a field is missing.
const VERI_NULL: Value = Value::Null;

// --------------------------------------------------------------------------
// reading the state
// --------------------------------------------------------------------------

/// Read a field of a JSON object.
///
/// A missing field reads as `Value::Null`; a value of the wrong type is
/// handled by the typed readers below, which fall back to the default of the
/// type instead of panicking.
pub fn veri_get<'a>(obj: &'a Value, key: &str) -> &'a Value {
    match obj {
        Value::Object(map) => map.get(key).unwrap_or(&VERI_NULL),
        _ => &VERI_NULL,
    }
}

/// Read an integer.  A value that is not an integer reads as `default`.
pub fn veri_as_int(v: &Value, default: i64) -> i64 {
    match v {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).unwrap_or(default),
        _ => default,
    }
}

/// Read a boolean.  A value that is not a boolean reads as `default`.
pub fn veri_as_bool(v: &Value, default: bool) -> bool {
    v.as_bool().unwrap_or(default)
}

/// Read a string.  A value that is not a string reads as `default`.
pub fn veri_as_str(v: &Value, default: &str) -> String {
    match v {
        Value::String(s) => s.clone(),
        _ => default.to_string(),
    }
}

/// Read any value, unchanged.
pub fn veri_as_value(v: &Value) -> Value {
    v.clone()
}

// --------------------------------------------------------------------------
// arithmetic
// --------------------------------------------------------------------------

/// Floor division, like the `//` of the spec language.
pub fn veri_floordiv(a: i64, b: i64) -> i64 {
    if b == 0 {
        return 0;
    }
    let q = match a.checked_div(b) {
        Some(q) => q,
        None => return 0,
    };
    let r = a % b;
    if r != 0 && ((r < 0) != (b < 0)) {
        q - 1
    } else {
        q
    }
}

/// The remainder with the sign of the divisor, like the `%` of the spec
/// language.
pub fn veri_mod(a: i64, b: i64) -> i64 {
    if b == 0 {
        return 0;
    }
    let r = match a.checked_rem(b) {
        Some(r) => r,
        None => return 0,
    };
    if r != 0 && ((r < 0) != (b < 0)) {
        r + b
    } else {
        r
    }
}

/// The absolute value.
pub fn veri_abs(a: i64) -> i64 {
    a.saturating_abs()
}

/// The smaller of two integers.
pub fn veri_min(a: i64, b: i64) -> i64 {
    if a < b {
        a
    } else {
        b
    }
}

/// The larger of two integers.
pub fn veri_max(a: i64, b: i64) -> i64 {
    if a > b {
        a
    } else {
        b
    }
}

// --------------------------------------------------------------------------
// collections
// --------------------------------------------------------------------------

/// The length of a collection: an array, an object or a string.
pub fn veri_len(v: &Value) -> i64 {
    match v {
        Value::Array(items) => items.len() as i64,
        Value::Object(map) => map.len() as i64,
        Value::String(s) => s.chars().count() as i64,
        _ => 0,
    }
}

/// The length of a string, in characters.
pub fn veri_str_len(s: &str) -> i64 {
    s.chars().count() as i64
}

/// The string form of a map key or a set element.
///
/// Integer keys are stringified, exactly like the JSON representation of a
/// map in a trace.
pub fn veri_key(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Null => "null".to_string(),
        other => other.to_string(),
    }
}

/// The value of a map key, or the default when the key is absent.
pub fn veri_map_get(map: &Value, key: &str, default: &Value) -> Value {
    match map {
        Value::Object(m) => m.get(key).cloned().unwrap_or_else(|| default.clone()),
        _ => default.clone(),
    }
}

/// How many entries of the map hold this value.
///
/// A slot counts only when it is present *and* holds the value, exactly
/// like the Z3 encoding does.
pub fn veri_count_values(map: &Value, value: &Value) -> i64 {
    match map {
        Value::Object(m) => m.values().filter(|v| *v == value).count() as i64,
        _ => 0,
    }
}

/// Whether two maps describe the same things: the same keys, present or
/// not, slot by slot.
pub fn veri_same_keys(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len() && x.keys().all(|k| y.contains_key(k))
        }
        _ => false,
    }
}

/// A copy of the map with the key set.
///
/// A key outside the declared domain is ignored, exactly like the Z3
/// encoding does, so the two semantics never disagree.
pub fn veri_map_set(map: &Value, key: &str, value: &Value, domain: &[Value]) -> Value {
    let mut out = match map {
        Value::Object(m) => m.clone(),
        _ => Map::new(),
    };
    if !domain.iter().any(|d| veri_key(d) == key) {
        return Value::Object(out);
    }
    out.insert(key.to_string(), value.clone());
    Value::Object(out)
}

/// A copy of the set with the element added.
///
/// An element outside the declared domain is ignored.
pub fn veri_set_add(set: &Value, value: &Value, domain: &[Value]) -> Value {
    let mut items: Vec<Value> = match set {
        Value::Array(a) => a.clone(),
        _ => Vec::new(),
    };
    if !domain.iter().any(|d| veri_key(d) == veri_key(value)) {
        return Value::Array(items);
    }
    if !items.iter().any(|item| veri_equal(item, value)) {
        items.push(value.clone());
    }
    Value::Array(items)
}

/// Whether the set holds the element.
pub fn veri_set_has(set: &Value, value: &Value) -> bool {
    match set {
        Value::Array(items) => items.iter().any(|item| veri_equal(item, value)),
        _ => false,
    }
}

/// The element at an index, or the default when the index is out of range.
pub fn veri_seq_get(seq: &Value, index: i64, default: &Value) -> Value {
    match seq {
        Value::Array(items) => {
            if index < 0 {
                return default.clone();
            }
            items
                .get(index as usize)
                .cloned()
                .unwrap_or_else(|| default.clone())
        }
        _ => default.clone(),
    }
}

/// A copy of the sequence with the element appended.
pub fn veri_seq_append(seq: &Value, value: &Value) -> Value {
    let mut items: Vec<Value> = match seq {
        Value::Array(a) => a.clone(),
        _ => Vec::new(),
    };
    items.push(value.clone());
    Value::Array(items)
}

/// The keys of a map, as a JSON array of strings.
pub fn veri_keys(map: &Value) -> Value {
    match map {
        Value::Object(m) => Value::Array(
            m.keys()
                .map(|k| Value::String(k.clone()))
                .collect::<Vec<Value>>(),
        ),
        _ => Value::Array(Vec::new()),
    }
}

/// The values of a map, in key order.
pub fn veri_values(map: &Value, default: &Value) -> Value {
    match map {
        Value::Object(m) => Value::Array(
            m.values()
                .map(|v| v.clone())
                .collect::<Vec<Value>>(),
        ),
        _ => {
            let _ = default;
            Value::Array(Vec::new())
        }
    }
}

/// Whether the map has the key.
pub fn veri_contains_key(map: &Value, key: &str) -> bool {
    match map {
        Value::Object(m) => m.contains_key(key),
        _ => false,
    }
}

/// Whether the list holds the value.
pub fn veri_contains(list: &Value, value: &Value) -> bool {
    match list {
        Value::Array(items) => items.iter().any(|item| veri_equal(item, value)),
        _ => false,
    }
}

/// The field of a record.
pub fn veri_record_get(record: &Value, field: &str, default: &Value) -> Value {
    match record {
        Value::Object(m) => m.get(field).cloned().unwrap_or_else(|| default.clone()),
        _ => default.clone(),
    }
}

// --------------------------------------------------------------------------
// comparison
// --------------------------------------------------------------------------

/// Deep equality.
///
/// Numbers are compared by value (`1` and `1.0` are equal), arrays element
/// by element and objects key by key, whatever the order of the keys.
pub fn veri_equal(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => match (x.as_f64(), y.as_f64()) {
            (Some(x), Some(y)) => x == y,
            _ => x == y,
        },
        (Value::String(x), Value::String(y)) => x == y,
        (Value::Bool(x), Value::Bool(y)) => x == y,
        (Value::Null, Value::Null) => true,
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y.iter()).all(|(a, b)| veri_equal(a, b))
        }
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len()
                && x.iter().all(|(k, v)| match y.get(k) {
                    Some(other) => veri_equal(v, other),
                    None => false,
                })
        }
        _ => false,
    }
}

/// Deep equality of two values, the name the generated code uses.
pub fn veri_value_eq(a: &Value, b: &Value) -> bool {
    veri_equal(a, b)
}

// --------------------------------------------------------------------------
// traces
// --------------------------------------------------------------------------

/// One step of a trace.
#[derive(Clone, Debug)]
pub struct TraceStep {
    /// The action that was applied, or `"init"` for the first step.
    pub action: String,
    /// The parameters of the action.
    pub params: Value,
    /// The state after the step.
    pub state: Value,
}

/// Records the steps of a run in the canonical VeriSpec trace format.
///
/// The canonical format is a JSON list of
/// `{"action": ..., "params": {...}, "state": {...}}`, and the first entry is
/// the initial state:
///
/// ```ignore
/// let mut trace = TraceRecorder::new();
/// adapter.reset();
/// trace.record_init(&adapter.snapshot());
/// adapter.apply("deposit", &params);
/// trace.record("deposit", &params, &adapter.snapshot());
/// trace.write_to("trace.json")?;
/// ```
///
/// The file is the language agnostic bridge: `verispec check-trace` decides
/// whether the run honours the contract.
#[derive(Clone, Debug, Default)]
pub struct TraceRecorder {
    steps: Vec<TraceStep>,
}

impl TraceRecorder {
    /// An empty recorder.
    pub fn new() -> Self {
        TraceRecorder { steps: Vec::new() }
    }

    /// Record one step.
    pub fn record(&mut self, action: &str, params: &HashMap<String, Value>, state: &Value) {
        let mut map = Map::new();
        for (key, value) in params {
            map.insert(key.clone(), value.clone());
        }
        self.steps.push(TraceStep {
            action: action.to_string(),
            params: Value::Object(map),
            state: state.clone(),
        });
    }

    /// Record the initial state, the first entry of a canonical trace.
    pub fn record_init(&mut self, state: &Value) {
        self.record("init", &HashMap::new(), state);
    }

    /// The trace as a JSON list.
    pub fn to_json(&self) -> String {
        let list: Vec<Value> = self
            .steps
            .iter()
            .map(|step| {
                let mut map = Map::new();
                map.insert("action".to_string(), Value::String(step.action.clone()));
                map.insert("params".to_string(), step.params.clone());
                map.insert("state".to_string(), step.state.clone());
                Value::Object(map)
            })
            .collect();
        serde_json::to_string(&list).unwrap_or_else(|_| "[]".to_string())
    }

    /// Write the trace to a file.
    pub fn write_to(&self, path: impl AsRef<Path>) -> std::io::Result<()> {
        fs::write(path, self.to_json())
    }

    /// How many steps the trace holds.
    pub fn len(&self) -> usize {
        self.steps.len()
    }

    /// Whether the trace is empty.
    pub fn is_empty(&self) -> bool {
        self.steps.is_empty()
    }

    /// The steps of the trace.
    pub fn steps(&self) -> &[TraceStep] {
        &self.steps
    }
}

// --------------------------------------------------------------------------
// reference adapters
// --------------------------------------------------------------------------

/// Two reference adapters, used by the conformance suite of this crate.
///
/// They implement the `Bank` spec of the VeriSpec distribution: two accounts
/// and a transfer that conserves the total.  `BankAdapter` is correct and
/// `BankAdapterBuggy` loses one unit on every transfer, so the conformance
/// test detects it.
pub mod examples {
    use super::*;

    /// A correct adapter for the `Bank` spec.
    #[derive(Clone, Debug)]
    pub struct BankAdapter {
        balance_a: i64,
        balance_b: i64,
    }

    impl Default for BankAdapter {
        fn default() -> Self {
            BankAdapter::new()
        }
    }

    impl BankAdapter {
        /// The initial state of the `Bank` spec.
        pub fn new() -> Self {
            BankAdapter {
                balance_a: 50,
                balance_b: 50,
            }
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
                    self.balance_a -= amount;
                    self.balance_b += amount;
                }
                "transfer_b_to_a" => {
                    self.balance_b -= amount;
                    self.balance_a += amount;
                }
                _ => {}
            }
        }

        fn snapshot(&self) -> Value {
            let mut map = Map::new();
            map.insert("balance_a".to_string(), Value::from(self.balance_a));
            map.insert("balance_b".to_string(), Value::from(self.balance_b));
            Value::Object(map)
        }
    }

    /// A deliberately wrong adapter for the `Bank` spec: it keeps one unit
    /// of every transfer, so the total is not conserved.
    #[derive(Clone, Debug)]
    pub struct BankAdapterBuggy {
        balance_a: i64,
        balance_b: i64,
    }

    impl Default for BankAdapterBuggy {
        fn default() -> Self {
            BankAdapterBuggy::new()
        }
    }

    impl BankAdapterBuggy {
        /// The initial state of the `Bank` spec.
        pub fn new() -> Self {
            BankAdapterBuggy {
                balance_a: 50,
                balance_b: 50,
            }
        }
    }

    impl VeriSpecAdapter for BankAdapterBuggy {
        fn reset(&mut self) {
            self.balance_a = 50;
            self.balance_b = 50;
        }

        fn apply(&mut self, action: &str, params: &HashMap<String, Value>) {
            let amount = params.get("amount").and_then(|v| v.as_i64()).unwrap_or(0);
            match action {
                "transfer_a_to_b" => {
                    self.balance_a -= amount;
                    self.balance_b += amount - 1;
                }
                "transfer_b_to_a" => {
                    self.balance_b -= amount;
                    self.balance_a += amount - 1;
                }
                _ => {}
            }
        }

        fn snapshot(&self) -> Value {
            let mut map = Map::new();
            map.insert("balance_a".to_string(), Value::from(self.balance_a));
            map.insert("balance_b".to_string(), Value::from(self.balance_b));
            Value::Object(map)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_missing_map_key_reads_as_the_default() {
        let map = json!({"0": 7});
        let def = json!(0);
        assert_eq!(veri_map_get(&map, "0", &def), json!(7));
        assert_eq!(veri_map_get(&map, "1", &def), json!(0));
    }

    #[test]
    fn a_key_outside_the_domain_is_ignored() {
        let map = json!({"0": 7});
        let domain = vec![json!(0), json!(1)];
        let out = veri_map_set(&map, "9", &json!(3), &domain);
        assert_eq!(out, json!({"0": 7}));
        let out = veri_map_set(&map, "1", &json!(3), &domain);
        assert_eq!(out, json!({"0": 7, "1": 3}));
    }

    #[test]
    fn count_values_counts_the_entries_that_hold_the_value() {
        let map = json!({"0": "x", "1": "y", "2": "z"});
        assert_eq!(veri_count_values(&map, &json!("z")), 1);
        assert_eq!(veri_count_values(&map, &json!("x")), 1);
        assert_eq!(veri_count_values(&map, &json!("nope")), 0);
        assert_eq!(veri_count_values(&json!({}), &json!("z")), 0);
    }

    fn same_keys_compares_the_key_sets() {
        assert!(veri_same_keys(&json!({"0": "a"}), &json!({"0": "b"})));
        assert!(!veri_same_keys(&json!({"0": "a"}), &json!({})));
        assert!(!veri_same_keys(&json!({"0": "a"}), &json!({"1": "b"})));
        assert!(veri_same_keys(
            &json!({"0": "a", "1": "b"}),
            &json!({"0": "x", "1": "y"})
        ));
    }

    fn a_set_holds_every_element_once() {
        let set = json!(["a"]);
        let domain = vec![json!("a"), json!("b")];
        let out = veri_set_add(&set, &json!("a"), &domain);
        assert_eq!(out, json!(["a"]));
        let out = veri_set_add(&set, &json!("b"), &domain);
        assert_eq!(out, json!(["a", "b"]));
    }

    #[test]
    fn floor_division_follows_the_spec() {
        assert_eq!(veri_floordiv(7, 2), 3);
        assert_eq!(veri_floordiv(-7, 2), -4);
        assert_eq!(veri_mod(-7, 2), 1);
        assert_eq!(veri_mod(7, -2), -1);
    }

    #[test]
    fn a_wrong_type_never_panics() {
        assert_eq!(veri_as_int(&json!("nope"), 3), 3);
        assert_eq!(veri_as_bool(&json!(7), true), true);
        assert_eq!(veri_as_str(&json!(7), "x"), "x");
    }

    #[test]
    fn the_trace_is_the_canonical_format() {
        let mut trace = TraceRecorder::new();
        let mut params = HashMap::new();
        params.insert("amount".to_string(), json!(10));
        trace.record_init(&json!({"balance_a": 50, "balance_b": 50}));
        trace.record("deposit", &params, &json!({"balance_a": 60, "balance_b": 50}));
        let text = trace.to_json();
        let parsed: Value = serde_json::from_str(&text).unwrap();
        let list = parsed.as_array().unwrap();
        assert_eq!(list.len(), 2);
        assert_eq!(list[0]["action"], json!("init"));
        assert_eq!(list[0]["params"], json!({}));
        assert_eq!(list[1]["action"], json!("deposit"));
        assert_eq!(list[1]["params"], json!({"amount": 10}));
    }

    #[test]
    fn the_trace_can_be_written_to_a_file() {
        let mut trace = TraceRecorder::new();
        trace.record_init(&json!({"balance_a": 50, "balance_b": 50}));
        let path = std::env::temp_dir().join("verispec-trace-test.json");
        trace.write_to(&path).unwrap();
        let text = fs::read_to_string(&path).unwrap();
        let parsed: Value = serde_json::from_str(&text).unwrap();
        let list = parsed.as_array().unwrap();
        assert_eq!(list.len(), 1);
        assert_eq!(list[0]["action"], json!("init"));
        assert_eq!(list[0]["state"], json!({"balance_a": 50, "balance_b": 50}));
        let _ = fs::remove_file(&path);
    }
}
