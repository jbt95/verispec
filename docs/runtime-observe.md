# Runtime conformance from OpenTelemetry and logs

VeriSpec can check **live or exported telemetry** against a human-owned spec
without sending data to any hosted service.  You run `verispec observe-runtime`
on your machine; it maps spans or structured JSON logs into the same trace
step shape used by `verispec check-trace`, then replays them through the
existing conformance checker (`check_trace_data`).

## What you need

1. A **spec** (`examples/bank.yaml`, or your own contract).
2. A **mapping file** that declares how telemetry becomes steps: span name and
   attributes (or log fields) → `action`, `params`, and observed `state`.
3. **Input**: an OTLP JSON export, NDJSON logs on stdin, or a local OTLP/HTTP
   receiver.

See [`examples/runtime-observe/`](../examples/runtime-observe/) for a bank demo.

## Mapping file

YAML with a `rules` list.  Each rule has:

| Field | Role |
| --- | --- |
| `when` / `match` | Conditions: `kind: span` or `log: true`, `span_name`, `attributes`, or log `field` / `equals` |
| `action` | VeriSpec action name, or `attribute:…` / `field:…` / `${attr.foo}` |
| `params` | Parameter object (same templating) |
| `state` | Observed state variables after the step |

Value templates:

- `attribute:key` — OTLP span attribute or log field
- `field:key` — top-level key on a JSON log line
- `${attr.key}` / `${field.key}` / `${span.name}` — alternate form
- `json:{"amount": 1}` — inline JSON literal

The first matching rule wins.  Unmatched events are skipped.

## Commands

### Batch: OTLP JSON file

```bash
verispec observe-runtime \
  --spec examples/bank.yaml \
  --mapping examples/runtime-observe/bank-mapping.yaml \
  --input examples/runtime-observe/bank-otlp.json
```

### Batch: structured JSON logs (NDJSON)

```bash
verispec observe-runtime \
  --spec examples/bank.yaml \
  --mapping examples/runtime-observe/bank-mapping.yaml \
  --input examples/runtime-observe/bank-logs.ndjson
```

### Streaming: stdin → JSON lines

Each input line is one JSON object (log record or small OTLP chunk).  The
process prints one JSON object per line for steps, violations, and a final
summary.  Exit code `1` on the first violation unless
`--continue-on-violation` is set.

```bash
cat examples/runtime-observe/bank-logs.ndjson | verispec observe-runtime \
  --spec examples/bank.yaml \
  --mapping examples/runtime-observe/bank-mapping.yaml \
  --stdin
```

Optional webhook on violation (still local except for the URL you configure):

```bash
verispec observe-runtime ... --stdin --webhook http://127.0.0.1:9999/alerts
```

### Local OTLP/HTTP receiver

Listens on `HOST:PORT` for `POST /v1/traces` and `POST /v1/logs` with
**OTLP JSON** bodies (no protobuf in this minimal receiver).  Point an
OpenTelemetry Collector `otlphttp` exporter at it, or send test payloads with
`curl`.

```bash
verispec observe-runtime \
  --spec examples/bank.yaml \
  --mapping examples/runtime-observe/bank-mapping.yaml \
  --listen 127.0.0.1:4318 \
  --stream
```

`GET /health` returns `ok`.

## Guarantees and limits

- Same guarantee as trace replay: **`conformance-tested`** for the steps that
  were mapped and checked, not a proof for all possible runs.
- Mapping is **declarative only**; there is no expression language in the
  mapping file beyond attribute/field references.
- The HTTP receiver is **single-process, threaded stdlib** — suitable for
  development and sidecar-style deployment, not high-volume production
  ingestion.
- **Protobuf OTLP** is not decoded; use JSON export or an OTLP/HTTP JSON
  exporter.
- Telemetry is **not sent anywhere by default**; only an explicit `--webhook`
  posts violation JSON.

## Relation to `verispec monitor`

| Command | Input | Use case |
| --- | --- | --- |
| `monitor` | Growing JSON trace file on disk | Adapter already writes VeriSpec trace JSON |
| `observe-runtime` | OTLP JSON, logs, or OTLP/HTTP | OpenTelemetry or app logs with a mapping |

Both end in the same trace conformance logic.
