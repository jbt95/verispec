"""Spec templates used by `verispec init`.

Each template ships a spec, an adapter skeleton in the chosen language and a
short README.  The templates are the fastest path from "we have an idea" to
"we have a counterexample".
"""

from __future__ import annotations

from typing import Any

__all__ = ["template_names", "build_template"]


_LEDGER = """\
spec_version: "1.1"
name: Ledger
description: >
  A double entry ledger with two accounts.  Every posting moves value between
  accounts, the total is conserved, and no account may go negative.
vars:
  balances:
    type: map
    key: int
    key_min: 0
    key_max: 1
    value: int
    value_min: 0
    value_max: 1000
    max_size: 2
  total: {type: int, min: 0, max: 1000}
init:
  balances: {0: 100, 1: 0}
  total: "100"
actions:
  - name: transfer
    params:
      from_account: {type: int, min: 0, max: 1}
      to_account: {type: int, min: 0, max: 1}
      amount: {type: int, min: 1, max: 100}
    requires:
      - "amount > 0"
      - "from_account != to_account"
      - "map_get(balances, from_account) >= amount"
    effects:
      balances: >
        map_set(
          map_set(balances, from_account, map_get(balances, from_account) - amount),
          to_account, map_get(balances, to_account) + amount)
      total: "total"
invariants:
  - {name: total_conserved, expr: "map_get(balances, 0) + map_get(balances, 1) == total"}
  - {name: no_negative, expr: "map_get(balances, 0) >= 0 and map_get(balances, 1) >= 0"}
"""

_LEDGER_INV = """\
  - {name: total_conserved, expr: "map_get(balances, 0) + map_get(balances, 1) == total"}
"""


_IDEMPOTENCY = """\
spec_version: "1.1"
name: Idempotency
description: >
  A request may only be applied once.  The seen set records the request ids
  that were already applied.
vars:
  seen:
    type: set
    of: str
    elem_domain: [req-1, req-2, req-3, req-4]
    max_size: 4
  applied: {type: int, min: 0, max: 100}
init:
  seen: []
  applied: "0"
actions:
  - name: apply
    params:
      request_id: {type: "enum[req-1,req-2,req-3,req-4]"}
    requires: ["request_id not in seen"]
    effects:
      seen: "set_add(seen, request_id)"
      applied: "applied + 1"
invariants:
  - {name: applied_bounded, expr: "applied <= 4"}
  - {name: seen_bounded, expr: "len(seen) == applied"}
"""

_ORDER = """\
spec_version: "1.1"
name: OrderStateMachine
description: >
  An order moves through pending -> paid -> shipped -> delivered.  It can be
  cancelled only while it is still pending.
vars:
  status: {type: "enum[pending,paid,shipped,delivered,cancelled]"}
  items: {type: int, min: 0, max: 100}
  paid: {type: bool}
init:
  status: pending
  items: "0"
  paid: "false"
actions:
  - name: pay
    requires: ["status == \\"pending\\"", "items > 0"]
    effects:
      status: paid
      paid: "true"
  - name: ship
    requires: ["status == \\"paid\\""]
    effects:
      status: shipped
  - name: deliver
    requires: ["status == \\"shipped\\""]
    effects:
      status: delivered
  - name: cancel
    requires: ["status == \\"pending\\""]
    effects:
      status: cancelled
invariants:
  - {name: paid_iff, expr: "(status == \\"paid\\" or status == \\"shipped\\" or status == \\"delivered\\") == paid"}
  - {name: items_positive, expr: "items >= 0"}
"""

_RATE_LIMITER = """\
spec_version: "1.1"
name: RateLimiter
description: >
  A token bucket with a fixed capacity.  Refills are bounded by the capacity
  and the bucket never holds more tokens than the capacity.
vars:
  tokens: {type: int, min: 0, max: 10}
  capacity: {type: int, min: 1, max: 10}
init:
  tokens: "3"
  capacity: "3"
actions:
  - name: consume
    params:
      amount: {type: int, min: 1, max: 5}
    requires: ["amount > 0", "tokens >= amount"]
    effects:
      tokens: "tokens - amount"
  - name: refill
    params:
      amount: {type: int, min: 1, max: 5}
    requires: ["amount > 0"]
    effects:
      tokens: "min(tokens + amount, capacity)"
invariants:
  - {name: never_over_capacity, expr: "tokens <= capacity"}
  - {name: never_negative, expr: "tokens >= 0"}
"""

