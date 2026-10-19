#!/usr/bin/env python3
"""Reference implementation of the VeriSpec Java/Kotlin template filling.

The real generator lives in ``src/verispec/gentests.py``.  This script is the
*executable specification* of the ``{{PLACEHOLDER}}`` contract of
``adapters/java/templates/conformance.java.txt`` and
``conformance.kt.txt``: it fills the placeholders for a scalar spec and writes
the result, so that the templates can be compiled and run.

It is deliberately small: it handles the scalar subset of the spec language
(int, bool, str, enum) and refuses anything else with a clear error.  The
real generator is the one that has to handle every construct.

    python adapters/java/tools/render_template.py examples/bank.yaml \\
        --lang java -o /tmp/BankVeriSpecTest.java
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE.parent / "templates"


# --------------------------------------------------------------------------
# spec reading (the tiny subset this reference filler needs)
# --------------------------------------------------------------------------


def load_spec(path: str) -> dict:
    import yaml

    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part.title() for part in parts[1:])


def type_of(raw) -> dict:
    if isinstance(raw, str):
        return {"type": raw}
    return dict(raw)


# --------------------------------------------------------------------------
# expression translation (scalars only)
# --------------------------------------------------------------------------


def translate(expr: str, vars_: dict, params: dict) -> str:
    """Translate a spec expression into a Java/Kotlin expression."""
    tree = ast.parse(expr, mode="eval")

    def walk(node) -> str:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool):
                return "true" if value else "false"
            if isinstance(value, str):
                return '"' + value.replace('"', '\\"') + '"'
            return str(value)
        if isinstance(node, ast.Name):
            if node.id in vars_:
                return f"{camel(node.id)}(state)"
            if node.id in params:
                return f"{camel(node.id)}(params)"
            raise ValueError(f"unknown name in expression: {node.id}")
        if isinstance(node, ast.BoolOp):
            op = " && " if isinstance(node.op, ast.And) else " || "
            return "(" + op.join(walk(v) for v in node.values) + ")"
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return f"(!{walk(node.operand)})"
            return f"(-{walk(node.operand)})"
        if isinstance(node, ast.BinOp):
            left = walk(node.left)
            right = walk(node.right)
            if isinstance(node.op, ast.Add):
                return f"({left} + {right})"
            if isinstance(node.op, ast.Sub):
                return f"({left} - {right})"
            if isinstance(node.op, ast.Mult):
                return f"({left} * {right})"
            if isinstance(node.op, ast.FloorDiv):
                return f"Math.floorDiv({left}, {right})"
            if isinstance(node.op, ast.Mod):
                return f"({left} % {right})"
            raise ValueError("unsupported operator in expression")
        if isinstance(node, ast.Compare):
            left = walk(node.left)
            parts = []
            for op, comp in zip(node.ops, node.comparators):
                right = walk(comp)
                if isinstance(op, ast.Eq):
                    parts.append(f"({left} == {right})")
                elif isinstance(op, ast.NotEq):
                    parts.append(f"({left} != {right})")
                elif isinstance(op, ast.Lt):
                    parts.append(f"({left} < {right})")
                elif isinstance(op, ast.LtE):
                    parts.append(f"({left} <= {right})")
                elif isinstance(op, ast.Gt):
                    parts.append(f"({left} > {right})")
                elif isinstance(op, ast.GtE):
                    parts.append(f"({left} >= {right})")
                else:
                    raise ValueError("unsupported comparison in expression")
                left = right
            return " && ".join(parts)
        raise ValueError(f"unsupported expression node: {type(node).__name__}")

    return walk(tree)


# --------------------------------------------------------------------------
# the blocks
# --------------------------------------------------------------------------


def java_state(spec: dict) -> str:
    import json

    lines = []
    init = spec.get("init") or {}
    entries = []
    for name, raw in init.items():
        value = raw
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            value = int(value.strip())
        entries.append(f"{json.dumps(name)}, {json.dumps(value)}")
    lines.append("    private static final Map<String, Object> SPEC_INIT = Map.<String, Object>of(")
    lines.append("            " + ",\n            ".join(entries) + ");")
    lines.append("")
    for name, raw in (spec.get("vars") or {}).items():
        kind = type_of(raw).get("type")
        fn = camel(name)
        if kind == "int":
            lines.append(f"    private static int {fn}(Map<String, Object> state) {{")
            lines.append(f'        return ((Number) state.get("{name}")).intValue();')
        elif kind == "bool":
            lines.append(f"    private static boolean {fn}(Map<String, Object> state) {{")
            lines.append(f'        return (Boolean) state.get("{name}");')
        else:
            lines.append(f"    private static String {fn}(Map<String, Object> state) {{")
            lines.append(f'        return (String) state.get("{name}");')
        lines.append("    }")
        lines.append("")
    return "\n".join(lines).rstrip()


def java_actions(spec: dict) -> str:
    vars_ = spec.get("vars") or {}
    out = []
    # One accessor per parameter name, not per action: two actions may share
    # a parameter.
    seen_params: set[str] = set()
    for action in spec.get("actions") or []:
        params = action.get("params") or {}
        for pname, praw in params.items():
            if pname in seen_params:
                continue
            seen_params.add(pname)
            out.append(f"    private static int {camel(pname)}(Map<String, Object> params) {{")
            out.append(f'        return ((Number) params.get("{pname}")).intValue();')
            out.append("    }")
            out.append("")
    for action in spec.get("actions") or []:
        name = action["name"]
        fn = camel(name)
        params = action.get("params") or {}
        requires = action.get("requires") or []
        out.append(
            f"    private static boolean requires{fn[0].upper() + fn[1:]}"
            "(Map<String, Object> state, Map<String, Object> params) {"
        )
        if requires:
            exprs = [translate(r, vars_, params) for r in requires]
            out.append("        return " + " && ".join(exprs) + ";")
        else:
            out.append("        return true;")
        out.append("    }")
        out.append("")
        out.append(
            f"    private static Map<String, Object> effects{fn[0].upper() + fn[1:]}"
            "(Map<String, Object> state, Map<String, Object> params) {"
        )
        out.append("        Map<String, Object> next = new LinkedHashMap<>(state);")
        for vname, vexpr in (action.get("effects") or {}).items():
            out.append(
                f'        next.put("{vname}", {translate(vexpr, vars_, params)});'
            )
        out.append("        return next;")
        out.append("    }")
        out.append("")
    out.append("    private static boolean requires(String action, Map<String, Object> state,")
    out.append("            Map<String, Object> params) {")
    out.append("        switch (action) {")
    for action in spec.get("actions") or []:
        fn = camel(action["name"])
        out.append(f'            case "{action["name"]}":')
        out.append(f"                return requires{fn[0].upper() + fn[1:]}(state, params);")
    out.append("            default:")
    out.append('                throw new IllegalArgumentException("unknown action: " + action);')
    out.append("        }")
    out.append("    }")
    out.append("")
    out.append("    private static Map<String, Object> effects(String action, Map<String, Object> state,")
    out.append("            Map<String, Object> params) {")
    out.append("        switch (action) {")
    for action in spec.get("actions") or []:
        fn = camel(action["name"])
        out.append(f'            case "{action["name"]}":')
        out.append(
            f"                return effects{fn[0].upper() + fn[1:]}(state, params);"
        )
    out.append("            default:")
    out.append('                throw new IllegalArgumentException("unknown action: " + action);')
    out.append("        }")
    out.append("    }")
    return "\n".join(out)


def java_invariants(spec: dict) -> str:
    vars_ = spec.get("vars") or {}
    out = ["    private static void checkInvariants(Map<String, Object> state, String context) {"]
    for inv in spec.get("invariants") or []:
        expr = translate(inv["expr"], vars_, {})
        out.append("        Assertions.assertTrue(")
        out.append(f"                {expr},")
        out.append(
            f'                "invariant {inv["name"]} violated at " + context + ": "'
        )
        literal = inv["expr"].replace('"', '\\"')
        out.append(f'                        + "{literal}" + " in " + state);')
    out.append("    }")
    return "\n".join(out)


def java_adapter_factory() -> str:
    return """    private static VeriSpecAdapter newAdapter() {
        String className = System.getProperty("verispec.adapter",
                System.getenv("VERISPEC_ADAPTER"));
        if (className == null || className.isBlank()) {
            throw new IllegalStateException(
                    "EDIT ME: set verispec.adapter to the class implementing VeriSpecAdapter");
        }
        try {
            return (VeriSpecAdapter) Class.forName(className.trim())
                    .getDeclaredConstructor().newInstance();
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("cannot instantiate " + className, e);
        }
    }"""


def java_strategies(spec: dict) -> str:
    out = ["    @Provide", "    public Arbitrary<List<Step>> steps() {"]
    actions = spec.get("actions") or []
    out.append(f"        Arbitrary<Step> step = Arbitraries.integers().between(0, {len(actions) - 1})")
    out.append("                .flatMap(i -> {")
    out.append("                    switch (i) {")
    for index, action in enumerate(actions):
        fn = camel(action["name"])
        out.append(f"                        case {index}:")
        out.append(f'                            return Arbitraries.just("{action["name"]}")')
        out.append(f"                                    .flatMap(action -> params{fn[0].upper() + fn[1:]}()")
        out.append("                                            .map(params -> new Step(action, params)));")
    out.append("                        default:")
    out.append('                            throw new IllegalStateException("no action for index " + i);')
    out.append("                    }")
    out.append("                });")
    out.append("        return step.list().ofMinSize(1).ofMaxSize(25);")
    out.append("    }")
    for action in actions:
        fn = camel(action["name"])
        out.append("")
        out.append(
            f"    private static Arbitrary<Map<String, Object>> params{fn[0].upper() + fn[1:]}() {{"
        )
        parts = []
        for pname, praw in (action.get("params") or {}).items():
            kind = type_of(praw).get("type")
            if kind == "bool":
                parts.append(("Arbitraries.booleans()", pname))
            elif kind == "enum":
                values = type_of(praw).get("values", [])
                literal = ", ".join(f'"{v}"' for v in values)
                parts.append((f"Arbitraries.of({literal})", pname))
            else:
                lo = type_of(praw).get("min", 0)
                hi = type_of(praw).get("max", lo + 20)
                parts.append((f"Arbitraries.integers().between({lo}, {hi})", pname))
        if len(parts) == 1:
            source, pname = parts[0]
            out.append(
                f"        return {source}.map(v -> Map.<String, Object>of(\"{pname}\", v));"
            )
        else:
            out.append("        return Combinators.combine(")
            for source, _ in parts:
                out.append(f"                {source},")
            out[-1] = out[-1].rstrip(",")
            out.append("        ).as(t -> Map.<String, Object>of(")
            for index, (_, pname) in enumerate(parts):
                out.append(f'                "{pname}", t.get{index + 1}(),')
            out[-1] = out[-1].rstrip(",")
            out.append("        ));")
        out.append("    }")
    return "\n".join(out)


# --------------------------------------------------------------------------
# the Kotlin blocks
# --------------------------------------------------------------------------


def kotlin_state(spec: dict) -> str:
    lines = []
    init = spec.get("init") or {}
    entries = []
    for name, raw in init.items():
        value = raw
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            value = int(value.strip())
        entries.append(f'"{name}" to {value}')
    lines.append("    private val SPEC_INIT: Map<String, Any?> = mapOf<String, Any?>(")
    lines.append("            " + ",\n            ".join(entries) + ")")
    lines.append("")
    for name, raw in (spec.get("vars") or {}).items():
        kind = type_of(raw).get("type")
        fn = camel(name)
        if kind == "int":
            lines.append(f"    private fun {fn}(state: Map<String, Any?>): Int =")
            lines.append(f'            (state["{name}"] as Number).toInt()')
        elif kind == "bool":
            lines.append(f"    private fun {fn}(state: Map<String, Any?>): Boolean =")
            lines.append(f'            state["{name}"] as Boolean')
        else:
            lines.append(f"    private fun {fn}(state: Map<String, Any?>): String =")
            lines.append(f'            state["{name}"] as String')
        lines.append("")
    return "\n".join(lines).rstrip()


def kotlin_actions(spec: dict) -> str:
    vars_ = spec.get("vars") or {}
    out = []
    # One accessor per parameter name, not per action: two actions may share
    # a parameter.
    seen_params: set[str] = set()
    for action in spec.get("actions") or []:
        params = action.get("params") or {}
        for pname, praw in params.items():
            if pname in seen_params:
                continue
            seen_params.add(pname)
            out.append(f"    private fun {camel(pname)}(params: Map<String, Any>): Int =")
            out.append(f'            (params["{pname}"] as Number).toInt()')
            out.append("")
    for action in spec.get("actions") or []:
        name = action["name"]
        fn = camel(name)
        upper = fn[0].upper() + fn[1:]
        params = action.get("params") or {}
        requires = action.get("requires") or []
        out.append(
            f"    private fun requires{upper}(state: Map<String, Any?>,"
            " params: Map<String, Any>): Boolean {"
        )
        if requires:
            exprs = [translate(r, vars_, params) for r in requires]
            out.append("        return " + " && ".join(exprs))
        else:
            out.append("        return true")
        out.append("    }")
        out.append("")
        out.append(
            f"    private fun effects{upper}(state: Map<String, Any?>,"
            " params: Map<String, Any>): Map<String, Any?> {"
        )
        out.append("        val next = LinkedHashMap<String, Any?>(state)")
        for vname, vexpr in (action.get("effects") or {}).items():
            out.append(f'        next["{vname}"] = {translate(vexpr, vars_, params)}')
        out.append("        return next")
        out.append("    }")
        out.append("")
    out.append(
        "    private fun requires(action: String, state: Map<String, Any?>,"
        " params: Map<String, Any>): Boolean {"
    )
    out.append("        return when (action) {")
    for action in spec.get("actions") or []:
        fn = camel(action["name"])
        out.append(f'            "{action["name"]}" -> requires{fn[0].upper() + fn[1:]}(state, params)')
    out.append('            else -> throw IllegalArgumentException("unknown action: $action")')
    out.append("        }")
    out.append("    }")
    out.append("")
    out.append(
        "    private fun effects(action: String, state: Map<String, Any?>,"
        " params: Map<String, Any>): Map<String, Any?> {"
    )
    out.append("        return when (action) {")
    for action in spec.get("actions") or []:
        fn = camel(action["name"])
        out.append(f'            "{action["name"]}" -> effects{fn[0].upper() + fn[1:]}(state, params)')
    out.append('            else -> throw IllegalArgumentException("unknown action: $action")')
    out.append("        }")
    out.append("    }")
    return "\n".join(out)


def kotlin_invariants(spec: dict) -> str:
    vars_ = spec.get("vars") or {}
    out = ["    private fun checkInvariants(state: Map<String, Any?>, context: String) {"]
    for inv in spec.get("invariants") or []:
        expr = translate(inv["expr"], vars_, {})
        out.append("        assertTrue(")
        out.append(f"                {expr},")
        out.append(
            f'                "invariant {inv["name"]} violated at $context: '
            f'{inv["expr"]} in $state")'
        )
    out.append("    }")
    return "\n".join(out)


def kotlin_adapter_factory() -> str:
    return """    private fun newAdapter(): VeriSpecAdapter {
        val className = System.getProperty("verispec.adapter",
                System.getenv("VERISPEC_ADAPTER"))
        if (className.isNullOrBlank()) {
            throw IllegalStateException(
                    "EDIT ME: set verispec.adapter to the class implementing VeriSpecAdapter")
        }
        return try {
            Class.forName(className.trim()).getDeclaredConstructor()
                    .newInstance() as VeriSpecAdapter
        } catch (e: ReflectiveOperationException) {
            throw IllegalStateException("cannot instantiate $className", e)
        }
    }"""


def kotlin_strategies(spec: dict) -> str:
    out = ["    @Provide", "    fun steps(): Arbitrary<List<Step>> {"]
    actions = spec.get("actions") or []
    out.append("        val step: Arbitrary<Step> = Arbitraries.integers()")
    out.append(f"                .between(0, {len(actions) - 1})")
    out.append("                .flatMap { i ->")
    out.append("                    when (i) {")
    for index, action in enumerate(actions):
        fn = camel(action["name"])
        out.append(f"                        {index} -> Arbitraries.just(\"{action['name']}\")")
        out.append(f"                                .flatMap {{ action -> params{fn[0].upper() + fn[1:]}()")
        out.append("                                        .map { params -> Step(action, params) } }")
    out.append("                        else -> throw IllegalStateException(")
    out.append('                                "no action for index $i")')
    out.append("                    }")
    out.append("                }")
    out.append("        return step.list().ofMinSize(1).ofMaxSize(25)")
    out.append("    }")
    for action in actions:
        fn = camel(action["name"])
        out.append("")
        out.append(
            f"    private fun params{fn[0].upper() + fn[1:]}(): Arbitrary<Map<String, Any>> ="
        )
        parts = []
        for pname, praw in (action.get("params") or {}).items():
            kind = type_of(praw).get("type")
            if kind == "bool":
                parts.append(("Arbitraries.booleans()", pname))
            elif kind == "enum":
                values = type_of(praw).get("values", [])
                literal = ", ".join(f'"{v}"' for v in values)
                parts.append((f"Arbitraries.of({literal})", pname))
            else:
                lo = type_of(praw).get("min", 0)
                hi = type_of(praw).get("max", lo + 20)
                parts.append((f"Arbitraries.integers().between({lo}, {hi})", pname))
        if len(parts) == 1:
            source, pname = parts[0]
            out.append(f"            {source}")
            out.append(f'                    .map {{ v -> mapOf<String, Any>("{pname}" to v) }}')
        else:
            out.append("            Combinators.combine(")
            for source, _ in parts:
                out.append(f"                    {source},")
            out[-1] = out[-1].rstrip(",")
            out.append("            ).as { t ->")
            out.append("                mapOf<String, Any>(")
            for index, (_, pname) in enumerate(parts):
                out.append(f'                    "{pname}" to t.get{index + 1}(),')
            out[-1] = out[-1].rstrip(",")
            out.append("                )")
            out.append("            }")
    return "\n".join(out)


# --------------------------------------------------------------------------
# the rendering
# --------------------------------------------------------------------------


def render_java(spec: dict, package: str, class_name: str) -> str:
    template = (TEMPLATES / "conformance.java.txt").read_text(encoding="utf-8")
    blocks = {
        "SPEC_NAME": spec.get("name", "Unnamed"),
        "PACKAGE": package,
        "CLASS_NAME": class_name,
        "STATE_FIELDS": java_state(spec),
        "ACTION_FUNCTIONS": java_actions(spec),
        "INVARIANT_FUNCTIONS": java_invariants(spec),
        "ADAPTER_FACTORY": java_adapter_factory(),
        "PARAM_STRATEGIES": java_strategies(spec),
    }
    for key, value in blocks.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def render_kotlin(spec: dict, package: str, class_name: str) -> str:
    template = (TEMPLATES / "conformance.kt.txt").read_text(encoding="utf-8")
    blocks = {
        "SPEC_NAME": spec.get("name", "Unnamed"),
        "PACKAGE": package,
        "CLASS_NAME": class_name,
        "STATE_FIELDS": kotlin_state(spec),
        "ACTION_FUNCTIONS": kotlin_actions(spec),
        "INVARIANT_FUNCTIONS": kotlin_invariants(spec),
        "ADAPTER_FACTORY": kotlin_adapter_factory(),
        "PARAM_STRATEGIES": kotlin_strategies(spec),
    }
    for key, value in blocks.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec")
    parser.add_argument("--lang", default="java", choices=["java", "kotlin"])
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--package", default="verispec.conformance")
    parser.add_argument("--class-name", default=None)
    args = parser.parse_args()

    spec = load_spec(args.spec)
    class_name = args.class_name or (camel(spec.get("name", "Spec")) + "VeriSpecTest")
    if args.lang == "java":
        code = render_java(spec, args.package, class_name)
    else:
        code = render_kotlin(spec, args.package, class_name)
    out = Path(args.output)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(code, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
