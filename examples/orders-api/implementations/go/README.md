# The orders API, in Go

The Go implementation of the orders API example.  The contract is the same
contract every implementation of this example obeys:
[`orders.verispec.yaml`](../../orders.verispec.yaml).  The Python
implementation lives next door, in
[`examples/orders-api`](../../), and the two record the same trace, byte for
byte.

The stack is deliberately ordinary:

- **net/http** for the HTTP layer,
- **modernc.org/sqlite** (a pure Go SQLite, no cgo) for the database,
- a **service type** with the business rules.

The HTTP layer holds no logic of its own: every route is a shell over
`Service`.  That is what makes the VeriSpec adapter honest: it calls the same
service methods the routes call, and it reads the state back from the
database.  If the adapter reimplemented the rules, the conformance test would
be testing the adapter.

## The layout

| Path | What it is |
| --- | --- |
| `ordersapi/service.go` | The business rules, over a SQLite database. |
| `ordersapi/http.go` | The HTTP layer: a thin shell over the service. |
| `ordersapi/adapter.go` | The VeriSpec adapter. |
| `ordersapi/adapter_buggy.go` | The two deliberately broken adapters. |
| `ordersapi/trace.go` | The trace recorder, in the canonical trace format. |
| `ordersapi/orders_api_verispec_test.go` | The generated conformance test. |
| `ordersapi/orders_api_test.go` | The API tests and the teeth test. |
| `ordersapi/trace_test.go` | The trace tests. |
| `cmd/ordersapi/main.go` | The server. |
| `cmd/record-trace/main.go` | Records the canonical session. |

The module path is `verispec.dev/examples/orders-api`, and the module needs
Go 1.21 or newer.

## Running the server

```console
$ go run ./cmd/ordersapi --addr :8080 --db :memory:
```

`--db` is a SQLite path; `:memory:` is a private in-memory database, and any
file name is a database on disk.  The catalogue is seeded on start: product 0
is a *widget* with 5 units in stock, product 1 is a *gadget* with 3.

```console
$ curl -s localhost:8080/products
[{"id":0,"name":"widget","stock":5,"reserved":0},{"id":1,"name":"gadget","stock":3,"reserved":0}]

$ curl -s -X POST localhost:8080/orders \
    -H 'Content-Type: application/json' \
    -d '{"product_id": 0, "quantity": 2}'
{"id":0,"product_id":0,"quantity":2,"state":"pending","amount":20}
```

## Running the tests

```console
$ go test ./...
```

The suite has three layers:

1. `TestTheApi` drives the real handlers over `httptest` and checks the
   business rules: the status codes, the error messages, the stock, the
   reservation.
2. `TestVeriSpecConformance` is the *generated* property test.  It drives the
   adapter with random action sequences and checks, at every step, that the
   state is what the spec prescribes and that every invariant holds.
3. `TestTheSuiteHasTeeth` runs the same generated test against the broken
   adapter, and requires it to fail.  A suite that stays green on a
   deliberately broken adapter proves nothing.

The generated test is produced by VeriSpec from the spec:

```console
$ ../../../../.venv/bin/python -m verispec.cli gen-tests ../../orders.verispec.yaml \
    --lang go --package ordersapi_test \
    --adapter verispec.dev/examples/orders-api/ordersapi.OrdersApiAdapter \
    -o ordersapi/orders_api_verispec_test.go
```

The committed file is exactly that output, formatted with `gofmt`.  The
generator writes the initial state as a Go literal (a JSON literal is not
valid Go for a spec with collections) and its `veriIf` helper is generic, so
a conditional can take part in an arithmetic expression.

## The API

| Route | What it does |
| --- | --- |
| `GET /products` | The catalogue. |
| `GET /orders` | The orders. |
| `GET /orders/{id}` | One order, or 404. |
| `POST /orders` | Creates an order (`pending`). 201, 404 or 422. |
| `POST /orders/{id}/reserve` | Reserves the stock (`reserved`). 200, 404 or 409. |
| `POST /orders/{id}/pay` | Pays the order (`paid`). 200, 404 or 409. |
| `POST /orders/{id}/ship` | Ships the order (`shipped`). 200, 404 or 409. |
| `POST /orders/{id}/cancel` | Cancels the order (`cancelled`). 200, 404 or 409. |

Every error is a JSON object with a `detail`:

```json
{"detail": "order 7 does not exist"}
```

The rules, and the status code each one produces:

| Rule | Status | Message |
| --- | --- | --- |
| The quantity is not between 1 and 10. | 422 | `quantity must be between 1 and 10` |
| The product does not exist. | 404 | `product 9 does not exist` |
| The order does not exist. | 404 | `order 7 does not exist` |
| There is not enough stock. | 409 | `not enough stock for product 0: 0 available, 1 requested` |
| The order is not in the right state. | 409 | `order 0 is paid, it cannot be cancelled` |
| The idempotency key was used. | 409 | `idempotency key k1 was already used` |

The stock check and the reservation write are in one transaction
(`BEGIN IMMEDIATE`), so two concurrent requests cannot both take the last
unit.

## The adapter contract

The Go adapter contract is three methods:

```go
Reset()
Apply(action string, params map[string]any)
Snapshot() map[string]any
```

`OrdersApiAdapter` implements it over the service.  `Snapshot` returns exactly
the variables of the spec, and nothing else:

| Variable | Type in the snapshot |
| --- | --- |
| `stock`, `reserved` | `map[string]any`, keyed by the stringified product id |
| `order_product`, `order_quantity`, `order_state`, `order_amount` | `map[string]any`, keyed by the stringified order id |
| `paid_keys` | `[]any`, sorted |
| `revenue`, `orders_created` | `int` |

The maps are keyed by the stringified id because that is the JSON
representation the trace format uses.

## From HTTP to spec actions

| Spec action | The service call behind it | The route that calls it |
| --- | --- | --- |
| `place_order` | `Service.PlaceOrder(product, quantity)` | `POST /orders` |
| `reserve` | `Service.Reserve(orderID)` | `POST /orders/{id}/reserve` |
| `pay` | `Service.Pay(orderID, key)` | `POST /orders/{id}/pay` |
| `ship` | `Service.Ship(orderID)` | `POST /orders/{id}/ship` |
| `cancel` | `Service.Cancel(orderID)` | `POST /orders/{id}/cancel` |

The adapter adds two things the routes do not have: it checks that the order
the service created is the order the model expects, and it checks that the
implementation did not go past the bounds of the model.

## The model is bounded, the implementation is not

The spec is a **bounded** model: two products, two orders, two idempotency
keys.  A bounded model is what makes the exhaustive search possible.

The implementation is **not** bounded: a real database holds as many orders
as you ask for.  When the implementation goes past the bounds of the model,
the adapter says so instead of quietly hiding the extra rows, because a check
that silently drops state is not a check:

```console
panic: the bounded model does not cover order [2]: the implementation is past the bounds of the spec
```

## The trace

The trace is how the implementation reports what it did, in the language
agnostic JSON format that `verispec check-trace` replays.  It is committed at
[`traces/orders-session-go.json`](../../traces/orders-session-go.json), and
it is the same trace, byte for byte, as the one the Python implementation
records.

```console
$ go run ./cmd/record-trace -o ../../traces/orders-session-go.json
$ ../../../../.venv/bin/python -m verispec.cli check-trace ../../orders.verispec.yaml \
    ../../traces/orders-session-go.json
trace OK: 8 steps conform to spec OrdersApi
```

`TestTheCommittedTraceIsUpToDate` regenerates the trace and compares it with
the committed one, so the two implementations cannot drift apart.
