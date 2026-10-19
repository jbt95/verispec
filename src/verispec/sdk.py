"""The Python trace recorder SDK.

A trace is how a real implementation reports what it did, in the language
agnostic JSON format that `verispec check-trace` replays.

```python
from verispec.model import load_spec
from verispec.sdk import TraceRecorder

spec = load_spec("specs/bank.verispec.yaml")
recorder = TraceRecorder(spec, adapter=BankAdapter())
recorder.reset()
recorder.apply("transfer_a_to_b", amount=20)
recorder.write("traces/session.json")
```

The same SDK exists for TypeScript, Go, Rust, C and Java: see
`docs/language-integrations.md`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import Spec
from .values import state_to_json

__all__ = ["TraceRecorder", "record_trace"]


class TraceRecorder:
    """Record the steps an adapter takes, in the canonical trace format."""

    def __init__(self, spec: Spec, adapter: Any = None):
        self.spec = spec
        self.adapter = adapter
        self.steps: list[dict[str, Any]] = []

    # -- recording --------------------------------------------------------

    def reset(self) -> None:
        """Reset the adapter and record the initial state."""
        if self.adapter is not None:
            self.adapter.reset()
        self.steps = []
        self._append("init", {}, self._snapshot())

    def apply(self, action: str, **params: Any) -> None:
        """Apply an action through the adapter and record the step."""
        if self.adapter is None:
            raise RuntimeError("this recorder has no adapter; use record() instead")
        self.adapter.apply(action, **params)
        self._append(action, params, self._snapshot())

    def record(self, action: str, params: dict[str, Any] | None = None, state: dict[str, Any] | None = None) -> None:
        """Record a step explicitly (useful when the implementation is not an adapter)."""
        if state is None:
            state = self._snapshot()
        self._append(action, params or {}, state)

    def _append(self, action: str, params: dict[str, Any], state: dict[str, Any]) -> None:
        self.steps.append(
            {
                "action": action,
                "params": dict(params),
                "state": state_to_json(self.spec.types, state),
            }
        )

    def _snapshot(self) -> dict[str, Any]:
        if self.adapter is None:
            raise RuntimeError("this recorder has no adapter; use record() instead")
        return self.adapter.snapshot()

    # -- output -----------------------------------------------------------

    def to_json(self, indent: int = 2) -> str:
        """The trace as a JSON document."""
        return json.dumps(self.steps, indent=indent)

    def write(self, path: str | Path) -> Path:
        """Write the trace to a file."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.to_json() + "\n")
        return out

    def __len__(self) -> int:
        return len(self.steps)


def record_trace(spec: Spec, adapter: Any, actions: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Record a sequence of actions with an adapter and return the trace."""
    recorder = TraceRecorder(spec, adapter)
    recorder.reset()
    for action, params in actions:
        recorder.apply(action, **params)
    return recorder.steps
