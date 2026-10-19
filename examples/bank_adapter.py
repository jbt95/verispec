"""Correct Bank adapter for generated VeriSpec tests."""
class BankAdapter:
    def reset(self):
        self.state={"balance_a":50,"balance_b":50}
    def apply(self, action, **params):
        amount=params.get("amount",0)
        if action=="transfer_a_to_b":
            if not (amount>0 and self.state["balance_a"]>=amount): raise ValueError("precondition")
            self.state["balance_a"]-=amount; self.state["balance_b"]+=amount
        elif action=="transfer_b_to_a":
            if not (amount>0 and self.state["balance_b"]>=amount): raise ValueError("precondition")
            self.state["balance_b"]-=amount; self.state["balance_a"]+=amount
        else: raise ValueError(action)
    def snapshot(self):
        return dict(self.state)
# alias expected by some harnesses
Adapter=BankAdapter
