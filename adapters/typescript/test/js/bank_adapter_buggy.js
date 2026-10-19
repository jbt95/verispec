/**
 * The deliberately broken bank adapter, in plain JavaScript.
 *
 * It leaks one unit on every transfer_a_to_b. The conformance suite must
 * FAIL on it.
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
        state.balance_a -= amount;
        state.balance_b += amount - 1;
      } else if (action === "transfer_b_to_a") {
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
