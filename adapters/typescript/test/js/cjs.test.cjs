/**
 * The CommonJS entry point must work too: a plain JavaScript project that
 * uses `require()` cannot import an ESM-only package.
 */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { TraceRecorder, recordTrace } = require("../../dist/cjs/src/index.js");

test("the CommonJS build exposes the trace recorder", () => {
  assert.equal(typeof TraceRecorder, "function");
  assert.equal(typeof recordTrace, "function");
});

test("the CommonJS build records a canonical trace", () => {
  const adapter = {
    state: {},
    reset() {
      this.state = { balance_a: 50, balance_b: 50 };
    },
    apply(action, params) {
      const amount = params.amount ?? 0;
      this.state.balance_a -= amount;
      this.state.balance_b += amount;
    },
    snapshot() {
      return { ...this.state };
    },
  };
  const recorder = new TraceRecorder(adapter);
  recorder.reset();
  recorder.apply("transfer_a_to_b", { amount: 20 });
  const trace = recorder.trace;
  assert.equal(trace.length, 2);
  assert.equal(trace[0].action, "init");
  assert.deepEqual(trace[1].state, { balance_a: 30, balance_b: 70 });
});
