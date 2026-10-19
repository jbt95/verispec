"""Generate conformance tests for Rust and C adapters.

The generated test is the language-agnostic part of the "Route B" strategy:
verify the model once, then check that the real implementation conforms.

* **rust** - a self-contained integration test file.  The spec expressions are
  transpiled to Rust and the runtime helpers come from the `verispec-adapter`
  crate.  The random driver is a deterministic LCG written into the file, so
  the test needs no extra dependency and every failure is reproducible.
* **c**    - the same test for a scalar-only C spec, using the `verispec.h`
  runtime.

Both generators produce *conformance-tested* evidence: they show that the
adapter agrees with the spec on the states they reach.  They never claim a
proof.
"""

from __future__ import annotations

import re
from typing import Any

from .codegen_ext import to_c, to_rust
from .errors import SpecError
from .model import Spec, initial_state
from .types import (
    EnumType,
    RecordType,
    TypeSpec,
    type_name,
)
from .values import state_to_json

__all__ = ["gen_rust", "gen_c", "gen_external", "EXTERNAL_LANGUAGES"]

#: Languages handled by :func:`gen_external`.
EXTERNAL_LANGUAGES = ("rust", "c")

#: The seed of the deterministic generator embedded in the generated tests.
SEED = 0x9E3779B97F4A7C15

#: How many independent runs and how many steps per run the driver performs.
ITERATIONS = 50
STEPS = 25


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _ident(name: str) -> str:
    """A safe identifier for a spec name (action, variable, invariant)."""
    out = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if not out:
        out = "v"
    if out[0].isdigit():
        out = "v_" + out
    return out


def _is_scalar(t: TypeSpec) -> bool:
    return getattr(t, "kind", None) in ("int", "bool", "str", "enum")


def _require_scalar_spec(spec: Spec, lang: str) -> None:
    for name, var in spec.vars.items():
        if not _is_scalar(var.type):
            raise SpecError(
                f"the {lang} adapter supports scalar specs only: variable {name!r} has "
                f"type {type_name(var.type)}; use the Rust adapter for collection specs"
            )


def _param_bounds(pv) -> tuple[int, int]:
    lo = pv.min if pv.min is not None else 0
    hi = pv.max if pv.max is not None else lo + 20
    if hi < lo:
        hi = lo
    return lo, hi


def _rust_str(value: str) -> str:
    """A `&str` literal."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _c_str(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _rust_json(value: Any) -> str:
    """A `serde_json::Value` expression for a concrete JSON value."""
    if value is True:
        return "Value::from(true)"
    if value is False:
        return "Value::from(false)"
    if value is None:
        return "Value::Null"
    if isinstance(value, str):
        return f"Value::from({_rust_str(value)})"
    if isinstance(value, int):
        return f"Value::from({value}i64)"
    if isinstance(value, list):
        return "Value::from(vec![" + ", ".join(_rust_json(v) for v in value) + "])"
    if isinstance(value, dict):
        items = ", ".join(
            f"({_rust_str(k)}.to_string(), {_rust_json(v)})" for k, v in value.items()
        )
        return "Value::Object(Map::from_iter([" + items + "]))"
    raise SpecError(f"cannot express {value!r} as a JSON value in Rust")


# --------------------------------------------------------------------------
# Rust
# --------------------------------------------------------------------------

RUST_PRELUDE = """
/// A deterministic 64 bit generator (xorshift64*).
///
/// The conformance run must be reproducible, so the generator is seeded with a
/// constant and never reads the clock.  The same seed always produces the
/// same sequence of actions.
pub struct VeriLcg {
    state: u64,
}

