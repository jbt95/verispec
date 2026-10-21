# Spec-strength semantic examples

These files demonstrate [VerusEval](https://arxiv.org/abs/2610.26464)-style checks layered on
`verispec spec-strength`: Z3 vacuity, concrete I/O cases, and reference comparison.

```bash
verispec spec-strength bank-weak.yaml \
  --io-examples bank-io.json \
  --reference-spec ../bank.yaml \
  --format json
```

See [RFC-0005](../../rfcs/0005-spec-strength-semantics.md) for the design.
