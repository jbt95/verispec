//! The VeriSpec conformance suite for the Rust adapter SDK.
//!
//! The suite runs the *generated* conformance test (the file
//! `tests/bank_conformance.rs`, produced by
//! `verispec gen-tests examples/bank.yaml --lang rust`) against two
//! adapters:
//!
//! * `BankAdapter`, which implements the spec, and
//! * `BankAdapterBuggy`, which loses one unit on every transfer.
//!
//! The first must pass, the second must fail.  A generator that cannot tell
//! the two apart is not evidence of anything.

use verispec_adapter::examples::{BankAdapter, BankAdapterBuggy};
use verispec_adapter::VeriSpecAdapter;

/// The generated conformance test.
#[path = "bank_conformance.rs"]
mod generated;

#[test]
fn the_correct_adapter_passes_the_conformance_test() {
    let result = generated::run_conformance(|| Box::new(BankAdapter::new()));
    assert!(
        result.is_ok(),
        "the correct adapter must pass the conformance test: {:?}",
        result.err()
    );
}

#[test]
fn the_buggy_adapter_fails_the_conformance_test() {
    let result = generated::run_conformance(|| Box::new(BankAdapterBuggy::new()));
    assert!(
        result.is_err(),
        "the buggy adapter must be detected by the conformance test"
    );
    let message = result.err().unwrap();
    assert!(
        message.contains("the spec says") || message.contains("violated"),
        "the failure must say what went wrong, got: {}",
        message
    );
}

#[test]
fn the_generated_test_uses_the_sdk() {
    // The generated file drives the adapter through the SDK trait, so the
    // adapter is reachable from the generated code.
    let mut adapter = BankAdapter::new();
    adapter.reset();
    assert_eq!(
        adapter.snapshot().get("balance_a").and_then(|v| v.as_i64()),
        Some(50)
    );
}
