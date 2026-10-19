"""Tests for the spec format v1: collections, records, imports, versioning."""

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.errors import SpecError  # noqa: E402
from verispec.model import (  # noqa: E402
    apply_action,
    check_invariants,
    initial_state,
    load_spec,
    load_spec_data,
    version_matches,
)


def build(spec_yaml: str):
    return load_spec_data(yaml.safe_load(spec_yaml), "<test>")


COLLECTION_SPEC = """
spec_version: "1.0"
name: Collections
vars:
  balances: {type: map, key: int, key_min: 0, key_max: 3, value: int, value_min: 0, value_max: 100, max_size: 4}
  by_name: {type: map, key: str, key_domain: [alice, bob], value: int, value_min: 0, value_max: 100, max_size: 2}
  seen: {type: set, of: str, elem_domain: [a, b, c], max_size: 3}
  history: {type: seq, of: int, elem_min: 0, elem_max: 10, max_size: 4}
  customer: {type: record, fields: {name: str, tier: "enum[gold,silver]", active: bool}}
init:
  balances: {0: 10, 1: 20}
  by_name: {alice: 5}
  seen: [a]
  history: [1, 2]
  customer: {name: alice, tier: gold, active: true}
actions:
  - name: deposit
    params:
      account: {type: int, min: 0, max: 3}
      amount: {type: int, min: 1, max: 50}
    requires: ["amount > 0"]
    effects:
      balances: "map_set(balances, account, map_get(balances, account) + amount)"
      seen: "set_add(seen, \\"a\\")"
      history: "seq_append(history, amount)"
      customer:
        active: "false"
invariants:
  - {name: capacity, expr: "len(balances) <= 4"}
"""


# -- loading and types -----------------------------------------------------


def test_collection_spec_loads():
    s = build(COLLECTION_SPEC)
    assert s.spec_version == "1.0"
    assert set(s.vars) == {"balances", "by_name", "seen", "history", "customer"}


def test_initial_state_of_collections():
    s = build(COLLECTION_SPEC)
    st = initial_state(s)
    assert st["balances"] == {0: 10, 1: 20}
    assert st["seen"] == {"a"}
    assert st["history"] == [1, 2]
    assert st["customer"] == {"name": "alice", "tier": "gold", "active": True}


def test_apply_action_updates_collections_and_records():
    s = build(COLLECTION_SPEC)
    st = initial_state(s)
    nxt = apply_action(s, st, "deposit", {"account": 2, "amount": 5})
    assert nxt["balances"] == {0: 10, 1: 20, 2: 5}
    assert nxt["history"] == [1, 2, 5]
    assert nxt["customer"]["active"] is False
    assert nxt["customer"]["name"] == "alice"


def test_map_set_ignores_out_of_domain_keys():
    s = build(COLLECTION_SPEC)
    st = initial_state(s)
    nxt = apply_action(s, st, "deposit", {"account": 3, "amount": 1})
    assert nxt["balances"] == {0: 10, 1: 20, 3: 1}


def test_missing_key_reads_the_default():
    s = build(COLLECTION_SPEC)
    st = initial_state(s)
    assert apply_action(s, st, "deposit", {"account": 3, "amount": 1})["balances"][3] == 1


def test_invariants_are_checked():
    s = build(COLLECTION_SPEC)
    st = initial_state(s)
    assert check_invariants(s, st) == []


# -- type errors -----------------------------------------------------------


def test_map_requires_a_domain():
    with pytest.raises(SpecError):
        build(
            """
name: X
vars:
  m: {type: map, key: int, value: int}
actions: []
"""
        )


def test_str_keys_require_a_domain():
    with pytest.raises(SpecError):
        build(
            """
name: X
vars:
  m: {type: map, key: str, value: int}
actions: []
"""
        )


def test_collection_domain_is_bounded():
    with pytest.raises(SpecError):
        build(
            """
name: X
vars:
  m: {type: map, key: int, key_min: 0, key_max: 100000, value: int}
actions: []
"""
        )


