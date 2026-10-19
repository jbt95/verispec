package dev.verispec;

import java.util.Map;

/**
 * The VeriSpec adapter contract.
 *
 * <p>An adapter is the bridge between a human owned VeriSpec spec (a YAML file)
 * and a real implementation.  The generated conformance test drives an adapter
 * with random actions and compares the state the implementation reports with
 * the state the spec prescribes, so the implementation is checked against the
 * contract instead of against another implementation of the same idea.
 *
 * <p>Rules an implementation must follow:
 *
 * <ul>
 *   <li>{@link #reset()} puts the implementation back into the state the spec
 *       declares in {@code init}.</li>
 *   <li>{@link #apply(String, Map)} applies exactly one action of the spec.
 *       The generated test only calls it when the action's preconditions
 *       hold, so an implementation may (and should) reject an action whose
 *       precondition does not hold.</li>
 *   <li>{@link #snapshot()} returns <em>exactly</em> the spec's state
 *       variables, and nothing else.  The Java types of the VeriSpec value
 *       types are:
 *       <ul>
 *         <li>{@code int} -&gt; {@link Integer}</li>
 *         <li>{@code bool} -&gt; {@link Boolean}</li>
 *         <li>{@code str} and {@code enum[...]} -&gt; {@link String}</li>
 *         <li>{@code map} -&gt; {@code Map<String, Object>} with stringified keys</li>
 *         <li>{@code set} -&gt; {@code java.util.Set<Object>}</li>
 *         <li>{@code seq} -&gt; {@code java.util.List<Object>}</li>
 *         <li>{@code record} -&gt; {@code Map<String, Object>} keyed by field name</li>
 *       </ul>
 *   </li>
 * </ul>
 *
 * <p>An action the implementation does not know is a programming error in the
 * adapter: throw {@link IllegalArgumentException}.  A precondition that does
 * not hold when the adapter is asked to apply an action is a bug in the
 * implementation: throw {@link IllegalStateException}.
 *
 * <p>What an adapter gives you is <em>conformance evidence</em>, never a proof.
 * See {@link #GUARANTEE}.
 */
public interface VeriSpecAdapter {

    /**
     * The guarantee label an adapter run carries.
     *
     * <p>{@code conformance-tested} means: the implementation was driven
     * through random action sequences and every observed state matched the
     * spec.  It does not mean the implementation is correct for every
     * possible run; no bounded evidence can say that.
     */
    String GUARANTEE = "conformance-tested";

    /** Put the implementation back into the initial state of the spec. */
    void reset();

    /**
     * Apply one action of the spec.
     *
     * @param action  the action name, exactly as the spec spells it
     * @param params  the action parameters, keyed by the spec's parameter names
     */
    void apply(String action, Map<String, Object> params);

    /**
     * The current state, with exactly the spec's state variables.
     *
     * @return a map from variable name to value; never {@code null}
     */
    Map<String, Object> snapshot();
}
