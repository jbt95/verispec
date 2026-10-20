"""Incremental cache, parallelism, and resource limits for bounded checking."""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.check_cache import read_cached_result, write_cached_result
from verispec.check_config import CheckConfig, cache_key_digest
from verispec.model import load_spec
from verispec.scaled_check import merge_invariant_results, run_bounded_check, run_check_suite

BANK = str(ROOT / "examples" / "bank.yaml")
LEDGER = str(ROOT / "examples" / "ledger.yaml")
CORPUS_SPECS = sorted(str(p) for p in (ROOT / "corpus").glob("*/spec.yaml"))


def test_cache_round_trip(tmp_path):
    spec = load_spec(BANK)
    config = CheckConfig(bound=3)
    key = cache_key_digest(config.cache_key_material(spec))
    assert read_cached_result(tmp_path, key) is None
    write_cached_result(tmp_path, key, {"status": "ok", "bound": 3})
    cached = read_cached_result(tmp_path, key)
    assert cached["status"] == "ok"


def test_run_bounded_check_cache_hit(tmp_path):
    spec = load_spec(BANK)
    config = CheckConfig(bound=4)
    first = run_bounded_check(spec, config, cache_dir=tmp_path, use_cache=True)
    assert first.get("from_cache") is not True
    assert first["status"] == "ok"
    second = run_bounded_check(spec, config, cache_dir=tmp_path, use_cache=True)
    assert second.get("from_cache") is True
    assert second["status"] == "ok"


def test_cache_key_changes_with_bound(tmp_path):
    spec = load_spec(BANK)
    k1 = cache_key_digest(CheckConfig(bound=3).cache_key_material(spec))
    k2 = cache_key_digest(CheckConfig(bound=4).cache_key_material(spec))
    assert k1 != k2


def test_cache_key_includes_liveness_options():
    spec = load_spec(BANK)
    base = cache_key_digest(CheckConfig(bound=3).cache_key_material(spec))
    skipped = cache_key_digest(
        CheckConfig(bound=3, skip_liveness=True).cache_key_material(spec)
    )
    assert base != skipped


def test_check_suite_deterministic_order(tmp_path):
    specs = [BANK, LEDGER]
    config = CheckConfig(bound=3)
    suite = run_check_suite(specs, config, jobs=2, cache_dir=tmp_path)
    paths = [row["spec_path"] for row in suite["results"]]
    assert paths == sorted(paths, key=lambda p: (Path(p).name, p))
    assert suite["ok"] is True
    assert suite["specs"] == 2


def test_check_suite_cache_hits_on_rerun(tmp_path):
    config = CheckConfig(bound=3)
    run_check_suite([BANK, LEDGER], config, jobs=1, cache_dir=tmp_path)
    suite = run_check_suite([BANK, LEDGER], config, jobs=2, cache_dir=tmp_path)
    assert suite["cache_hits"] == 2


def test_wall_timeout_returns_unknown(tmp_path):
    spec = load_spec(BANK)
    config = CheckConfig(bound=8, wall_timeout_ms=1)
    result = run_bounded_check(spec, config, cache_dir=tmp_path, use_cache=False)
    assert result["status"] == "unknown"
    assert result.get("reason") == "wall-timeout"


def test_merge_invariant_results_violation_first():
    spec = load_spec(BANK)
    ok = {"status": "ok", "bound": 2, "message": "ok", "guarantee": "bounded-proved(2)"}
    viol = {
        "status": "violation",
        "bound": 1,
        "message": "bad",
        "guarantee": "violation-found",
        "violated": ["x"],
    }
    merged = merge_invariant_results(spec, [("b", ok), ("a", viol)], max_bound=5)
    assert merged["status"] == "violation"
    assert merged["violated"] == ["x"]


def test_cli_check_suite(capsys, tmp_path):
    from verispec.cli import EXIT_OK, main

    code = main(
        [
            "check-suite",
            BANK,
            LEDGER,
            "--bound",
            "3",
            "--jobs",
            "2",
            "--cache-dir",
            str(tmp_path),
            "--format",
            "json",
        ]
    )
    assert code == EXIT_OK
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["command"] == "check-suite"
    assert payload["specs"] == 2
    assert payload["ok"] is True


def test_corpus_specs_suite_smoke(tmp_path):
    if len(CORPUS_SPECS) < 2:
        return
    subset = CORPUS_SPECS[:4]
    config = CheckConfig(bound=2, timeout_ms=5000)
    started = time.perf_counter()
    suite = run_check_suite(subset, config, jobs=min(4, len(subset)), cache_dir=tmp_path)
    elapsed = time.perf_counter() - started
    assert suite["specs"] == len(subset)
    rerun = run_check_suite(subset, config, jobs=min(4, len(subset)), cache_dir=tmp_path)
    assert rerun["cache_hits"] == len(subset)
    assert rerun["cache_hits"] >= 0
    assert elapsed < 600  # generous guard for CI