_QUEUE = """\
spec_version: "1.1"
name: Queue
description: >
  A bounded queue.  Enqueuing past the capacity is a bug, dequeuing an empty
  queue is a bug.
vars:
  items:
    type: seq
    of: int
    elem_min: 0
    elem_max: 100
    max_size: 4
  head: {type: int, min: 0, max: 4}
  dropped: {type: int, min: 0, max: 100}
init:
  items: []
  head: "0"
  dropped: "0"
actions:
  - name: enqueue
    params:
      value: {type: int, min: 0, max: 100}
    requires: ["len(items) < 4"]
    effects:
      items: "seq_append(items, value)"
  - name: dequeue
    requires: ["head < len(items)"]
    effects:
      head: "head + 1"
      dropped: "dropped + 1"
invariants:
  - {name: size_bounded, expr: "len(items) <= 4"}
  - {name: head_valid, expr: "head <= len(items)"}
"""


_SPECS: dict[str, str] = {
    "ledger": _LEDGER,
    "idempotency": _IDEMPOTENCY,
    "order": _ORDER,
    "rate-limiter": _RATE_LIMITER,
    "queue": _QUEUE,
}


_ADAPTER_PY = '''\
"""Adapter for the {name} spec.

The adapter is the *implementation under test*: VeriSpec checks that it obeys
the contract in {spec_file}.  Keep it small, keep it honest.
"""


class {cls}:
    def reset(self):
        self.state = {state_init}

    def apply(self, action, **params):
{apply_body}

    def snapshot(self):
        return dict(self.state)
'''

_ADAPTER_TS = """\
// Adapter for the {name} spec.
// The adapter is the implementation under test: VeriSpec checks that it obeys
// the contract in {spec_file}.

export function makeAdapter(): Adapter {{
  let state: any;
  return {{
    reset(): void {{
      state = {state_init};
    }},
    apply(action: string, params: Record<string, any>): void {{
{apply_body}
    }},
    snapshot(): Record<string, any> {{
      return JSON.parse(JSON.stringify(state));
    }},
  }};
}}
"""

_ADAPTER_GO = """\
// Adapter for the {name} spec.
// The adapter is the implementation under test: VeriSpec checks that it obeys
// the contract in {spec_file}.

package {pkg}

type Adapter struct {{
	state map[string]any
}}

func NewAdapter() *Adapter {{
	return &Adapter{{state: map[string]any{{}}}}
}}

func (a *Adapter) Reset() {{
	a.state = map[string]any{state_init}
}}

func (a *Adapter) Apply(action string, params map[string]any) {{
{apply_body}
}}

func (a *Adapter) Snapshot() map[string]any {{
	out := map[string]any{{}}
	for k, v := range a.state {{
		out[k] = v
	}}
	return out
}}
"""


def _python_state(spec_yaml: str) -> tuple[str, str]:
    """Return (initial state literal, apply body) for the Python adapter."""
    if "balances" in spec_yaml:
        init = '{"balances": {0: 100, 1: 0}, "total": 100}'
        body = (
            "        if action == 'transfer':\n"
            "            amount = params['amount']\n"
            "            src = params['from_account']\n"
            "            dst = params['to_account']\n"
            "            if self.state['balances'].get(src, 0) < amount:\n"
            "                raise ValueError('precondition failed')\n"
            "            self.state['balances'][src] = self.state['balances'].get(src, 0) - amount\n"
            "            self.state['balances'][dst] = self.state['balances'].get(dst, 0) + amount\n"
            "        else:\n"
            "            raise ValueError(action)\n"
        )
        return init, body
    if "seen" in spec_yaml:
        init = '{"seen": set(), "applied": 0}'
        body = (
            "        if action == 'apply':\n"
            "            request_id = params['request_id']\n"
            "            if request_id in self.state['seen']:\n"
            "                raise ValueError('precondition failed')\n"
            "            self.state['seen'].add(request_id)\n"
            "            self.state['applied'] += 1\n"
            "        else:\n"
            "            raise ValueError(action)\n"
        )
        return init, body
    if "status" in spec_yaml:
        init = '{"status": "pending", "items": 0, "paid": False}'
        body = (
            "        if action == 'pay':\n"
            "            self.state['status'] = 'paid'\n"
            "            self.state['paid'] = True\n"
            "        elif action == 'ship':\n"
            "            self.state['status'] = 'shipped'\n"
            "        elif action == 'deliver':\n"
            "            self.state['status'] = 'delivered'\n"
            "        elif action == 'cancel':\n"
            "            self.state['status'] = 'cancelled'\n"
            "        else:\n"
            "            raise ValueError(action)\n"
        )
        return init, body
    if "tokens" in spec_yaml:
        init = '{"tokens": 3, "capacity": 3}'
        body = (
            "        if action == 'consume':\n"
            "            amount = params['amount']\n"
            "            if self.state['tokens'] < amount:\n"
            "                raise ValueError('precondition failed')\n"
            "            self.state['tokens'] -= amount\n"
            "        elif action == 'refill':\n"
            "            amount = params['amount']\n"
            "            self.state['tokens'] = min(self.state['tokens'] + amount, self.state['capacity'])\n"
            "        else:\n"
            "            raise ValueError(action)\n"
        )
        return init, body
    init = '{"items": [], "dropped": 0}'
    body = (
        "        if action == 'enqueue':\n"
        "            if len(self.state['items']) >= 4:\n"
        "                raise ValueError('precondition failed')\n"
        "            self.state['items'].append(params['value'])\n"
        "        elif action == 'dequeue':\n"
        "            if not self.state['items']:\n"
        "                raise ValueError('precondition failed')\n"
        "            self.state['items'].pop(0)\n"
        "        else:\n"
        "            raise ValueError(action)\n"
    )
    return init, body


