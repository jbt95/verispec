/**
 * The tests for the TypeScript implementation of the orders API.
 *
 * The same four layers as the Python example:
 *
 * 1. the API tests: does the API enforce the business rules?
 * 2. the conformance test: does the implementation obey the *spec*?
 * 3. the teeth test: does the conformance test fail on the broken adapter?
 * 4. the trace test: does a recorded session conform, and does it match the
 *    trace the Python implementation recorded?
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { OrdersService, startServer } from "../orders-api.js";
import { BuggyOrdersApiAdapter, OrdersApiAdapter } from "../adapter.js";
import { effects_cancel, effects_reserve, runConformance } from "../orders.verispec.test.js";

const HERE = dirname(fileURLToPath(import.meta.url));

/** The same session the Python implementation records. */
const SESSION: Array<[string, Record<string, any>]> = [
  ["place_order", { order_id: 0, product: 0, quantity: 3 }],
  ["reserve", { order_id: 0, product: 0 }],
  ["pay", { order_id: 0, key: "k1" }],
  ["ship", { order_id: 0 }],
  ["place_order", { order_id: 1, product: 1, quantity: 1 }],
  ["reserve", { order_id: 1, product: 1 }],
  ["pay", { order_id: 1, key: "k2" }],
];

/** Walk up until the example directory shows up. */
function findExampleDir(): string {
  let dir = HERE;
  for (let i = 0; i < 8; i++) {
    if (existsSync(join(dir, "orders.verispec.yaml"))) return dir;
    dir = resolve(dir, "..");
  }
  throw new Error("could not find the orders API example directory");
}

const EXAMPLE_DIR = findExampleDir();
const PYTHON_TRACE = join(EXAMPLE_DIR, "traces", "orders-session.json");
const GO_TRACE = join(EXAMPLE_DIR, "traces", "orders-session-go.json");
const TS_TRACE = join(EXAMPLE_DIR, "traces", "orders-session-typescript.json");

/** Start the API on a free port and return a fetch helper. */
async function withApi<T>(body: (call: (method: string, path: string, payload?: unknown) => Promise<{ status: number; body: any }>) => Promise<T>): Promise<T> {
  const service = new OrdersService();
  const server = await startServer(0, service);
  const base = `http://127.0.0.1:${server.port}`;
  const call = async (method: string, path: string, payload?: unknown) => {
    const response = await fetch(base + path, {
      method,
      headers: payload ? { "content-type": "application/json" } : {},
      body: payload ? JSON.stringify(payload) : undefined,
    });
    const text = await response.text();
    return { status: response.status, body: text ? JSON.parse(text) : null };
  };
  try {
    return await body(call);
  } finally {
    server.close();
  }
}

// --------------------------------------------------------------------------
// 1. the API
// --------------------------------------------------------------------------

test("the catalogue is seeded", async () => {
  await withApi(async (call) => {
    const { status, body } = await call("GET", "/products");
    assert.equal(status, 200);
    assert.deepEqual(
      body.map((p: any) => p.id),
      [0, 1],
    );
    assert.equal(body[0].stock, 5);
    assert.equal(body[1].stock, 3);
  });
});

test("placing an order", async () => {
  await withApi(async (call) => {
    const { status, body } = await call("POST", "/orders", { product_id: 0, quantity: 2 });
    assert.equal(status, 201);
    assert.equal(body.state, "pending");
    assert.equal(body.amount, 20);
  });
});

test("an unknown product is a 404", async () => {
  await withApi(async (call) => {
    const { status } = await call("POST", "/orders", { product_id: 9, quantity: 1 });
    assert.equal(status, 404);
  });
});

test("a bad quantity is a 422", async () => {
  await withApi(async (call) => {
    assert.equal((await call("POST", "/orders", { product_id: 0, quantity: 0 })).status, 422);
    assert.equal((await call("POST", "/orders", { product_id: 0, quantity: 99 })).status, 422);
  });
});

test("reserving more than the stock is a 409", async () => {
  await withApi(async (call) => {
    await call("POST", "/orders", { product_id: 0, quantity: 5 });
    await call("POST", "/orders/0/reserve");
    await call("POST", "/orders", { product_id: 0, quantity: 1 });
    const { status, body } = await call("POST", "/orders/1/reserve");
    assert.equal(status, 409);
    assert.match(body.detail, /not enough stock/);
  });
});

test("reserving twice is a 409", async () => {
  await withApi(async (call) => {
    await call("POST", "/orders", { product_id: 0, quantity: 1 });
    assert.equal((await call("POST", "/orders/0/reserve")).status, 200);
    assert.equal((await call("POST", "/orders/0/reserve")).status, 409);
  });
});

test("paying twice is a 409", async () => {
  await withApi(async (call) => {
    await call("POST", "/orders", { product_id: 0, quantity: 1 });
    await call("POST", "/orders/0/reserve");
    assert.equal((await call("POST", "/orders/0/pay", { idempotency_key: "k1" })).status, 200);
    assert.equal((await call("POST", "/orders/0/pay", { idempotency_key: "k2" })).status, 409);
  });
});