def test_unknown_variable_in_expression():
    with pytest.raises(SpecError):
        build(
            """
name: X
vars:
  a: {type: int, min: 0, max: 10}
actions:
  - name: go
    effects:
      a: "b + 1"
"""
        )


def test_effect_type_must_match_the_var():
    with pytest.raises(SpecError):
        build(
            """
name: X
vars:
  a: {type: int, min: 0, max: 10}
  b: {type: bool}
actions:
  - name: go
    effects:
      a: "true"
"""
        )


def test_invariant_must_be_boolean():
    with pytest.raises(SpecError):
        build(
            """
name: X
vars:
  a: {type: int, min: 0, max: 10}
actions: []
invariants:
  - {name: bad, expr: "a + 1"}
"""
        )


def test_unknown_record_field():
    with pytest.raises(SpecError):
        build(
            """
name: X
vars:
  c: {type: record, fields: {name: str}}
actions:
  - name: go
    effects:
      c: {nickname: "x"}
"""
        )


def test_attribute_access_is_forbidden():
    with pytest.raises(SpecError):
        build(
            """
name: X
vars:
  c: {type: record, fields: {name: str}}
actions:
  - name: go
    requires: ["c.name == 'x'"]
"""
        )


# -- versioning ------------------------------------------------------------


def test_spec_version_is_optional_and_defaults_to_v01():
    s = build(
        """
name: X
vars:
  a: {type: int, min: 0, max: 10}
actions: []
"""
    )
    assert s.spec_version == "0.1"


def test_unsupported_major_is_rejected():
    with pytest.raises(SpecError):
        build(
            """
spec_version: "2.0"
name: X
vars:
  a: {type: int, min: 0, max: 10}
actions: []
"""
        )


def test_version_ranges():
    assert version_matches("1.0.0", "^1.0")
    assert not version_matches("2.0.0", "^1.0")
    assert version_matches("1.2.0", ">=1.0,<2.0")
    assert version_matches("1.0.0", "1.x")
    assert version_matches("1.0.0", None)


# -- imports ---------------------------------------------------------------


def test_imports_merge_and_namespace(tmp_path):
    (tmp_path / "common.yaml").write_text(
        """
spec_version: "1.0"
name: Common
vars:
  counter: {type: int, min: 0, max: 10}
init:
  counter: "0"
actions:
  - name: bump
    effects:
      counter: "counter + 1"
invariants:
  - {name: counter_bounded, expr: "counter <= 10"}
"""
    )
    (tmp_path / "main.yaml").write_text(
        """
spec_version: "1.0"
name: Main
imports:
  - path: ./common.yaml
    as: common
vars:
  total: {type: int, min: 0, max: 10}
init:
  total: "0"
actions:
  - name: add
    effects:
      total: "total + common_counter"
invariants:
  - {name: total_bounded, expr: "total <= 10"}
"""
    )
    s = load_spec(tmp_path / "main.yaml")
    assert "common_counter" in s.vars
    assert s.has_action("common_bump")
    assert any(i["name"] == "common_counter_bounded" for i in s.invariants)
    st = initial_state(s)
    assert st["common_counter"] == 0
    nxt = apply_action(s, st, "common_bump", {})
    assert nxt["common_counter"] == 1


def test_import_version_is_checked(tmp_path):
    (tmp_path / "common.yaml").write_text(
        """
spec_version: "2.0"
name: Common
vars:
  counter: {type: int, min: 0, max: 10}
actions: []
"""
    )
    (tmp_path / "main.yaml").write_text(
        """
spec_version: "1.0"
name: Main
imports:
  - path: ./common.yaml
    as: common
    version: "^1.0"
vars:
  total: {type: int, min: 0, max: 10}
actions: []
"""
    )
    with pytest.raises(SpecError):
        load_spec(tmp_path / "main.yaml")


def test_missing_import_is_reported(tmp_path):
    (tmp_path / "main.yaml").write_text(
        """
spec_version: "1.0"
name: Main
imports:
  - path: ./nope.yaml
    as: nope
vars:
  total: {type: int, min: 0, max: 10}
actions: []
"""
    )
    with pytest.raises(SpecError):
        load_spec(tmp_path / "main.yaml")