#[allow(dead_code)]
impl VeriLcg {
    pub fn new(seed: u64) -> Self {
        VeriLcg { state: seed | 1 }
    }

    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.state;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.state = x;
        x.wrapping_mul(0x2545F4914F6CDD1D)
    }

    /// A value in `lo ..= hi`.
    pub fn next_range(&mut self, lo: i64, hi: i64) -> i64 {
        if hi <= lo {
            return lo;
        }
        let span = (hi - lo + 1) as u64;
        lo + (self.next_u64() % span) as i64
    }

    pub fn next_bool(&mut self) -> bool {
        self.next_u64() & 1 == 1
    }
}
"""


def gen_rust(spec: Spec, adapter: str | None = None) -> str:
    """Generate a Rust conformance test for a Rust adapter.

    `adapter` is the path of the adapter type, for example
    `"my_crate::BankAdapter"`.  The type must implement `VeriSpecAdapter` and
    have a `new()` constructor; when it is not given the generated factory
    panics with an explicit `todo!()` so the failure says what to do.
    """
    var_names = set(spec.vars)
    out: list[str] = []
    add = out.append

    add("// Generated by VeriSpec. DO NOT EDIT.")
    add("//")
    add(f"// Spec: {spec.name}")
    add("//")
    add("// Adapter contract (Rust):")
    add("//")
    add("//     impl verispec_adapter::VeriSpecAdapter for MyAdapter {")
    add("//         fn reset(&mut self);")
    add("//         fn apply(&mut self, action: &str, params: &HashMap<String, Value>);")
    add("//         fn snapshot(&self) -> Value;   // a JSON object with exactly the spec's variables")
    add("//     }")
    add("//")
    add("// The spec expressions below are transpiled from the same AST the Z3 checker")
    add("// uses, so the expected behaviour is the behaviour of the spec.")
    add("//")
    add("// Randomness: a deterministic 64 bit LCG (VeriLcg below).  No extra crate, no")
    add("// network, and every failure is reproducible.")
    add("//")
    add("// This file is *conformance-tested* evidence, not a proof: it shows that the")
    add("// adapter agrees with the spec on the states the run reaches.")
    add("")
    add("use std::collections::HashMap;")
    add("")
    add("use serde_json::{Map, Value};")
    add("use verispec_adapter::*;")
    add("")
    add(RUST_PRELUDE.strip())
    add("")

    # -- the initial state ------------------------------------------------
    init = state_to_json(spec.types, initial_state(spec))
    add("/// The initial state of the spec.")
    add("pub fn veri_spec_init() -> Value {")
    add("    let mut state = Map::new();")
    for name, value in init.items():
        add(f'    state.insert("{name}".to_string(), {_rust_json(value)});')
    add("    Value::Object(state)")
    add("}")
    add("")

    # -- enum tables ------------------------------------------------------
    enum_tables: list[tuple[str, list[str]]] = []
    for action in spec.actions:
        for pname, pvar in action.params.items():
            if isinstance(pvar.type, EnumType):
                enum_tables.append(
                    (f"VERI_ENUM_{_ident(action.name)}_{_ident(pname)}", list(pvar.type.values))
                )
    if enum_tables:
        add("// The finite value sets of the enum parameters.")
        for name, values in enum_tables:
            items = ", ".join(_rust_str(v) for v in values)
            add(f"const {name}: [&str; {len(values)}] = [{items}];")
        add("")

    # -- preconditions ----------------------------------------------------
    for action in spec.actions:
        tenv = spec.tenv(action)
        reqs = [to_rust(r, tenv, var_names) for r in action.requires] or ["true"]
        add(f"/// The preconditions of `{action.name}`.")
        add("#[allow(unused_parens, unused_variables)]")
        add(f"pub fn veri_requires_{_ident(action.name)}(state: &Value, params: &Value) -> bool {{")
        add("    " + " && ".join(f"({req})" for req in reqs))
        add("}")
        add("")

    # -- parameters -------------------------------------------------------
    for action in spec.actions:
        add(f"/// A random parameter set for `{action.name}`.")
        add(
            f"fn veri_params_{_ident(action.name)}(rng: &mut VeriLcg) -> HashMap<String, Value> {{"
        )
        add("    let mut params = HashMap::new();")
        for pname, pvar in action.params.items():
            kind = pvar.type.kind
            if kind == "int":
                lo, hi = _param_bounds(pvar)
                expr = f"Value::from(rng.next_range({lo}, {hi}))"
            elif kind == "bool":
                expr = "Value::from(rng.next_bool())"
            elif kind == "enum":
                table = f"VERI_ENUM_{_ident(action.name)}_{_ident(pname)}"
                count = len(pvar.type.values)
                expr = (
                    f"Value::from({table}[rng.next_range(0, {count - 1}) as usize].to_string())"
                )
            else:  # str
                expr = 'Value::from(format!("s{}", rng.next_range(0, 999)))'
            add(f'    params.insert("{pname}".to_string(), {expr});')
        add("    params")
        add("}")
        add("")

    # -- effects ----------------------------------------------------------
    for action in spec.actions:
        tenv = spec.tenv(action)
        for vname, expr in action.effects.items():
            var = spec.vars[vname]
            fname = f"veri_effect_{_ident(action.name)}_{_ident(vname)}"
            if isinstance(var.type, RecordType):
                add(f"/// The new value of `{vname}` after `{action.name}`.")
                add("#[allow(unused_parens, unused_variables)]")
                add(f"fn {fname}(state: &Value, params: &Value) -> Value {{")
                add(f'    let mut record = veri_as_value(veri_get(state, "{vname}"));')
                add("    if let Some(fields) = record.as_object_mut() {")
                for field_name, field_expr in expr.items():
                    add(
                        f'        fields.insert("{field_name}".to_string(), '
                        f"Value::from({to_rust(field_expr, tenv, var_names)}));"
                    )
                add("    }")
                add("    record")
                add("}")
            else:
                add(f"/// The new value of `{vname}` after `{action.name}`.")
                add("#[allow(unused_parens, unused_variables)]")
                add(f"fn {fname}(state: &Value, params: &Value) -> Value {{")
                add(f"    Value::from({to_rust(str(expr), tenv, var_names)})")
                add("}")
            add("")
        add(f"/// The state after `{action.name}`.")
        add(f"pub fn veri_effects_{_ident(action.name)}(state: &Value, params: &Value) -> Value {{")
        add("    let mut next = state.clone();")
        if action.effects:
            add("    if let Some(fields) = next.as_object_mut() {")
            for vname in action.effects:
                add(
                    f'        fields.insert("{vname}".to_string(), '
                    f"veri_effect_{_ident(action.name)}_{_ident(vname)}(state, params));"
                )
            add("    }")
        add("    next")
        add("}")
        add("")

    # -- invariants -------------------------------------------------------
    for inv in spec.invariants:
        add(f"/// The invariant `{inv['name']}`.")
        add("#[allow(unused_parens, unused_variables)]")
        add(
            f"pub fn veri_invariant_{_ident(inv['name'])}(state: &Value) -> bool {{"
        )
        add(f"    {to_rust(inv['expr'], spec.types, var_names)}")
        add("}")
        add("")
    add("/// Every invariant of the spec, checked against one state.")
    add("fn veri_check_invariants(state: &Value) -> Result<(), String> {")
    for inv in spec.invariants:
        add(f"    if !veri_invariant_{_ident(inv['name'])}(state) {{")
        add(
            f'        return Err(format!("invariant {inv["name"]} violated: {{}}", state));'
        )
        add("    }")
    add("    Ok(())")
    add("}")
    add("")

    # -- the action table -------------------------------------------------
    add("/// One action of the spec, with its precondition, its effect and a")
    add("/// generator for its parameters.")
    add("pub struct VeriAction {")
    add("    pub name: &'static str,")
    add("    pub requires: fn(&Value, &Value) -> bool,")
    add("    pub effects: fn(&Value, &Value) -> Value,")
    add("    pub params: fn(&mut VeriLcg) -> HashMap<String, Value>,")
    add("}")
    add("")
    add("/// Every action of the spec.")
    add("pub fn veri_actions() -> Vec<VeriAction> {")
    add("    vec![")
    for action in spec.actions:
        ident = _ident(action.name)
        add("        VeriAction {")
        add(f'            name: "{action.name}",')
        add(f"            requires: veri_requires_{ident},")
        add(f"            effects: veri_effects_{ident},")
        add(f"            params: veri_params_{ident},")
        add("        },")
    add("    ]")
    add("}")
    add("")

    # -- the driver -------------------------------------------------------
    add("/// Drive the adapter and compare every state with the spec.")
    add("///")
    add("/// Returns `Err` with a readable message when the adapter disagrees with the")
    add("/// spec, so the same function can be reused by an outer test harness.")
    add("pub fn run_conformance<F>(make: F) -> Result<(), String>")
    add("where")
    add("    F: Fn() -> Box<dyn VeriSpecAdapter>,")
    add("{")
    add("    let actions = veri_actions();")
    add(f"    let mut rng = VeriLcg::new(0x{SEED:016X});")
    add(f"    for iteration in 0..{ITERATIONS}u32 {{")
    add("        let mut adapter = make();")
    add("        adapter.reset();")
    add("        let mut previous = adapter.snapshot();")
    add("        let expected_init = veri_spec_init();")
    add("        if !veri_equal(&previous, &expected_init) {")
    add("            return Err(format!(")
    add(
        '                "iteration {}: after reset() the adapter state is {}, the spec says {}",'
    )
    add("                iteration,")
    add("                previous,")
    add("                expected_init,")
    add("            ));")
    add("        }")
    add("        veri_check_invariants(&previous)?;")
    add(f"        for step in 0..{STEPS}u32 {{")
    add("            let action = &actions[(rng.next_u64() % actions.len() as u64) as usize];")
    add("            let params = (action.params)(&mut rng);")
    add("            let params_value = Value::Object(")
    add("                params.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),")
    add("            );")
    add("            if !(action.requires)(&previous, &params_value) {")
    add("                continue;")
    add("            }")
    add("            let expected = (action.effects)(&previous, &params_value);")
    add("            adapter.apply(action.name, &params);")
    add("            let state = adapter.snapshot();")
    add("            if !veri_equal(&state, &expected) {")
    add("                return Err(format!(")
    add(
        '                    "iteration {} step {}: action {} with {} produced {}, the spec says {}",'
    )
    add("                    iteration,")
    add("                    step,")
    add("                    action.name,")
    add("                    params_value,")
    add("                    state,")
    add("                    expected,")
    add("                ));")
    add("            }")
    add("            veri_check_invariants(&state)?;")
    add("            previous = state;")
    add("        }")
    add("    }")
    add("    Ok(())")
    add("}")
    add("")

    # -- the factory ------------------------------------------------------
    add("/// The adapter under test.")
    add("///")
    add("/// Fill this in: it must return the adapter that implements the spec.")
    if adapter:
        add(f"/// The generated code assumes `{adapter}::new()` exists.")
        add("pub fn veri_make_adapter() -> Box<dyn VeriSpecAdapter> {")
        add(f"    Box::new({adapter}::new())")
        add("}")
    else:
        add("/// The generated code calls `todo!()`: the test fails until you fill it in.")
        add("pub fn veri_make_adapter() -> Box<dyn VeriSpecAdapter> {")
        add("    todo!(")
        add('        "veri_make_adapter() is not implemented: return your adapter here, "')
        add('            "for example Box::new(MyAdapter::new())"')
        add("    )")
        add("}")
    add("")

    add("#[test]")
    add("fn verispec_conformance() {")
    add("    if let Err(error) = run_conformance(veri_make_adapter) {")
    add('        panic!("verispec conformance failed: {}", error);')
    add("    }")
    add("}")
    return "\n".join(out) + "\n"

# --------------------------------------------------------------------------
# C
# --------------------------------------------------------------------------


def _c_type(t: TypeSpec) -> str:
    """The C type of a scalar spec type."""
    kind = getattr(t, "kind", None)
    if kind == "int":
        return "long long"
    if kind == "bool":
        return "int"
    if kind in ("str", "enum"):
        return "const char *"
    raise SpecError(f"the C adapter supports scalar specs only, got {type_name(t)}")


def _c_literal(t: TypeSpec, value: Any) -> str:
    """A C literal for a concrete value of a scalar type."""
    kind = getattr(t, "kind", None)
    if kind == "int":
        return f"{int(value)}LL"
    if kind == "bool":
        return "1" if value else "0"
    return _c_str(str(value))


def _c_unused(names: list[str]) -> list[str]:
    """The `(void)x;` lines that silence the unused parameter warnings."""
    return [f"    (void){name};" for name in names]


def _c_state_signature(spec: Spec) -> str:
    """The parameter list that carries the state of the spec."""
    return ", ".join(
        f"{_c_type(var.type)} {_ident(name)}" for name, var in spec.vars.items()
    )


def _c_action_signature(spec: Spec, action) -> str:
    """The parameter list that carries the state and the parameters."""
    parts = [
        f"{_c_type(var.type)} {_ident(name)}" for name, var in spec.vars.items()
    ]
    parts += [
        f"{_c_type(pvar.type)} {_ident(pname)}"
        for pname, pvar in action.params.items()
    ]
    return ", ".join(parts)


def _c_state_args(spec: Spec, prefix: str = "") -> str:
    """The state variables as a call argument list."""
    return ", ".join(f"{prefix}{_ident(name)}" for name in spec.vars)


def _c_action_args(spec: Spec, action, prefix: str = "") -> str:
    """The state variables and the parameters as a call argument list."""
    parts = [f"{prefix}{_ident(name)}" for name in spec.vars]
    parts += [f"{_ident(pname)}" for pname in action.params]
    return ", ".join(parts)


def gen_c(spec: Spec, adapter: str | None = None) -> str:
    """Generate a C conformance test for a C adapter.

    The C adapter supports scalar state only.  The adapter itself provides
    three functions:

        void veri_reset(void);
        void veri_apply(const char *action, const char *params_json);
        void veri_snapshot(char *out, size_t out_len);

    `veri_apply` receives the action name and the parameters as a JSON
    object, `veri_snapshot` writes the state as a JSON object with exactly the
    spec's variables.  `adapter` is accepted for symmetry with the other
    generators and documented in the header of the generated file.
    """
    _require_scalar_spec(spec, "C")
    var_names = set(spec.vars)
    state = initial_state(spec)
    out: list[str] = []
    add = out.append

    add("/* Generated by VeriSpec. DO NOT EDIT.")
    add(" *")
    add(f" * Spec: {spec.name}")
    add(" *")
    add(" * Adapter contract (C):")
    add(" *")
    add(" *     void veri_reset(void);")
    add(" *     void veri_apply(const char *action, const char *params_json);")
    add(" *     void veri_snapshot(char *out, size_t out_len);")
    add(" *")
    add(" * The spec expressions below are transpiled from the same AST the Z3 checker")
    add(" * uses, so the expected behaviour is the behaviour of the spec.")
    add(" *")
    add(" * Randomness: a deterministic 64 bit LCG (veri_rng in verispec.h).  Every")
    add(" * failure is reproducible.")
    add(" *")
    add(" * This file is *conformance-tested* evidence, not a proof.")
    add(" */")
    add("")
    add("#include <stdio.h>")
    add("#include <stdlib.h>")
    add("#include <string.h>")
    add("")
    add('#include "verispec.h"')
    add("")
    add("/* The adapter, implemented by the user in another translation unit. */")
    add("void veri_reset(void);")
    add("void veri_apply(const char *action, const char *params_json);")
    add("void veri_snapshot(char *out, size_t out_len);")
    add("")

    # -- the factory ------------------------------------------------------
    add("/* The hook where you plug your adapter in.")
    add(" *")
    add(" * The default implementation does nothing: the conformance run calls")
    add(" * veri_reset() before it uses the adapter.  If your adapter needs a setup")
    add(" * step, edit the body below.  If you provide veri_make_adapter() in your")
    add(" * own translation unit, compile this file with -DVERISPEC_NO_DEFAULT_FACTORY")
    add(" * so the default does not clash with yours.")
    add(" */")
    add("#ifndef VERISPEC_NO_DEFAULT_FACTORY")
    add("void veri_make_adapter(void) {")
    add("    /* TODO(user): initialise your adapter here. */")
    add("}")
    add("#endif")
    add("")

    # -- the preconditions ------------------------------------------------
    for action in spec.actions:
        tenv = spec.tenv(action)
        reqs = [to_c(r, tenv, var_names) for r in action.requires] or ["1"]
        add(f"/* The preconditions of `{action.name}`. */")
        add(f"static int veri_requires_{_ident(action.name)}({_c_action_signature(spec, action)}) {{")
        for line in _c_unused([_ident(n) for n in spec.vars] + [_ident(p) for p in action.params]):
            add(line)
        add("    return " + " && ".join(reqs) + ";")
        add("}")
        add("")

    # -- the effects ------------------------------------------------------
    for action in spec.actions:
        tenv = spec.tenv(action)
        outs = [f"{_c_type(spec.vars[v].type)} *out_{_ident(v)}" for v in action.effects]
        signature = ", ".join([_c_action_signature(spec, action)] + outs)
        add(f"/* The state after `{action.name}`. */")
        add(f"static void veri_effects_{_ident(action.name)}({signature}) {{")
        for line in _c_unused([_ident(n) for n in spec.vars] + [_ident(p) for p in action.params]):
            add(line)
        for vname, expr in action.effects.items():
            add(f"    *out_{_ident(vname)} = {to_c(str(expr), tenv, var_names)};")
        add("}")
        add("")

    # -- the invariants ---------------------------------------------------
    for inv in spec.invariants:
        add(f"/* The invariant `{inv['name']}`. */")
        add(f"static int veri_invariant_{_ident(inv['name'])}({_c_state_signature(spec)}) {{")
        for line in _c_unused([_ident(n) for n in spec.vars]):
            add(line)
        add(f"    return {to_c(inv['expr'], spec.types, var_names)};")
        add("}")
        add("")

    # -- the expected state -----------------------------------------------
    add("/* The state the spec expects, as a JSON object. */")
    add(f"static veri_value *veri_expected_state({_c_state_signature(spec)}) {{")
    for line in _c_unused([_ident(n) for n in spec.vars]):
        add(line)
    add("    veri_value *state = veri_value_new_object();")
    add("    if (state == NULL) {")
    add('        fprintf(stderr, "verispec: out of memory\\n");')
    add("        return NULL;")
    add("    }")
    for name, var in spec.vars.items():
        if var.type.kind == "int":
            add(
                f'    veri_object_set(state, "{name}", veri_value_new_int({_ident(name)}));'
            )
        elif var.type.kind == "bool":
            add(
                f'    veri_object_set(state, "{name}", veri_value_new_bool({_ident(name)}));'
            )
        else:
            add(
                f'    veri_object_set(state, "{name}", veri_value_new_str({_ident(name)}));'
            )
    add("    return state;")
    add("}")
    add("")

    # -- the state check --------------------------------------------------
    add("/* Compare the adapter state with the state the spec expects. */")
    add(f"static int veri_check_state({_c_state_signature(spec)}, const char *where) {{")
    for line in _c_unused([_ident(n) for n in spec.vars]):
        add(line)
    add("    char buffer[8192];")
    add("    veri_value *expected;")
    add("    veri_value *got;")
    add("    int ok;")
    add("")
    add("    veri_snapshot(buffer, sizeof buffer);")
    add(f"    expected = veri_expected_state({_c_state_args(spec)});")
    add("    got = veri_json_parse(buffer);")
    add("    if (got == NULL) {")
    add(
        '        fprintf(stderr, "verispec: %s: the adapter snapshot is not valid JSON: %s\\n",'
    )
    add("                where, buffer);")
    add("        veri_value_free(expected);")
    add("        return 0;")
    add("    }")
    add("    ok = veri_value_equal(expected, got);")
    add("    if (!ok) {")
    add(
        '        fprintf(stderr, "verispec: %s: the adapter state does not match the spec\\n",'
    )
    add("                where);")
    add("        {")
    add("            char *text = veri_json_write(expected);")
    add('            fprintf(stderr, "  expected: %s\\n", text ? text : "(null)");')
    add("            free(text);")
    add("        }")
    add("        {")
    add("            char *text = veri_json_write(got);")
    add('            fprintf(stderr, "  got:      %s\\n", text ? text : "(null)");')
    add("            free(text);")
    add("        }")
    add("    }")
    add("    veri_value_free(expected);")
    add("    veri_value_free(got);")
    add("    return ok;")
    add("}")
    add("")

    # -- the driver -------------------------------------------------------
    add("int main(void) {")
    add("    veri_rng rng;")
    for name, var in spec.vars.items():
        add(
            f"    {_c_type(var.type)} {_ident(name)} = "
            f"{_c_literal(var.type, state[name])};"
        )
    add("    int iteration;")
    add("    int step;")
    add("")
    add("    veri_make_adapter();")
    add(f"    veri_rng_init(&rng, 0x{SEED:016X}ULL);")
    add("")
    add(f"    for (iteration = 0; iteration < {ITERATIONS}; iteration++) {{")
    add("        veri_reset();")
    for name, var in spec.vars.items():
        add(f"        {_ident(name)} = {_c_literal(var.type, state[name])};")
    add(f'        if (!veri_check_state({_c_state_args(spec)}, "after reset()")) {{')
    add("            return 1;")
    add("        }")
    add(f"        for (step = 0; step < {STEPS}; step++) {{")
    if spec.actions:
        add(f"            switch ((int)(veri_rng_next(&rng) % {len(spec.actions)}u)) {{")
        for index, action in enumerate(spec.actions):
            add(f"            case {index}: {{")
            add(f'                const char *action = "{action.name}";')
            add("                char params_json[1024];")
            for pname, pvar in action.params.items():
                ptype = pvar.type
                if ptype.kind == "int":
                    lo, hi = _param_bounds(pvar)
                    add(
                        f"                long long {_ident(pname)} = "
                        f"veri_rng_range(&rng, {lo}, {hi});"
                    )
                elif ptype.kind == "bool":
                    add(f"                int {_ident(pname)} = veri_rng_bool(&rng);")
                    add(
                        f"                const char *{_ident(pname)}_json = "
                        f'{_ident(pname)} ? "true" : "false";'
                    )
                elif ptype.kind == "enum":
                    values = list(ptype.values)
                    items = ", ".join(_c_str(v) for v in values)
                    add(
                        f"                static const char *const "
                        f"VERI_ENUM_{_ident(action.name)}_{_ident(pname)}[] = {{{items}}};"
                    )
                    add(
                        f"                const char *{_ident(pname)} = "
                        f"VERI_ENUM_{_ident(action.name)}_{_ident(pname)}"
                        f"[veri_rng_range(&rng, 0, {len(values) - 1})];"
                    )
                    add(f"                char {_ident(pname)}_json[256];")
                    add(
                        f"                veri_json_quote({_ident(pname)}, "
                        f"{_ident(pname)}_json, sizeof {_ident(pname)}_json);"
                    )
                else:
                    add(
                        f"                char {_ident(pname)}_value[64];"
                    )
                    add(
                        f"                snprintf({_ident(pname)}_value, "
                        f'sizeof {_ident(pname)}_value, "s%lld", '
                        f"veri_rng_range(&rng, 0, 999));"
                    )
                    add(
                        f"                const char *{_ident(pname)} = {_ident(pname)}_value;"
                    )
                    add(f"                char {_ident(pname)}_json[256];")
                    add(
                        f"                veri_json_quote({_ident(pname)}, "
                        f"{_ident(pname)}_json, sizeof {_ident(pname)}_json);"
                    )
            # the parameters, as a JSON object
            if action.params:
                fmt_parts = []
                fmt_args = []
                for pname, pvar in action.params.items():
                    if pvar.type.kind == "int":
                        fmt_parts.append(f'\\"{pname}\\": %lld')
                        fmt_args.append(_ident(pname))
                    elif pvar.type.kind == "bool":
                        fmt_parts.append(f'\\"{pname}\\": %s')
                        fmt_args.append(f"{_ident(pname)}_json")
                    else:
                        fmt_parts.append(f'\\"{pname}\\": %s')
                        fmt_args.append(f"{_ident(pname)}_json")
                add(
                    f'                snprintf(params_json, sizeof params_json, "{{{", ".join(fmt_parts)}}}",'
                )
                add("                        " + ", ".join(fmt_args) + ");")
            else:
                add('                snprintf(params_json, sizeof params_json, "{}");')
            # the next state
            for name in spec.vars:
                add(
                    f"                {_c_type(spec.vars[name].type)} next_{_ident(name)} = "
                    f"{_ident(name)};"
                )
            # the precondition
            add(
                f"                if (!veri_requires_{_ident(action.name)}"
                f"({_c_action_args(spec, action)})) {{"
            )
            add("                    break;")
            add("                }")
            # the effect
            eff_args = _c_action_args(spec, action)
            if action.effects:
                eff_args += ", " + ", ".join(f"&next_{_ident(v)}" for v in action.effects)
            add(f"                veri_effects_{_ident(action.name)}({eff_args});")
            add("                veri_apply(action, params_json);")
            add(
                f"                if (!veri_check_state({_c_state_args(spec, 'next_')}, action)) {{"
            )
            add("                    return 1;")
            add("                }")
            for inv in spec.invariants:
                add(
                    f"                if (!veri_invariant_{_ident(inv['name'])}"
                    f"({_c_state_args(spec, 'next_')})) {{"
                )
                add(
                    f'                    fprintf(stderr, "verispec: invariant {inv["name"]} '
                    'violated\\n");'
                )
                add("                    return 1;")
                add("                }")
            for name in spec.vars:
                add(f"                {_ident(name)} = next_{_ident(name)};")
            add("                break;")
            add("            }")
        add("            default:")
        add("                break;")
        add("            }")
    add("        }")
    add("    }")
    add('    printf("verispec conformance: OK\\n");')
    add("    return 0;")
    add("}")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# dispatcher
# --------------------------------------------------------------------------


def gen_external(
    spec: Spec, lang: str, adapter: str | None = None, package: str | None = None
) -> str:
    """Generate a conformance test for a language VeriSpec does not ship itself."""
    if lang == "rust":
        return gen_rust(spec, adapter=adapter)
    if lang == "c":
        return gen_c(spec, adapter=adapter)
    raise SpecError(
        f"unknown language {lang!r}: the external generators cover "
        f"{', '.join(EXTERNAL_LANGUAGES)}"
    )
