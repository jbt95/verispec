package dev.verispec.conformance;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import dev.verispec.VeriSpecAdapter;
import dev.verispec.VeriSpecValues;

import net.jqwik.api.Arbitraries;
import net.jqwik.api.Arbitrary;
import net.jqwik.api.ForAll;
import net.jqwik.api.Property;
import net.jqwik.api.Provide;

/**
 * The conformance test for the Bank adapter, in the shape that
 * {@code verispec gen-tests --lang java} produces.
 *
 * <p>It drives the adapter with a random sequence of actions:
 *
 * <ol>
 *   <li>{@code reset()} must produce exactly the state the spec's {@code init}
 *       block prescribes;</li>
 *   <li>every step draws an action and its parameters, and the action is
 *       <em>skipped</em> when its preconditions do not hold in the current
 *       state (an action that is not enabled cannot be taken);</li>
 *   <li>after an action is applied, the adapter's snapshot must equal the
 *       state the spec's {@code effects} prescribe;</li>
 *   <li>every state, including the initial one, must satisfy every invariant
 *       of the spec.</li>
 * </ol>
 *
 * <p>What a green run gives you is <em>conformance-tested</em> evidence: the
 * implementation behaved like the spec on every sequence that was tried.  It
 * is not a proof.
 */
public class BankVeriSpecTest {

    private static final String ACTION = "transfer_a_to_b";
    private static final int MIN_AMOUNT = 1;
    private static final int MAX_AMOUNT = 50;
    private static final int TOTAL = 100;

    /**
     * The parameter strategy: the amounts the action is called with, in the
     * range the spec declares for the parameter.
     */
    @Provide
    public Arbitrary<List<Integer>> amounts() {
        return Arbitraries.integers().between(MIN_AMOUNT, MAX_AMOUNT)
                .list().ofMinSize(1).ofMaxSize(25);
    }

    @Property(tries = 200)
    public void theAdapterConformsToTheSpec(@ForAll("amounts") List<Integer> amounts) {
        VeriSpecAdapter adapter = new BankAdapter();
        adapter.reset();
        Map<String, Object> state = new LinkedHashMap<>(adapter.snapshot());

        assertEquals(expectedInit().keySet(), state.keySet(),
                "the snapshot must contain exactly the spec's variables");
        assertTrue(VeriSpecValues.equalMaps(expectedInit(), state),
                "after reset() expected " + expectedInit() + " but got " + state);
        checkInvariants(state, "after reset()");

        int step = 0;
        for (Integer amount : amounts) {
            Map<String, Object> params = Map.<String, Object>of("amount", amount);
            if (!requires(state, amount)) {
                // The precondition does not hold: the action is not applied.
                step++;
                continue;
            }
            Map<String, Object> expected = effects(state, amount);
            adapter.apply(ACTION, params);
            Map<String, Object> actual = new LinkedHashMap<>(adapter.snapshot());
            assertTrue(VeriSpecValues.equalMaps(expected, actual),
                    "step " + step + " action " + ACTION + " " + params
                            + ": expected " + expected + " but got " + actual
                            + " (prev=" + state + ")");
            checkInvariants(actual, "step " + step);
            state = actual;
            step++;
        }
    }

    // ------------------------------------------------- the spec, in Java

    /** The state the spec's {@code init} block prescribes. */
    private static Map<String, Object> expectedInit() {
        return Map.<String, Object>of("balance_a", 50, "balance_b", 50);
    }

    /** The preconditions of {@code transfer_a_to_b}. */
    private static boolean requires(Map<String, Object> state, int amount) {
        return amount > 0 && balanceA(state) >= amount;
    }

    /** The effects of {@code transfer_a_to_b}, applied to the current state. */
    private static Map<String, Object> effects(Map<String, Object> state, int amount) {
        Map<String, Object> next = new LinkedHashMap<>(state);
        next.put("balance_a", balanceA(state) - amount);
        next.put("balance_b", balanceB(state) + amount);
        return next;
    }

    /** Every invariant of the spec, checked against one state. */
    private static void checkInvariants(Map<String, Object> state, String context) {
        int total = balanceA(state) + balanceB(state);
        assertTrue(total == TOTAL,
                "invariant total_conserved violated at " + context
                        + ": balance_a + balance_b == " + TOTAL + " but was " + total
                        + " in " + state);
    }

    private static int balanceA(Map<String, Object> state) {
        return ((Number) state.get("balance_a")).intValue();
    }

    private static int balanceB(Map<String, Object> state) {
        return ((Number) state.get("balance_b")).intValue();
    }
}
