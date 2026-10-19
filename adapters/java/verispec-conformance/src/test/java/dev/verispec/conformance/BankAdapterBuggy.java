package dev.verispec.conformance;

import java.util.LinkedHashMap;
import java.util.Map;

import dev.verispec.VeriSpecAdapter;

/**
 * A deliberately broken adapter for the Bank spec.
 *
 * <p>It leaks one unit on every transfer: the amount that leaves account A
 * does not arrive in account B.  The total is no longer 100, so the
 * invariant {@code total_conserved} is violated and the conformance test
 * fails.  It is here to show that the generated test really does catch a
 * broken implementation.
 */
public final class BankAdapterBuggy implements VeriSpecAdapter {

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
                balanceB += amount - 1; // the bug: one unit disappears
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
