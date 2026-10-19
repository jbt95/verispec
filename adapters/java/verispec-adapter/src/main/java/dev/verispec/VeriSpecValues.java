package dev.verispec;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Helpers that implement the VeriSpec collection semantics.
 *
 * <p>The generated conformance test evaluates the spec's expressions with
 * these helpers, so that the Java side and the Python/Z3 side agree on the
 * edge cases.  The rules are the ones written down in
 * {@code docs/spec-format.md}:
 *
 * <ul>
 *   <li><b>Missing map keys read as the value type default.</b>  Reading a key
 *       that is not present returns {@code 0}, {@code false}, {@code ""} or the
 *       first enum value, depending on the value type.  It is never an error.
 *       See {@link #mapGet(Map, Object, Object)} and the {@code *Default}
 *       helpers.</li>
 *   <li><b>Out-of-domain keys are ignored.</b>  {@code map_set(m, k, v)} with a
 *       {@code k} outside the declared key domain returns a copy of {@code m}
 *       unchanged.  See {@link #mapSet(Map, Object, Object, Collection)}.</li>
 *   <li><b>Sets are unique.</b>  {@code set_add(s, v)} on a set that already
 *       contains {@code v} returns the set unchanged, and an element outside
 *       the declared domain is ignored.  See {@link #setAdd}.</li>
 *   <li><b>Sequences grow.</b>  {@code seq_append(q, v)} returns a copy of
 *       {@code q} with {@code v} at the end; duplicates are kept.  See
 *       {@link #seqAppend}.</li>
 *   <li><b>Reading out of range is the default.</b>  {@code seq_get(q, i)} with
 *       an index outside {@code 0 <= i < len(q)} returns the element type
 *       default.  See {@link #seqGet}.</li>
 * </ul>
 *
 * <p>Map keys are strings on the wire, so an integer key {@code 3} is written
 * and read as {@code "3"}; see {@link #key(Object)}.
 */
public final class VeriSpecValues {

    private VeriSpecValues() {
        // utility class
    }

    // ------------------------------------------------------------------ keys

    /**
     * The JSON object key of a value.
     *
     * <p>Integer keys are written in decimal ({@code 3} becomes {@code "3"}),
     * booleans become {@code "true"} / {@code "false"}, and everything else
     * becomes its string form.  This is the same stringification the Python
     * runtime uses for the trace format.
     */
    public static String key(Object key) {
        if (key == null) {
            return "null";
        }
        if (key instanceof Boolean) {
            return ((Boolean) key) ? "true" : "false";
        }
        if (key instanceof Integer || key instanceof Long || key instanceof Short
                || key instanceof Byte) {
            return key.toString();
        }
        return String.valueOf(key);
    }

    // -------------------------------------------------------------- defaults

    /** Default of an {@code int} value type: the declared minimum, or {@code 0}. */
    public static int intDefault(Integer min) {
        return min == null ? 0 : min;
    }

    /** Default of a {@code bool} value type. */
    public static boolean boolDefault() {
        return false;
    }

    /** Default of a {@code str} value type. */
    public static String strDefault() {
        return "";
    }

    /** Default of an {@code enum[...]} value type: its first value. */
    public static String enumDefault(String firstValue) {
        return firstValue == null ? "" : firstValue;
    }

    // ------------------------------------------------------------------ maps

    /**
     * Read a map key.
     *
     * <p>Rule: a missing key reads as the value type default, never as an
     * error.  The caller passes that default in, because only the caller knows
     * the value type.
     */
    public static Object mapGet(Map<String, Object> map, Object key, Object defaultValue) {
        if (map == null) {
            return defaultValue;
        }
        String name = key(key);
        if (map.containsKey(name)) {
            return map.get(name);
        }
        return defaultValue;
    }

    /**
     * Return a copy of {@code map} with {@code key} set to {@code value}.
     *
     * <p>Rule: a key outside the declared domain is ignored, so the copy is
     * returned unchanged.  The Z3 encoding does exactly the same thing, which
     * is why the two semantics never disagree.
     */
    public static Map<String, Object> mapSet(
            Map<String, Object> map, Object key, Object value, Collection<?> domain) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (map != null) {
            out.putAll(map);
        }
        if (!inDomain(domain, key)) {
            return out;
        }
        out.put(key(key), value);
        return out;
    }

    /** Whether a map holds the given key. */
    public static boolean containsKey(Map<String, Object> map, Object key) {
        return map != null && map.containsKey(key(key));
    }

    /** The keys of a map, in iteration order. */
    public static List<Object> keys(Map<String, Object> map) {
        return map == null ? new ArrayList<>() : new ArrayList<>(map.keySet());
    }

    /** The values of a map, in key iteration order. */
    public static List<Object> values(Map<String, Object> map) {
        return map == null ? new ArrayList<>() : new ArrayList<>(map.values());
    }

    // ------------------------------------------------------------------ sets

    /**
     * Return a copy of {@code set} with {@code value} added.
     *
     * <p>Rules: a set has no duplicates, so adding an element that is already
     * there changes nothing; and an element outside the declared domain is
     * ignored.
     */
    public static Set<Object> setAdd(Collection<?> set, Object value, Collection<?> domain) {
        Set<Object> out = new LinkedHashSet<>();
        if (set != null) {
            out.addAll(set);
        }
        if (!inDomain(domain, value)) {
            return out;
        }
        out.add(value);
        return out;
    }

    /** Whether a set holds the given element. */
    public static boolean setHas(Collection<?> set, Object value) {
        return contains(set, value);
    }

    // ------------------------------------------------------------- sequences

    /**
     * Read the element of a sequence.
     *
     * <p>Rule: an index outside {@code 0 <= i < len(q)}, or an index that is not
     * an integer, reads as the element type default.
     */
    public static Object seqGet(List<?> seq, Object index, Object defaultValue) {
        if (seq == null || !(index instanceof Number)) {
            return defaultValue;
        }
        int i = ((Number) index).intValue();
        if (i < 0 || i >= seq.size()) {
            return defaultValue;
        }
        return seq.get(i);
    }

    /**
     * Return a copy of {@code seq} with {@code value} appended.
     *
     * <p>Rule: a sequence keeps duplicates and keeps its order.
     */
    public static List<Object> seqAppend(List<?> seq, Object value) {
        List<Object> out = new ArrayList<>();
        if (seq != null) {
            out.addAll(seq);
        }
        out.add(value);
        return out;
    }

    // ------------------------------------------------------------ membership

    /** Whether a collection holds the given value. */
    public static boolean contains(Collection<?> items, Object value) {
        if (items == null) {
            return false;
        }
        for (Object item : items) {
            if (equal(item, value)) {
                return true;
            }
        }
        return false;
    }

    /**
     * Whether a value belongs to the finite domain of a map key or a set
     * element.
     *
     * <p>The domain is the list of keys (or elements) the spec declared.  A
     * boolean is never a valid key or element, so {@code true} does not match
     * the integer {@code 1}.
     */
    public static boolean inDomain(Collection<?> domain, Object value) {
        if (domain == null || domain.isEmpty()) {
            return false;
        }
        for (Object item : domain) {
            if (equal(item, value)) {
                return true;
            }
        }
        return false;
    }

    // -------------------------------------------------------------- equality

    /**
     * Deep equality with the VeriSpec value semantics.
     *
     * <p>Rules: numbers compare by value (an {@code Integer} 50 and a
     * {@code Long} 50 are equal); maps compare by their keys and values; sets
     * compare without order; sequences compare with order.  A boolean is never
     * equal to a number.
     */
    public static boolean equal(Object a, Object b) {
        if (a == null || b == null) {
            return a == b;
        }
        if (a instanceof Number && b instanceof Number) {
            return numbersEqual((Number) a, (Number) b);
        }
        if (a instanceof Map<?, ?> && b instanceof Map<?, ?>) {
            Map<?, ?> left = (Map<?, ?>) a;
            Map<?, ?> right = (Map<?, ?>) b;
            if (left.size() != right.size()) {
                return false;
            }
            for (Map.Entry<?, ?> entry : left.entrySet()) {
                if (!right.containsKey(entry.getKey())) {
                    return false;
                }
                if (!equal(entry.getValue(), right.get(entry.getKey()))) {
                    return false;
                }
            }
            return true;
        }
        if (a instanceof Collection<?> && b instanceof Collection<?>) {
            Collection<?> left = (Collection<?>) a;
            Collection<?> right = (Collection<?>) b;
            if (a instanceof Set<?> || b instanceof Set<?>) {
                // A set has no order: compare by membership.
                if (left.size() != right.size()) {
                    return false;
                }
                for (Object item : left) {
                    if (!contains(right, item)) {
                        return false;
                    }
                }
                return true;
            }
            if (left.size() != right.size()) {
                return false;
            }
            Iterator<?> leftIt = left.iterator();
            Iterator<?> rightIt = right.iterator();
            while (leftIt.hasNext() && rightIt.hasNext()) {
                if (!equal(leftIt.next(), rightIt.next())) {
                    return false;
                }
            }
            return true;
        }
        return a.equals(b);
    }

    /**
     * Compare two states.
     *
     * <p>The maps must have exactly the same keys, and every value must be
     * equal under {@link #equal(Object, Object)}.
     */
    public static boolean equalMaps(Map<String, Object> a, Map<String, Object> b) {
        if (a == null || b == null) {
            return a == b;
        }
        if (a.size() != b.size()) {
            return false;
        }
        for (Map.Entry<String, Object> entry : a.entrySet()) {
            if (!b.containsKey(entry.getKey())) {
                return false;
            }
            if (!equal(entry.getValue(), b.get(entry.getKey()))) {
                return false;
            }
        }
        return true;
    }

    private static boolean numbersEqual(Number a, Number b) {
        boolean aIntegral = a instanceof Integer || a instanceof Long || a instanceof Short
                || a instanceof Byte;
        boolean bIntegral = b instanceof Integer || b instanceof Long || b instanceof Short
                || b instanceof Byte;
        if (aIntegral && bIntegral) {
            return a.longValue() == b.longValue();
        }
        return a.doubleValue() == b.doubleValue();
    }
}