def _ts_state(spec_yaml: str) -> tuple[str, str]:
    init, body = _python_state(spec_yaml)
    init = init.replace("'", '"').replace("True", "true").replace("False", "false")
    init = init.replace("set()", "[]")
    body = (
        body.replace("        ", "      ")
        .replace("self.state", "state")
        .replace("params[", "params[")
        .replace("raise ValueError('precondition failed')", "throw new Error('precondition failed')")
        .replace("raise ValueError(action)", "throw new Error(action)")
        .replace("self.state['seen'].add(request_id)", "state['seen'] = [...state['seen'], request_id]")
    )
    return init, body


def _go_state(spec_yaml: str) -> tuple[str, str]:
    init, body = _python_state(spec_yaml)
    init = init.replace("'", '"').replace("True", "true").replace("False", "false")
    init = init.replace("set()", "[]any{}")
    body = (
        body.replace("        ", "\t")
        .replace("self.state", "a.state")
        .replace("params[", "params[")
        .replace("raise ValueError('precondition failed')", "panic(\"precondition failed\")")
        .replace("raise ValueError(action)", "panic(action)")
        .replace("self.state['seen'].add(request_id)", "a.state[\"seen\"] = append(a.state[\"seen\"].([]any), params[\"request_id\"])")
    )
    return init, body


_README = """\
# {name}

A VeriSpec project scaffolded with `verispec init --template {template}`.

```
{name}.verispec.yaml   the spec: the human owned contract
adapter_{langfile}     the implementation under test
```

## Try it

```bash
verispec validate {name}.verispec.yaml
verispec check {name}.verispec.yaml --bound 10
verispec gen-tests {name}.verispec.yaml --lang {lang} -o test_{name}_verispec.{ext}
```

The spec is the contract.  It is read-only for coding agents: if a check
fails, fix the implementation, not the spec.
"""


def template_names() -> list[str]:
    return sorted(_SPECS)


def build_template(name: str, lang: str = "python", spec_name: str | None = None) -> dict[str, Any]:
    """Build the files of a template."""
    if name not in _SPECS:
        raise KeyError(name)
    spec_yaml = _SPECS[name]
    cls = "".join(part.capitalize() for part in name.replace("-", "_").split("_")) + "Adapter"
    spec_file = f"{spec_name or name}.verispec.yaml"
    if lang == "python":
        init, body = _python_state(spec_yaml)
        adapter = _ADAPTER_PY.format(
            name=name, cls=cls, spec_file=spec_file, state_init=init, apply_body=body
        )
        adapter_file = f"adapter_{name}.py"
        langfile, ext = "py", "py"
    elif lang == "typescript":
        init, body = _ts_state(spec_yaml)
        adapter = _ADAPTER_TS.format(
            name=name, spec_file=spec_file, state_init=init, apply_body=body
        )
        adapter_file = f"adapter_{name}.ts"
        langfile, ext = "ts", "ts"
    elif lang == "go":
        init, body = _go_state(spec_yaml)
        adapter = _ADAPTER_GO.format(
            name=name,
            pkg=name.replace("-", "_"),
            spec_file=spec_file,
            state_init=init,
            apply_body=body,
        )
        adapter_file = f"adapter_{name}.go"
        langfile, ext = "go", "go"
    else:
        raise ValueError(f"unsupported language {lang!r}")
    readme = _README.format(
        name=spec_name or name, template=name, lang=lang, langfile=langfile, ext=ext
    )
    return {
        spec_file: spec_yaml,
        adapter_file: adapter,
        "README.md": readme,
    }
