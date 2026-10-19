package dev.verispec.conformance;

import java.util.LinkedHashMap;
import java.util.Map;

import dev.verispec.VeriSpecAdapter;

/**
 * The reference adapter for the Bank spec.
 *
 * <p>It is the implementation under test: VeriSpec checks that it obeys the
 * contract of {@code bank.yaml}.
 *
 * <pre>
 * vars:
 *   balance_a: {type: int, min: 0, max: 100}
 *   balance_b: {type: int, min: 0, max: 100}
 * init:
 *   balance_a: "50"
 *   balance_b: "50"
 * actions:
 *   - name: transfer_a_to_b
 *     params:
 *       amount: {type: int, min: 1, max: 50}
 *     requires: ["amount &gt; 0", "balance_a &gt;= amount"]
 *     effects:
 *       balance_a: "balance_a - amount"
 *       balance_b: "balance_b + amount"
 * invariants:
 *   - {name: total_conserved, expr: "balance_a + balance_b == 100"}
 * </pre>
 *
 * <p>Keep it small, keep it honest.
 */
public final class BankAdapter implements VeriSpecAdapter {

    private int balanceA;
    private int balanceB;

    @Override
    public void reset() {
        balanceA = 50;
        balanceB = 50;
    }

    @Override
    public void apply(String action, Map<String, Object> params) {
        switch (action) {
            case "transfer_a_to_b": {
                int amount = amount(params);
                if (!(amount > 0 && balanceA >= amount)) {
                    throw new IllegalStateException(
                            "precondition failed for " + action + ": amount=" + amount
                                    + ", balance_a=" + balanceA);
                }
                balanceA -= amount;
                balanceB += amount;
                break;
            }
            default:
                throw new IllegalArgumentException("unknown action: " + action);
        }
    }

    @Override
    public Map<String, Object> snapshot() {
        Map<String, Object> state = new LinkedHashMap<>();
        state.put("balance_a", balanceA);
        state.put("balance_b", balanceB);
        return state;
    }

    private static int amount(Map<String, Object> params) {
        Object raw = params.get("amount");
        if (!(raw instanceof Number)) {
            throw new IllegalArgumentException(
                    "param 'amount' must be a number, got " + raw);
        }
        return ((Number) raw).intValue();
    }
}
