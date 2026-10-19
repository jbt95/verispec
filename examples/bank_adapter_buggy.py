"""Buggy Bank adapter: it leaks 1 unit on every transfer_a_to_b.

The generated conformance test catches it: `total_conserved` no longer holds.
"""


class BankAdapterBuggy:
    def reset(self):
        self.state = {"balance_a": 50, "balance_b": 50}

    def apply(self, action, **params):
        amount = params.get("amount", 0)
        if action == "transfer_a_to_b":
            self.state["balance_a"] -= amount
            self.state["balance_b"] += amount - 1
        elif action == "transfer_b_to_a":
            self.state["balance_b"] -= amount
            self.state["balance_a"] += amount
        else:
            raise ValueError(action)

    def snapshot(self):
        return dict(self.state)


Adapter = BankAdapterBuggy
