package dev.verispec;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Tests for the trace recorder and for the tiny JSON writer it is built on.
 */
class TraceRecorderTest {

    @TempDir
    Path tempDir;

    // ------------------------------------------------------------- the recorder

    @Test
    @DisplayName("the first step of a trace is always the init step")
    void theInitStepIsAlwaysTheFirstStep() {
        TraceRecorder recorder = new TraceRecorder();
        recorder.recordInit(Map.<String, Object>of("balance_a", 50, "balance_b", 50));
        recorder.record("transfer_a_to_b", Map.<String, Object>of("amount", 20),
                Map.<String, Object>of("balance_a", 30, "balance_b", 70));

        List<TraceRecorder.Step> steps = recorder.steps();
        assertEquals(2, steps.size());
        assertEquals("init", steps.get(0).action());
        assertEquals(Map.<String, Object>of(), steps.get(0).params());
        assertEquals(Map.<String, Object>of("balance_a", 50, "balance_b", 50), steps.get(0).state());
        assertEquals("transfer_a_to_b", steps.get(1).action());
        assertEquals(Map.<String, Object>of("amount", 20), steps.get(1).params());
        assertEquals(Map.<String, Object>of("balance_a", 30, "balance_b", 70), steps.get(1).state());
    }

    @Test
    @DisplayName("a trace cannot start with an action")
    void aTraceCannotStartWithAnAction() {
        TraceRecorder recorder = new TraceRecorder();
        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> recorder.record("transfer_a_to_b", Map.<String, Object>of("amount", 1),
                        Map.<String, Object>of("balance_a", 49)));
        assertTrue(error.getMessage().contains("init"));
    }

    @Test
    @DisplayName("the init step cannot be recorded twice")
    void theInitStepCannotBeRecordedTwice() {
        TraceRecorder recorder = new TraceRecorder();
        recorder.recordInit(Map.<String, Object>of("balance_a", 50));
        assertThrows(IllegalStateException.class,
                () -> recorder.recordInit(Map.<String, Object>of("balance_a", 50)));
    }

    @Test
    @DisplayName("the recorder snapshots the adapter after an action")
    void theRecorderSnapshotsTheAdapter() {
        BankLikeAdapter adapter = new BankLikeAdapter();
        TraceRecorder recorder = TraceRecorder.start(adapter);
        adapter.apply("transfer_a_to_b", Map.<String, Object>of("amount", 20));
        recorder.recordApplied(adapter, "transfer_a_to_b", Map.<String, Object>of("amount", 20));

        List<TraceRecorder.Step> steps = recorder.steps();
        assertEquals(2, steps.size());
        assertEquals("init", steps.get(0).action());
        assertEquals(Map.<String, Object>of("balance_a", 50, "balance_b", 50), steps.get(0).state());
        assertEquals("transfer_a_to_b", steps.get(1).action());
        assertEquals(Map.<String, Object>of("balance_a", 30, "balance_b", 70), steps.get(1).state());
    }

    @Test
    @DisplayName("a recorded state is a copy, not a live view")
    void aRecordedStateIsACopy() {
        Map<String, Object> state = new LinkedHashMap<>();
        state.put("balance_a", 50);
        TraceRecorder recorder = new TraceRecorder().recordInit(state);
        state.put("balance_a", 999);
        assertEquals(50, recorder.steps().get(0).state().get("balance_a"));
    }

    // ------------------------------------------------------------- the trace file

    @Test
    @DisplayName("the trace is written in the canonical format")
    void theTraceIsWrittenInTheCanonicalFormat() throws IOException {
        TraceRecorder recorder = new TraceRecorder()
                .recordInit(Map.<String, Object>of("balance_a", 50, "balance_b", 50))
                .record("transfer_a_to_b", Map.<String, Object>of("amount", 20),
                        Map.<String, Object>of("balance_a", 30, "balance_b", 70));

        Path file = tempDir.resolve("bank.json");
        recorder.writeTo(file);
        String text = Files.readString(file, StandardCharsets.UTF_8);

        assertTrue(text.startsWith("["), "a trace is a JSON list");
        assertTrue(text.contains("\"action\": \"init\""), "the first step is the init step");
        assertTrue(text.contains("\"params\": {}"), "the init step has no parameters");
        assertTrue(text.contains("\"action\": \"transfer_a_to_b\""));
        assertTrue(text.contains("\"amount\": 20"), "numbers are written without quotes");
        assertTrue(text.endsWith(System.lineSeparator()));

        List<TraceRecorder.Step> read = TraceRecorder.readTrace(file);
        assertEquals(2, read.size());
        assertEquals("init", read.get(0).action());
        assertEquals("transfer_a_to_b", read.get(1).action());
        assertTrue(VeriSpecValues.equalMaps(
                Map.<String, Object>of("balance_a", 30, "balance_b", 70),
                read.get(1).state()));
    }

    @Test
    @DisplayName("a trace read back from a file keeps its order and its values")
    void aTraceReadBackFromAFileKeepsItsOrderAndItsValues() {
        String json = "["
                + "{\"action\": \"init\", \"params\": {}, \"state\": {\"balance_a\": 50}},"
                + "{\"action\": \"deposit\", \"params\": {\"amount\": 5},"
                + " \"state\": {\"balance_a\": 55}}"
                + "]";
        List<TraceRecorder.Step> steps = TraceRecorder.readTrace(json);
        assertEquals(2, steps.size());
        assertEquals("init", steps.get(0).action());
        assertEquals("deposit", steps.get(1).action());
        assertEquals(55L, steps.get(1).state().get("balance_a"));
    }

    @Test
    @DisplayName("a malformed trace is rejected")
    void aMalformedTraceIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> Json.parseTrace("{}"));
        assertThrows(IllegalArgumentException.class, () -> Json.parseTrace("[]"));
        assertThrows(IllegalArgumentException.class,
                () -> Json.parseTrace("[{\"action\": \"init\"}]"));
        assertThrows(IllegalArgumentException.class, () -> Json.parseTrace("["));
        assertThrows(IllegalArgumentException.class,
                () -> Json.parseTrace("[{\"action\": \"deposit\", \"params\": {}, \"state\": {}}]"));
    }

    // ----------------------------------------------------------------- the JSON

    @Test
    @DisplayName("strings are escaped")
    void stringsAreEscaped() {
        String json = Json.write("a\"b\\c\nd\te\u0001f");
        assertEquals("\"a\\\"b\\\\c\\nd\\te\\u0001f\"", json);
    }

    @Test
    @DisplayName("numbers, booleans and null are written without quotes")
    void numbersBooleansAndNullAreWrittenWithoutQuotes() {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("i", 50);
        value.put("l", 12345678901L);
        value.put("b", Boolean.TRUE);
        value.put("n", null);
        value.put("s", "x");
        assertEquals("{\"i\":50,\"l\":12345678901,\"b\":true,\"n\":null,\"s\":\"x\"}",
                Json.writeCompact(value));
    }

    @Test
    @DisplayName("a value survives a write and a read")
    void aValueSurvivesAWriteAndARead() {
        Map<String, Object> state = new LinkedHashMap<>();
        state.put("balance_a", 50);
        state.put("flag", Boolean.FALSE);
        state.put("name", "alice");
        Map<String, Object> nested = new LinkedHashMap<>();
        nested.put("0", 10);
        nested.put("1", 20);
        state.put("balances", nested);
        state.put("history", new ArrayList<>(List.of(1, 2, 3)));

        Object parsed = Json.parse(Json.writeCompact(state));
        assertTrue(parsed instanceof Map<?, ?>, "a state is a JSON object");
        assertTrue(VeriSpecValues.equal(state, parsed), "the value survives the round trip");
    }

    @Test
    @DisplayName("a set is written as an array")
    void aSetIsWrittenAsAnArray() {
        Map<String, Object> state = new LinkedHashMap<>();
        state.put("seen", new java.util.LinkedHashSet<>(List.of("a", "b")));
        assertEquals("{\"seen\":[\"a\",\"b\"]}", Json.writeCompact(state));
    }

    @Test
    @DisplayName("a value that JSON cannot represent is rejected")
    void aValueThatJsonCannotRepresentIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> Json.write(Double.NaN));
        assertThrows(IllegalArgumentException.class, () -> Json.write(new Object()));
    }

    // ------------------------------------------------------- the value helpers

    @Test
    @DisplayName("a missing map key reads as the value type default")
    void aMissingMapKeyReadsAsTheValueTypeDefault() {
        Map<String, Object> map = Map.<String, Object>of("0", 10);
        assertEquals(0, VeriSpecValues.mapGet(map, "1", VeriSpecValues.intDefault(null)));
        assertEquals(10, VeriSpecValues.mapGet(map, "0", VeriSpecValues.intDefault(null)));
        assertFalse((Boolean) VeriSpecValues.mapGet(map, "1", VeriSpecValues.boolDefault()));
        assertEquals("", VeriSpecValues.mapGet(map, "1", VeriSpecValues.strDefault()));
        assertEquals("pending",
                VeriSpecValues.mapGet(map, "1", VeriSpecValues.enumDefault("pending")));
    }

    @Test
    @DisplayName("a map_set with a key outside the domain is ignored")
    void aMapSetWithAKeyOutsideTheDomainIsIgnored() {
        Map<String, Object> map = Map.<String, Object>of("0", 10);
        Map<String, Object> ignored =
                VeriSpecValues.mapSet(map, 7, 99, List.of(0, 1, 2));
        assertEquals(map, ignored, "an out of domain key does not change the map");

        Map<String, Object> applied =
                VeriSpecValues.mapSet(map, 1, 99, List.of(0, 1, 2));
        assertEquals(99, applied.get("1"));
        assertEquals(10, applied.get("0"));
    }

    @Test
    @DisplayName("a set has no duplicates and ignores out of domain elements")
    void aSetHasNoDuplicatesAndIgnoresOutOfDomainElements() {
        java.util.Set<Object> set = VeriSpecValues.setAdd(
                new ArrayList<>(), "a", List.of("a", "b"));
        assertEquals(1, set.size());
        set = VeriSpecValues.setAdd(set, "a", List.of("a", "b"));
        assertEquals(1, set.size(), "a set never holds a duplicate");
        set = VeriSpecValues.setAdd(set, "z", List.of("a", "b"));
        assertEquals(1, set.size(), "an out of domain element is ignored");
        assertTrue(VeriSpecValues.setHas(set, "a"));
        assertFalse(VeriSpecValues.setHas(set, "z"));
    }

    @Test
    @DisplayName("a sequence appends and reads out of range as the default")
    void aSequenceAppendsAndReadsOutOfRangeAsTheDefault() {
        List<Object> seq = VeriSpecValues.seqAppend(new ArrayList<>(), 1);
        seq = VeriSpecValues.seqAppend(seq, 2);
        assertEquals(List.of(1, 2), seq);
        assertEquals(1, VeriSpecValues.seqGet(seq, 0, 0));
        assertEquals(0, VeriSpecValues.seqGet(seq, 7, 0), "an out of range index reads as the default");
        assertEquals(0, VeriSpecValues.seqGet(seq, -1, 0), "a negative index reads as the default");
    }

    @Test
    @DisplayName("numbers compare by value")
    void numbersCompareByValue() {
        assertTrue(VeriSpecValues.equal(50, 50L));
        assertTrue(VeriSpecValues.equalMaps(
                Map.<String, Object>of("balance_a", 50),
                Map.<String, Object>of("balance_a", 50L)));
        assertFalse(VeriSpecValues.equal(Boolean.TRUE, 1),
                "a boolean is never equal to a number");
    }

    @Test
    @DisplayName("a set compares without order and a sequence with order")
    void aSetComparesWithoutOrderAndASequenceWithOrder() {
        assertTrue(VeriSpecValues.equal(List.of("a", "b"), new java.util.LinkedHashSet<>(List.of("b", "a"))));
        assertFalse(VeriSpecValues.equal(List.of("a", "b"), List.of("b", "a")));
    }

    @Test
    @DisplayName("map keys are stringified")
    void mapKeysAreStringified() {
        assertEquals("3", VeriSpecValues.key(3));
        assertEquals("3", VeriSpecValues.key(3L));
        assertEquals("true", VeriSpecValues.key(Boolean.TRUE));
        assertEquals("alice", VeriSpecValues.key("alice"));
    }

    // ------------------------------------------------------------- a fake adapter

    /** The smallest adapter that could possibly be useful in a test. */
    private static final class BankLikeAdapter implements VeriSpecAdapter {
        private int balanceA = 50;
        private int balanceB = 50;

        @Override
        public void reset() {
            balanceA = 50;
            balanceB = 50;
        }

        @Override
        public void apply(String action, Map<String, Object> params) {
            if (!"transfer_a_to_b".equals(action)) {
                throw new IllegalArgumentException("unknown action: " + action);
            }
            int amount = ((Number) params.get("amount")).intValue();
            balanceA -= amount;
            balanceB += amount;
        }

        @Override
        public Map<String, Object> snapshot() {
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("balance_a", balanceA);
            out.put("balance_b", balanceB);
            return out;
        }
    }
}