test("reusing an idempotency key is a 409", async () => {
  await withApi(async (call) => {
    await call("POST", "/orders", { product_id: 0, quantity: 1 });
    await call("POST", "/orders", { product_id: 1, quantity: 1 });
    await call("POST", "/orders/0/reserve");
    await call("POST", "/orders/1/reserve");
    assert.equal((await call("POST", "/orders/0/pay", { idempotency_key: "k1" })).status, 200);
    assert.equal((await call("POST", "/orders/1/pay", { idempotency_key: "k1" })).status, 409);
  });
});

test("shipping without paying is a 409", async () => {
  await withApi(async (call) => {
    await call("POST", "/orders", { product_id: 0, quantity: 1 });
    assert.equal((await call("POST", "/orders/0/ship")).status, 409);
  });
});

test("shipping consumes the stock", async () => {
  await withApi(async (call) => {
    await call("POST", "/orders", { product_id: 0, quantity: 2 });
    await call("POST", "/orders/0/reserve");
    await call("POST", "/orders/0/pay", { idempotency_key: "k1" });
    assert.equal((await call("POST", "/orders/0/ship")).status, 200);
    const { body } = await call("GET", "/products");
    assert.equal(body[0].stock, 3);
    assert.equal(body[0].reserved, 0);
  });
});

test("cancelling a paid order is a 409", async () => {
  await withApi(async (call) => {
    await call("POST", "/orders", { product_id: 0, quantity: 1 });
    await call("POST", "/orders/0/reserve");
    await call("POST", "/orders/0/pay", { idempotency_key: "k1" });
    assert.equal((await call("POST", "/orders/0/cancel")).status, 409);
  });
});

test("cancelling releases the reservation", async () => {
  await withApi(async (call) => {
    await call("POST", "/orders", { product_id: 0, quantity: 2 });
    await call("POST", "/orders/0/reserve");
    assert.equal((await call("GET", "/products")).body[0].reserved, 2);
    assert.equal((await call("POST", "/orders/0/cancel")).status, 200);
    const product = (await call("GET", "/products")).body[0];
    assert.equal(product.reserved, 0);
    assert.equal(product.stock, 5);
  });
});

test("an unknown order is a 404", async () => {
  await withApi(async (call) => {
    assert.equal((await call("GET", "/orders/7")).status, 404);
  });
});

// --------------------------------------------------------------------------
// 2. the conformance test
// --------------------------------------------------------------------------

test("the implementation follows the spec", () => {
  runConformance(() => new OrdersApiAdapter());
});

test("the broken implementation diverges from the spec", () => {
  // The broken adapter keeps the reserved stock after a cancellation, and
  // the spec says it must not.  The check is deterministic and compares the
  // implementation with the spec's own effect functions: a random property
  // test would only find this by luck.
  const adapter = new BuggyOrdersApiAdapter();
  adapter.reset();
  adapter.apply("place_order", { order_id: 0, product: 0, quantity: 2 });

  const beforeReserve = adapter.snapshot();
  adapter.apply("reserve", { order_id: 0, product: 0 });
  assert.deepEqual(
    adapter.snapshot(),
    effects_reserve(beforeReserve, { order_id: 0, product: 0 }),
    "the reservation did not follow the spec",
  );

  const beforeCancel = adapter.snapshot();
  adapter.apply("cancel", { order_id: 0 });
  assert.notDeepEqual(
    adapter.snapshot(),
    effects_cancel(beforeCancel, { order_id: 0 }),
    "the broken adapter should diverge from the spec here: it keeps the reservation",
  );
});

// --------------------------------------------------------------------------
// 3. the trace
// --------------------------------------------------------------------------

test("the recorded trace matches the Python implementation", () => {
  const adapter = new OrdersApiAdapter();
  adapter.reset();
  const steps: any[] = [{ action: "init", params: {}, state: adapter.snapshot() }];
  for (const [action, params] of SESSION) {
    adapter.apply(action, params);
    steps.push({ action, params, state: adapter.snapshot() });
  }

  const python = JSON.parse(readFileSync(PYTHON_TRACE, "utf8"));
  assert.deepEqual(steps, python, "the TypeScript and the Python implementations diverged");

  // and the trace is written for the other implementations to compare with
  mkdirSync(dirname(TS_TRACE), { recursive: true });
  writeFileSync(TS_TRACE, JSON.stringify(steps, null, 2) + "\n", "utf8");
});

test("the Go implementation records the same trace", { skip: !existsSync(GO_TRACE) }, () => {
  const python = JSON.parse(readFileSync(PYTHON_TRACE, "utf8"));
  const go = JSON.parse(readFileSync(GO_TRACE, "utf8"));
  assert.deepEqual(go, python, "the Go and the Python implementations diverged");
});
