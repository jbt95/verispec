/**
 * The TypeScript conformance suite.
 *
 * Four checks, the same ones every adapter has to pass:
 *
 *   1. the generated property test passes on the correct adapter,
 *   2. the generated property test FAILS on the broken adapter,
 *   3. the trace recorder writes the canonical trace format,
 *   4. the recorded trace is accepted by `verispec check-trace`.
 *
 * The generated test is produced by the VeriSpec CLI in the `pretest` step:
 *
 *     verispec gen-tests ../../examples/bank.yaml --lang typescript \
 *         -o test/bank.verispec.test.ts
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { runConformance } from "./bank.verispec.test.js";
import { makeAdapter } from "./bank_adapter.js";
import { makeAdapter as makeBuggyAdapter } from "./bank_adapter_buggy.js";
import { TraceRecorder } from "../src/trace.js";

/** Walk up from this file until the repository's examples directory shows up. */
function findRepoRoot(): string {
  let dir = dirname(fileURLToPath(import.meta.url));
  for (let i = 0; i < 8; i++) {
    if (existsSync(join(dir, "examples", "bank.yaml"))) return dir;
    const parent = resolve(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error("could not find the VeriSpec repository root");
}

const REPO_ROOT = findRepoRoot();

test("the generated conformance test passes on the correct adapter", () => {
  runConformance(makeAdapter);
});

test("the generated conformance test fails on the broken adapter", () => {
  assert.throws(() => runConformance(makeBuggyAdapter), /Property failed|mismatch|invariant/);
});

test("the trace recorder writes the canonical trace format", () => {
  const recorder = new TraceRecorder(makeAdapter());
  recorder.reset();
  recorder.apply("transfer_a_to_b", { amount: 20 });
  const trace = recorder.trace;
  assert.equal(trace.length, 2);
  assert.equal(trace[0].action, "init");
  assert.deepEqual(trace[0].state, { balance_a: 50, balance_b: 50 });
  assert.equal(trace[1].action, "transfer_a_to_b");
  assert.deepEqual(trace[1].params, { amount: 20 });
  assert.deepEqual(trace[1].state, { balance_a: 30, balance_b: 70 });
});

test("verispec check-trace accepts the recorded trace", () => {
  const dir = mkdtempSync(join(tmpdir(), "verispec-ts-"));
  const path = join(dir, "trace.json");
  const recorder = new TraceRecorder(makeAdapter());
  recorder.reset();
  recorder.apply("transfer_a_to_b", { amount: 20 });
  recorder.apply("transfer_b_to_a", { amount: 10 });
  writeFileSync(path, recorder.toJson(), "utf8");

  const out = execFileSync(
    "verispec",
    ["check-trace", join(REPO_ROOT, "examples/bank.yaml"), path],
    { encoding: "utf8" },
  );
  assert.match(out, /trace OK/);
  // the trace on disk is valid JSON with the expected first entry
  const parsed = JSON.parse(readFileSync(path, "utf8"));
  assert.equal(parsed[0].action, "init");
});
