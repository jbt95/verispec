"""Correct Ledger adapter for the generated VeriSpec tests.

The adapter is the implementation under test.  It is deliberately small and
reviewable: VeriSpec checks that it obeys the contract in `ledger.yaml`.
"""


class LedgerAdapter:
    def reset(self):
        self.state = {"balances": {0: 100, 1: 0}, "total": 100, "posted": set()}

    def apply(self, action, **params):
        if action == "post":
            amount = params["amount"]
            src = params["from_account"]
            dst = params["to_account"]
            reference = params["reference"]
            balances = self.state["balances"]
            if src not in balances or dst not in balances:
                raise ValueError("unknown account")
            if balances[src] < amount:
                raise ValueError("precondition failed")
            if reference in self.state["posted"]:
                raise ValueError("precondition failed")
            balances[src] = balances[src] - amount
            balances[dst] = balances.get(dst, 0) + amount
            self.state["posted"] = set(self.state["posted"]) | {reference}
        else:
            raise ValueError(action)

    def snapshot(self):
        return {
            "balances": dict(self.state["balances"]),
            "total": self.state["total"],
            "posted": set(self.state["posted"]),
        }


Adapter = LedgerAdapter
