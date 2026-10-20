# VeriSpec benchmarks

Reproducible performance measurements for verification commands.

## Run locally

```console
python -m verispec.cli bench --quick
python -m verispec.cli bench --bound 8 --repeats 3 -o /tmp/bench.json
python benchmarks/compare_bmc_deepening.py
```

## CI smoke gate

The workflow job **Benchmark smoke** runs:

```console
verispec bench --smoke --quick --format json
```

Failures only occur when a case exceeds `baseline_smoke.json` median × `--factor` (default 3×).

## Compare BMC deepening strategies

`compare_bmc_deepening.py` times incremental deepening (current default) against the
reference solver-rebuild loop on the same machine.
