package dev.verispec;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Records what an adapter did and writes it in the canonical VeriSpec trace
 * format.
 *
 * <p>A trace is the language agnostic bridge between an implementation and
 * the VeriSpec checker.  It is a JSON list of steps:
 *
 * <pre>
 * [
 *   {"action": "init", "params": {}, "state": {"balance_a": 50, "balance_b": 50}},
 *   {"action": "transfer_a_to_b", "params": {"amount": 20},
 *    "state": {"balance_a": 30, "balance_b": 70}}
 * ]
 * </pre>
 *
 * <p>The first step is always the {@code init} step: the state the spec's
 * {@code init} block prescribes.  Every later step names the action that was
 * applied, the parameters it was called with, and the full state that resulted
 * from it.  The state of a step must contain exactly the spec's variables.
 *
 * <p>Write the trace with {@code verispec check-trace <spec> <trace.json>} to
 * replay it against the spec.  The result is <em>conformance-tested</em>
 * evidence, never a proof.
 */
public final class TraceRecorder {

    /** The guarantee label a trace check carries. */
    public static final String GUARANTEE = "conformance-tested";

    /**
     * One recorded step: the action, the parameters it was called with, and
     * the state that resulted from it.
     */
    public record Step(String action, Map<String, Object> params, Map<String, Object> state) {

        /**
         * Copy the maps, so that a later change of the adapter's own map does
         * not rewrite history.
         */
        public Step(String action, Map<String, Object> params, Map<String, Object> state) {
            this.action = action;
            this.params = params == null
                    ? Collections.<String, Object>emptyMap()
                    : new LinkedHashMap<>(params);
            this.state = state == null
                    ? Collections.<String, Object>emptyMap()
                    : new LinkedHashMap<>(state);
        }
    }

    private final List<Step> steps = new ArrayList<>();

    /** The recorded steps, in order. */
    public List<Step> steps() {
        return Collections.unmodifiableList(steps);
    }

    /** How many steps were recorded. */
    public int size() {
        return steps.size();
    }

    /**
     * Record the initial state.
     *
     * <p>The {@code init} step must be the first step of a trace; recording it
     * twice is an error.
     */
    public TraceRecorder recordInit(Map<String, Object> state) {
        if (!steps.isEmpty()) {
            throw new IllegalStateException(
                    "the init step must be the first step of a trace");
        }
        steps.add(new Step("init", Collections.<String, Object>emptyMap(), state));
        return this;
    }

    /**
     * Record an applied action together with the state that resulted from it.
     *
     * @param action  the action name, exactly as the spec spells it
     * @param params  the parameters the action was called with
     * @param state   the state after the action was applied
     */
    public TraceRecorder record(String action, Map<String, Object> params, Map<String, Object> state) {
        if (steps.isEmpty()) {
            throw new IllegalStateException("a trace must start with the init step");
        }
        if (action == null || action.isEmpty()) {
            throw new IllegalArgumentException("an action name must not be empty");
        }
        steps.add(new Step(action, params, state));
        return this;
    }

    /**
     * Snapshot the adapter and record the step.
     *
     * <p>The action must already have been applied to the adapter; this method
     * only takes the snapshot.
     */
    public TraceRecorder recordApplied(VeriSpecAdapter adapter, String action,
            Map<String, Object> params) {
        if (adapter == null) {
            throw new IllegalArgumentException("the adapter must not be null");
        }
        return record(action, params, adapter.snapshot());
    }

    /**
     * Reset the adapter and record the {@code init} step.
     *
     * @return a recorder that already holds the initial state
     */
    public static TraceRecorder start(VeriSpecAdapter adapter) {
        if (adapter == null) {
            throw new IllegalArgumentException("the adapter must not be null");
        }
        TraceRecorder recorder = new TraceRecorder();
        adapter.reset();
        return recorder.recordInit(adapter.snapshot());
    }

    /** The trace as a JSON document. */
    public String toJson() {
        List<Object> raw = new ArrayList<>(steps.size());
        for (Step step : steps) {
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("action", step.action());
            entry.put("params", step.params());
            entry.put("state", step.state());
            raw.add(entry);
        }
        return Json.write(raw);
    }

    /** Write the trace to a file, as UTF-8, with a trailing newline. */
    public void writeTo(Path path) {
        if (path == null) {
            throw new IllegalArgumentException("the path must not be null");
        }
        Path parent = path.getParent();
        if (parent != null) {
            try {
                Files.createDirectories(parent);
            } catch (IOException e) {
                throw new UncheckedIOException("cannot create " + parent, e);
            }
        }
        try {
            Files.writeString(path, toJson() + System.lineSeparator(), StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new UncheckedIOException("cannot write trace to " + path, e);
        }
    }

    /** Read a trace file back into steps. */
    public static List<Step> readTrace(Path path) {
        try {
            return readTrace(Files.readString(path, StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new UncheckedIOException("cannot read trace from " + path, e);
        }
    }

    /** Read a trace document back into steps. */
    public static List<Step> readTrace(String json) {
        List<Map<String, Object>> raw = Json.parseTrace(json);
        List<Step> steps = new ArrayList<>(raw.size());
        for (Map<String, Object> entry : raw) {
            Object action = entry.get("action");
            steps.add(new Step(
                    action == null ? "init" : String.valueOf(action),
                    asMap(entry.get("params")),
                    asMap(entry.get("state"))));
        }
        return steps;
    }

    private static Map<String, Object> asMap(Object raw) {
        if (raw == null) {
            return Collections.emptyMap();
        }
        if (!(raw instanceof Map<?, ?>)) {
            throw new IllegalArgumentException(
                    "expected a JSON object, got " + raw.getClass().getSimpleName());
        }
        return Json.toStringKeyedMap((Map<?, ?>) raw);
    }
}
