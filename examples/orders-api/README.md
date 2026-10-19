# The orders API: one spec, three implementations, one behaviour

A REST API with a database and real business rules, and the VeriSpec contract
that both the implementation and its tests obey.

The point of this example is not the API. It is the **shape of the workflow**:

```
                        orders.verispec.yaml
            the contract: state, actions, invariants
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
   Python (FastAPI)     TypeScript (node:http)      Go (net/http)
   + sqlite3            + node:sqlite             + modernc.org/sqlite
        │                       │                       │
        └─────────── adapter (3 methods) ───────────────┘
                                │
                 ┌──────────────┴──────────────┐
                 ▼                             ▼
        generated conformance test      recorded trace
        (does it follow the spec?)      (does the real behaviour
                                          follow the spec?)
```

The spec is written **once**. Each implementation has an *adapter*: three
methods that expose the real code to VeriSpec. The generated conformance test
then drives every implementation against the same contract, and the recorded
traces prove that the implementations agree with each other.

## The contract

```yaml
spec_version: "1.0"
name: OrdersApi
vars:
  stock:     {type: map, key: int, key_min: 0, key_max: 1, value: int, ...}
  reserved:  {type: map, key: int, key_min: 0, key_max: 1, value: int, ...}
  order_product:  {type: map, key: int, key_min: 0, key_max: 1, value: int, ...}
  order_quantity: {type: map, key: int, key_min: 0, key_max: 1, value: int, ...}
  order_state:    {type: map, key: int, key_min: 0, key_max: 1, value: "enum[none,pending,reserved,paid,shipped,cancelled]", ...}
  order_amount:   {type: map, key: int, key_min: 0, key_max: 1, value: int, ...}
  paid_keys: {type: set, of: str, elem_domain: [k1, k2], max_size: 2}
  revenue: {type: int, min: 0, max: 1000}
  orders_created: {type: int, min: 0, max: 2}
actions:
  - name: place_order
    params: {order_id: ..., product: ..., quantity: ...}
    requires: ["order_id == orders_created", "quantity > 0"]
    effects: {...}
  - name: reserve
    requires:
      - "map_get(order_state, order_id) == \\"pending\\""
      - "map_get(stock, product) - map_get(reserved, product) >= map_get(order_quantity, order_id)"
    effects: {...}
  - name: pay
    requires: ["map_get(order_state, order_id) == \\"reserved\\"", "key not in paid_keys"]
    effects: {...}
  - name: ship
    requires: ["map_get(order_state, order_id) == \\"paid\\""]
    effects: {...}
  - name: cancel
    requires: ["map_get(order_state, order_id) == \\"pending\\" or map_get(order_state, order_id) == \\"reserved\\""]
    effects: {...}
invariants:
  - {name: no_overselling, expr: "..."}
  - {name: one_payment_per_order, expr: "..."}
  - {name: revenue_matches_paid_orders, expr: "..."}
  - ...
```

The full file is [`orders.verispec.yaml`](orders.verispec.yaml).

### How the model is shaped, and why

An order has several fields, and a **map value must be a scalar**, so an order
is modelled as one map per field: `order_product`, `order_quantity`,
`order_state`, `order_amount`. That is the usual relational encoding of a
bounded model, and it is what the format can express today.

