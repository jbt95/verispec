# Rate limiter

A token bucket.  The capacity bound is the interesting invariant: a refill
that overflows the bucket must be clamped.

```bash
verispec check registry/rate-limiter/rate-limiter.verispec.yaml --bound 8
```
