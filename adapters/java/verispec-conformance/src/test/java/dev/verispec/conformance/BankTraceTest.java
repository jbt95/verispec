package dev.verispec.conformance;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import dev.verispec.TraceRecorder;
import dev.verispec.VeriSpecAdapter;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * The trace recorder in action.
 *
 * <p>The trace is the language agnostic bridge to {@code verispec
 * check-trace}: the adapter reports what it did, and VeriSpec replays the
 * report against the spec.  The result is <em>conformance-tested</em>
 * evidence, never a proof.
 */
class BankTraceTest {

    @TempDir
    Path tempDir;

    @Test
    @DisplayName("a recorded trace is the canonical VeriSpec trace format")
    void aRecordedTraceIsTheCanonicalVeriSpecTraceFormat() throws Exception {
        VeriSpecAdapter adapter = new BankAdapter();
        TraceRecorder recorder = TraceRecorder.start(adapter);

        adapter.apply("transfer_a_to_b", Map.<String, Object>of("amount", 20));
        recorder.recordApplied(adapter, "transfer_a_to_b", Map.<String, Object>of("amount", 20));
        adapter.apply("transfer_a_to_b", Map.<String, Object>of("amount", 10));
        recorder.recordApplied(adapter, "transfer_a_to_b", Map.<String, Object>of("amount", 10));

        Path file = tempDir.resolve("bank-trace.json");
        recorder.writeTo(file);
        String text = Files.readString(file, StandardCharsets.UTF_8);

        List<TraceRecorder.Step> steps = TraceRecorder.readTrace(file);
        assertEquals(3, steps.size());
        assertEquals("init", steps.get(0).action());
        assertEquals(50, intOf(steps.get(0).state().get("balance_a")));
        assertEquals(20L, steps.get(1).params().get("amount"));
        assertEquals(30, intOf(steps.get(1).state().get("balance_a")));
        assertEquals(20, intOf(steps.get(2).state().get("balance_a")));

        assertTrue(text.startsWith("["), "a trace is a JSON list");
        assertTrue(text.contains("\"action\": \"init\""), "the first step is the init step");
        assertTrue(text.contains("\"amount\": 20"), "numbers are written without quotes");
    }

    /**
     * The number an int in the trace is worth.
     *
     * <p>JSON has one number type, so a state that is read back from a trace
     * file holds a {@link Long} where the adapter held an {@link Integer}.
     * That is exactly why the value helpers compare numbers by value.
     */
    private static int intOf(Object value) {
        return ((Number) value).intValue();
    }
}