The model is **bounded on purpose**: two products, two orders, two
idempotency keys. A bounded model is what makes an exhaustive search
possible. The *implementation* is not bounded: it is a real database that
holds as many orders as you ask for. The adapter refuses to hide the
difference (see [Past the bounds](#past-the-bounds)).

### The business rules

| Rule | In the spec | In the code |
| --- | --- | --- |
| An order is created for a product, with a positive quantity | `place_order` requires `order_id == orders_created`, `quantity > 0` | `POST /orders` |
| You cannot reserve more stock than there is | `reserve` requires `stock - reserved >= quantity`; invariant `no_overselling` | `POST /orders/{id}/reserve` answers 409 |
| A payment is applied at most once | `pay` requires `key not in paid_keys`; invariant `one_payment_per_order` | `POST /orders/{id}/pay` answers 409 |
| The revenue is what the paid orders are worth | invariant `revenue_matches_paid_orders` | the `payments` table |
| Only a paid order ships, and shipping consumes the stock | `ship` requires the order to be `paid` | `POST /orders/{id}/ship` |
| An unpaid order can be cancelled, releasing the reservation | `cancel` requires `pending` or `reserved` | `POST /orders/{id}/cancel` |

Every rule in the table is in the spec, and the code is checked against it.

## Running it

### Python

```bash
pip install "fastapi" "uvicorn" "httpx"
cd examples/orders-api
uvicorn orders_api:app --reload
```

```console
$ curl -X POST localhost:8000/orders -H 'content-type: application/json' \
       -d '{"product_id": 0, "quantity": 2}'
{"id":0,"product_id":0,"quantity":2,"state":"pending","amount":20}

$ curl -X POST localhost:8000/orders/0/reserve
{"id":0,"product_id":0,"quantity":2,"state":"reserved","amount":20}

$ curl -X POST localhost:8000/orders/0/pay -H 'content-type: application/json' \
       -d '{"idempotency_key": "k1"}'
{"id":0,"product_id":0,"quantity":2,"state":"paid","amount":20}
```

### TypeScript

```bash
cd examples/orders-api/implementations/typescript
npm install
npm start          # http://localhost:3000
npm test
```

### Go

```bash
cd examples/orders-api/implementations/go
go test ./...
go run ./cmd/ordersapi --addr :8080    # http://localhost:8080
```

## Checking the contract

```console
$ verispec check orders.verispec.yaml --bound 6
no violation up to bound 6  (bounded claim only, not a proof for all depths; guarantee: bounded-proved(6))
```

The model checker explores every sequence of actions up to the bound and
proves that no invariant can be broken. The check is exponential in the
bound, so CI uses a modest one:

| Bound | Time (this model) |
| --- | --- |
| 5 | ~3 s |
| 6 | ~9 s |
| 7 | ~36 s |
| 8 | ~1.5 min |

When the solver cannot decide a bound, the tool says what it *did* prove:

```console
$ verispec check orders.verispec.yaml --bound 10 --timeout 20000
no violation up to bound 6; the solver could not decide bound 7 (timeout)  (guarantee: bounded-proved(6))
```

To go deeper, check one rule at a time, which is much cheaper:

```console
$ verispec check orders.verispec.yaml --bound 8 --invariant no_overselling
```

## The adapter

The adapter is the only language-specific piece, and it is deliberately
thin:

```python
class OrdersApiAdapter:
    def reset(self):
        self.service.reset()

    def apply(self, action, **params):
        if action == "place_order":
            self.service.place_order(params["product"], params["quantity"])
        elif action == "reserve":
            self.service.reserve(params["order_id"])
        ...

    def snapshot(self):
        # read the state back from the database
        return {
            "stock": {...}, "reserved": {...},
            "order_product": {...}, "order_quantity": {...},
            "order_state": {...}, "order_amount": {...},
            "paid_keys": {...}, "revenue": ..., "orders_created": ...,
        }
```

It calls the **same service methods the HTTP routes call**. It does not
reimplement a single business rule: an adapter that reimplemented the rules
would make the conformance test test the adapter.

## The four layers of tests

```bash
cd examples/orders-api && pytest -q .
```

1. **The API tests.** Ordinary HTTP tests: the routes answer 404, 409 and
   422 in the right cases, the stock is consumed on shipping, the
   reservation is released on cancellation.
2. **The conformance test.** The *generated* property test, driven against
   the adapter: after every action, the state the implementation reports
   must be exactly the state the spec says it should be, and every
   invariant must hold.
3. **The teeth test.** The same property, against a deliberately broken
   implementation: it must fail. A suite that stays green on a broken
   implementation proves nothing.
4. **The trace test.** A recorded session is replayed against the spec, and
   the committed trace is compared with a freshly recorded one, so the
   implementations cannot drift apart.

## The differential oracle

The three implementations record the same session:

```console
$ verispec diff-trace traces/orders-session.json traces/orders-session-typescript.json
the two traces are identical
```

That is the migration use case: when you rewrite a service in another
language, the contract and the traces prove the behaviour did not change.

## What VeriSpec catches here, and what it does not

**It catches** an implementation that computes the wrong state (the
conformance test), and it catches a state that violates an invariant, even
when the implementation reached it through a path the property test never
took (the trace path: an implementation that oversells produces a recorded
state with `reserved > stock`, and the trace checker names the invariant).

**It does not catch** an implementation that accepts an action the spec
forbids, if that path is never exercised: the generated property test only
takes actions the spec allows. The API's own tests cover that case, and the
trace path covers it whenever the illegal path is really taken.

**It does not prove** that the implementation is correct in general. The
guarantee is `conformance-tested` for the traces and `bounded-proved(k)` for
the model. See [`docs/guarantees.md`](../../docs/guarantees.md).

## Past the bounds

The model covers two products, two orders and two keys. The implementation
covers none of those limits. When the implementation goes past the model,
the adapter says so instead of quietly hiding the extra rows:

```python
class ModelOverflow(Exception):
    """The implementation went past the bounds of the bounded model."""
```

A check that silently drops state is not a check. Widen the model's domains
in `orders.verispec.yaml` when the example grows.
