"""Single error type used across VeriSpec.

Kept in its own module so that `types`, `expr` and `model` can import it
without creating an import cycle. `verispec.expr.SpecError` is kept as an
alias for backwards compatibility.
"""


class SpecError(Exception):
    """Raised for any invalid spec, expression, or misuse of VeriSpec."""
