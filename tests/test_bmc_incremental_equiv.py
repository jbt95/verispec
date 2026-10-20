"""Incremental BMC deepening must match the reference rebuild loop."""

from __future__ import annotations

from pathlib import Path

import pytest

from verispec.bmc import (
    _check_after_preflight_layered,
    _check_after_preflight_rebuild,
    _preflight,
)
from verispec.model import load_spec

ROOT = Path(__file__).resolve().parents[1]

SPECS = [
    ROOT / "examples" / "bank.yaml",
    ROOT / "examples" / "ledger.yaml",
    ROOT / "corpus" / "orders-api" / "spec.yaml",
]


@pytest.mark.parametrize("spec_path", [p for p in SPECS if p.is_file()])
@pytest.mark.parametrize("bound", (2, 4))
def test_incremental_deepening_matches_rebuild(spec_path: Path, bound: int) -> None:
    spec = load_spec(spec_path)
    preflight = _preflight(spec, bound, 30000)
    incremental = _check_after_preflight_layered(spec, bound, None, 30000, None)
    rebuild = _check_after_preflight_rebuild(
        spec, bound, None, 30000, None, preflight
    )
    if preflight is not None:
        assert incremental == preflight
        assert rebuild == preflight
        return
    assert incremental == rebuild
