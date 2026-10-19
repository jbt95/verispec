"""Buggy Ledger adapter: it loses one unit on every posting.

The generated conformance test catches it: the total is no longer conserved.
(The *missing balance check* is a different bug, and it lives in the spec
variant `ledger-buggy.yaml`: VeriSpec finds it with the model checker.)
"""


class LedgerAdapterBuggy:
    def reset(self):
        self.state = {"balances": {0: 100, 1: 0}, "total": 100, "posted": set()}

    def apply(self, action, **params):
        if action == "post":
            amount = params["amount"]
            src = params["from_account"]
            dst = params["to_account"]
            reference = params["reference"]
            balances = self.state["balances"]
            # BUG: one unit is lost on every posting
            balances[src] = balances.get(src, 0) - amount
            balances[dst] = balances.get(dst, 0) + amount - 1
            self.state["posted"] = set(self.state["posted"]) | {reference}
        else:
            raise ValueError(action)

    def snapshot(self):
        return {
            "balances": dict(self.state["balances"]),
            "total": self.state["total"],
            "posted": set(self.state["posted"]),
        }


Adapter = LedgerAdapterBuggy
