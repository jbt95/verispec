"""VeriSpec: spec-driven development with formal teeth.

One human owned spec produces:

* bounded model checking with Z3 (and concrete counterexamples),
* TLA+ and Lean exports,
* generated property tests for the adapter, in any language,
* trace conformance checks for real implementations, in any language,
* a brief for coding agents and an MCP server to verify against.

The spec format is the public contract; this implementation is one
implementation of it.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
