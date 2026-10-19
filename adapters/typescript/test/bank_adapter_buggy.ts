/**
 * The deliberately broken bank adapter: it leaks one unit on every
 * transfer_a_to_b.
 *
 * The conformance suite must FAIL on it. A suite that stays green on this
 * adapter is not testing anything.
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
        state.balance_a -= amount;
        state.balance_b += amount - 1;
      } else if (action === "transfer_b_to_a") {
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
