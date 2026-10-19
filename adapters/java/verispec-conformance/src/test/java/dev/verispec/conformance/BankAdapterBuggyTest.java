package dev.verispec.conformance;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.LinkedHashMap;
import java.util.Map;

import dev.verispec.VeriSpecAdapter;
import dev.verispec.VeriSpecValues;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * The other half of the story: the buggy adapter is caught.
 *
 * <p>{@link BankAdapterBuggy} leaks one unit on every transfer, so the
 * invariant {@code total_conserved} no longer holds after the first step.
 * The generated conformance test fails on exactly this, and this test pins
 * the failure down so that a future change in the generated test cannot
 * silently stop noticing the bug.
 */
class BankAdapterBuggyTest {

    @Test
    @DisplayName("the buggy adapter leaks one unit on every transfer")
    void theBuggyAdapterLeaksOneUnitOnEveryTransfer() {
        VeriSpecAdapter adapter = new BankAdapterBuggy();
        adapter.reset();
        adapter.apply("transfer_a_to_b", Map.<String, Object>of("amount", 20));

        Map<String, Object> state = new LinkedHashMap<>(adapter.snapshot());
        int total = total(state);
        assertNotEquals(100, total, "the buggy adapter is expected to lose one unit");
        assertEquals(99, total, "the buggy adapter loses exactly one unit");
    }

    @Test
    @DisplayName("the conformance walk reports a mismatch for the buggy adapter")
    void theConformanceWalkReportsAMismatchForTheBuggyAdapter() {
        VeriSpecAdapter adapter = new BankAdapterBuggy();
        adapter.reset();
        Map<String, Object> state = new LinkedHashMap<>(adapter.snapshot());

        Map<String, Object> expected = new LinkedHashMap<>(state);
        expected.put("balance_a", 30);
        expected.put("balance_b", 70);

        adapter.apply("transfer_a_to_b", Map.<String, Object>of("amount", 20));
        Map<String, Object> actual = new LinkedHashMap<>(adapter.snapshot());

        assertTrue(!VeriSpecValues.equalMaps(expected, actual),
                "the buggy adapter must not match the state the spec prescribes");
    }

    private static int total(Map<String, Object> state) {
        return ((Number) state.get("balance_a")).intValue()
                + ((Number) state.get("balance_b")).intValue();
    }
}
