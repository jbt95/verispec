# Idempotency

A request is applied at most once.  The set of seen request ids and the
counter must stay in sync.

```bash
verispec check registry/idempotency/idempotency.verispec.yaml --bound 8
```
