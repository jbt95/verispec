"""Tests for load_spec caching."""

from __future__ import annotations

from pathlib import Path

from verispec.model import load_spec

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "examples" / "bank.yaml"


def test_load_spec_cache_invalidates_on_content_change(tmp_path: Path) -> None:
    path = tmp_path / "spec.yaml"
    path.write_text(BANK.read_text().replace("name: Bank", "name: First", 1))
    first = load_spec(path)
    assert first.name == "First"

    path.write_text(BANK.read_text().replace("name: Bank", "name: Second", 1))
    second = load_spec(path)
    assert second.name == "Second"
