# The orders API, in TypeScript

The same contract, the same rules and the same database as the Python
implementation, in TypeScript. The stack has no third-party dependencies:

- `node:http` for the HTTP layer,
- `node:sqlite` for the database (built into Node 22 and later),
- `fast-check` for the generated conformance test (a dev dependency).

## Run it

```bash
npm install
npm start        # http://localhost:3000
npm test
```

`npm start` needs the compiled output, so run `npm run build` first (or use
`npm run build && node dist/serve.js`).

## The files

| File | What it is |
| --- | --- |
| `orders-api.ts` | The service with the business rules, and the HTTP layer over it. |
| `adapter.ts` | The VeriSpec adapter: `reset`, `apply`, `snapshot`. |
| `orders.verispec.test.ts` | The **generated** conformance test. Do not edit it: `npm run gen` regenerates it from the spec. |
| `test/orders-api.test.ts` | The API tests, the teeth test and the trace comparison. |

## The mapping

| HTTP | Spec action |
| --- | --- |
| `POST /orders` | `place_order` |
| `POST /orders/{id}/reserve` | `reserve` |
| `POST /orders/{id}/pay` | `pay` |
| `POST /orders/{id}/ship` | `ship` |
| `POST /orders/{id}/cancel` | `cancel` |

The adapter calls the same service methods the routes call, and it reads the
state back from the database. It does not reimplement a business rule.

## The bounded model

The model covers two products, two orders and two idempotency keys. The
implementation is not bounded. When it goes past the model, the adapter
throws `ModelOverflow` instead of hiding the extra rows.

## The trace

`npm test` records the session in `../../traces/orders-session-typescript.json`
and compares it with the Python trace. The two must be identical: same
actions, same parameters, same states. That is the differential oracle.

## License

Apache-2.0.
