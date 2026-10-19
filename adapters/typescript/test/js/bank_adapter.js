/**
 * The correct bank adapter, in plain JavaScript.
 *
 * The adapter is the implementation under test: VeriSpec checks that it
 * obeys the contract in bank.yaml.
 */

/**
 * @returns {{reset: () => void, apply: (action: string, params: Record<string, any>) => void, snapshot: () => Record<string, any>}}
 */
export function makeAdapter() {
  let state;
  return {
    reset() {
      state = { balance_a: 50, balance_b: 50 };
    },
    apply(action, params) {
      const amount = params.amount ?? 0;
      if (action === "transfer_a_to_b") {
        if (!(amount > 0 && state.balance_a >= amount)) throw new Error("precondition failed");
        state.balance_a -= amount;
        state.balance_b += amount;
      } else if (action === "transfer_b_to_a") {
        if (!(amount > 0 && state.balance_b >= amount)) throw new Error("precondition failed");
        state.balance_b -= amount;
        state.balance_a += amount;
      } else {
        throw new Error(`unknown action ${action}`);
      }
    },
    snapshot() {
      return JSON.parse(JSON.stringify(state));
    },
  };
}
