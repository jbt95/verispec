/**
 * The correct bank adapter: the implementation under test.
 *
 * The adapter is a translation layer, not a second implementation: a real
 * project would call its own code here.
 */
import type { Adapter } from "../src/trace.js";

export function makeAdapter(): Adapter {
  let state: Record<string, any>;
  return {
    reset(): void {
      state = { balance_a: 50, balance_b: 50 };
    },
    apply(action: string, params: Record<string, any>): void {
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
    snapshot(): Record<string, any> {
      return JSON.parse(JSON.stringify(state));
    },
  };
}
