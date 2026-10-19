package dev.verispec;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A tiny JSON writer and reader with no external dependencies.
 *
 * <p>The adapter SDK must stay dependency free: a user should be able to drop
 * the jar into a project without dragging a JSON library (or a version of one)
 * into the build.  This class implements only what the VeriSpec trace format
 * needs:
 *
 * <ul>
 *   <li>writing: maps, collections, arrays, strings, numbers, booleans and
 *       {@code null};</li>
 *   <li>reading: the trace format, that is a JSON list of objects with an
 *       {@code action}, a {@code params} object and a {@code state} object.</li>
 * </ul>
 *
 * <p>The writer escapes {@code "}, {@code \}, the control characters
 * ({@code \b \f \n \r \t} and a backslash-u escape for the rest) and leaves
 * non-ASCII characters alone, so the output is valid UTF-8 JSON.  Numbers,
 * booleans and {@code null} are written without quotes.
 *
 * <p>Sets are written as JSON arrays in iteration order.  The order of a set
 * is not significant in the VeriSpec semantics (a set is compared as a set),
 * so the order in the file does not carry meaning.
 */
public final class Json {

    private Json() {
        // utility class
    }

    // ------------------------------------------------------------------ write

    /** Write a value as an indented JSON document. */
    public static String write(Object value) {
        StringBuilder out = new StringBuilder();
        writeValue(out, value, 0, true);
        return out.toString();
    }

    /** Write a value as a compact JSON document (no whitespace). */
    public static String writeCompact(Object value) {
        StringBuilder out = new StringBuilder();
        writeValue(out, value, 0, false);
        return out.toString();
    }

    private static void writeValue(StringBuilder out, Object value, int depth, boolean pretty) {
        if (value == null) {
            out.append("null");
            return;
        }
        if (value instanceof Boolean) {
            out.append(((Boolean) value) ? "true" : "false");
            return;
        }
        if (value instanceof String) {
            writeString(out, (String) value);
            return;
        }
        if (value instanceof Character) {
            writeString(out, value.toString());
            return;
        }
        if (value instanceof Number) {
            writeNumber(out, (Number) value);
            return;
        }
        if (value instanceof Map<?, ?>) {
            writeObject(out, (Map<?, ?>) value, depth, pretty);
            return;
        }
        if (value instanceof Collection<?>) {
            writeArray(out, (Collection<?>) value, depth, pretty);
            return;
        }
        if (value.getClass().isArray()) {
            writeArray(out, arrayToList(value), depth, pretty);
            return;
        }
        throw new IllegalArgumentException(
                "cannot write JSON for " + value.getClass().getName());
    }

    private static List<Object> arrayToList(Object array) {
        int length = java.lang.reflect.Array.getLength(array);
        List<Object> out = new ArrayList<>(length);
        for (int i = 0; i < length; i++) {
            out.add(java.lang.reflect.Array.get(array, i));
        }
        return out;
    }

    private static void writeNumber(StringBuilder out, Number number) {
        if (number instanceof Double || number instanceof Float) {
            double d = number.doubleValue();
            if (Double.isNaN(d) || Double.isInfinite(d)) {
                throw new IllegalArgumentException("JSON cannot represent " + d);
            }
        }
        out.append(number.toString());
    }

    private static void writeObject(StringBuilder out, Map<?, ?> map, int depth, boolean pretty) {
        out.append('{');
        boolean first = true;
        for (Map.Entry<?, ?> entry : map.entrySet()) {
            if (!first) {
                out.append(',');
            }
            first = false;
            if (pretty) {
                out.append('\n');
                indent(out, depth + 1);
            }
            writeString(out, String.valueOf(entry.getKey()));
            out.append(':');
            if (pretty) {
                out.append(' ');
            }
            writeValue(out, entry.getValue(), depth + 1, pretty);
        }
        if (pretty && !map.isEmpty()) {
            out.append('\n');
            indent(out, depth);
        }
        out.append('}');
    }

    private static void writeArray(StringBuilder out, Collection<?> items, int depth, boolean pretty) {
        out.append('[');
        boolean first = true;
        for (Object item : items) {
            if (!first) {
                out.append(',');
            }
            first = false;
            if (pretty) {
                out.append('\n');
                indent(out, depth + 1);
            }
            writeValue(out, item, depth + 1, pretty);
        }
        if (pretty && !items.isEmpty()) {
            out.append('\n');
            indent(out, depth);
        }
        out.append(']');
    }

    private static void indent(StringBuilder out, int depth) {
        for (int i = 0; i < depth; i++) {
            out.append("  ");
        }
    }

    /** Write a JSON string literal, escaping everything that has to be escaped. */
    static void writeString(StringBuilder out, String text) {
        out.append('"');
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"':
                    out.append("\\\"");
                    break;
                case '\\':
                    out.append("\\\\");
                    break;
                case '\b':
                    out.append("\\b");
                    break;
                case '\f':
                    out.append("\\f");
                    break;
                case '\n':
                    out.append("\\n");
                    break;
                case '\r':
                    out.append("\\r");
                    break;
                case '\t':
                    out.append("\\t");
                    break;
                default:
                    if (c < 0x20) {
                        // Other control characters have no short escape: use a
                        // backslash-u escape with four hex digits.
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        out.append('"');
    }

    // ------------------------------------------------------------------- read

    /**
     * Parse a JSON document.
     *
     * @return a {@link Map}, {@link List}, {@link String}, {@link Long},
     *         {@link Double}, {@link Boolean} or {@code null}
     */
    public static Object parse(String text) {
        if (text == null) {
            throw new IllegalArgumentException("cannot parse null");
        }
        Parser parser = new Parser(text);
        parser.skipWhitespace();
        Object value = parser.parseValue();
        parser.skipWhitespace();
        if (!parser.atEnd()) {
            throw new IllegalArgumentException(
                    "unexpected trailing content at offset " + parser.pos);
        }
        return value;
    }

    /**
     * Parse a VeriSpec trace.
     *
     * <p>A trace is a non-empty JSON list of steps; every step is an object
     * with a {@code state} object, an {@code action} string and a
     * {@code params} object.  The first step must be the {@code init} step
     * (a missing action there is read as {@code "init"}).
     *
     * @return the steps, in order
     */
    public static List<Map<String, Object>> parseTrace(String text) {
        Object raw = parse(text);
        if (!(raw instanceof List<?>)) {
            throw new IllegalArgumentException("trace must be a JSON array");
        }
        List<?> items = (List<?>) raw;
        if (items.isEmpty()) {
            throw new IllegalArgumentException("trace must contain at least one step");
        }
        List<Map<String, Object>> steps = new ArrayList<>(items.size());
        for (int i = 0; i < items.size(); i++) {
            Object item = items.get(i);
            if (!(item instanceof Map<?, ?>)) {
                throw new IllegalArgumentException("trace step " + i + " must be a JSON object");
            }
            Map<String, Object> step = toStringKeyedMap((Map<?, ?>) item);
            if (!step.containsKey("state")) {
                throw new IllegalArgumentException("trace step " + i + ": missing 'state'");
            }
            if (i == 0) {
                Object action = step.get("action");
                if (action != null && !"init".equals(String.valueOf(action))) {
                    throw new IllegalArgumentException(
                            "trace step 0: the first step must be the 'init' step");
                }
            }
            steps.add(step);
        }
        return steps;
    }

    /** Copy a map into a map with string keys, preserving the iteration order. */
    public static Map<String, Object> toStringKeyedMap(Map<?, ?> raw) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : raw.entrySet()) {
            out.put(String.valueOf(entry.getKey()), entry.getValue());
        }
        return out;
    }

    // ----------------------------------------------------------------- parser

    private static final class Parser {
        private final String text;
        private int pos;

        Parser(String text) {
            this.text = text;
        }

        boolean atEnd() {
            return pos >= text.length();
        }

        void skipWhitespace() {
            while (pos < text.length()) {
                char c = text.charAt(pos);
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                    pos++;
                } else {
                    break;
                }
            }
        }

        Object parseValue() {
            skipWhitespace();
            if (atEnd()) {
                throw new IllegalArgumentException("unexpected end of JSON input");
            }
            char c = text.charAt(pos);
            switch (c) {
                case '{':
                    return parseObject();
                case '[':
                    return parseArray();
                case '"':
                    return parseString();
                case 't':
                    expect("true");
                    return Boolean.TRUE;
                case 'f':
                    expect("false");
                    return Boolean.FALSE;
                case 'n':
                    expect("null");
                    return null;
                default:
                    return parseNumber();
            }
        }

        private void expect(String literal) {
            if (!text.startsWith(literal, pos)) {
                throw new IllegalArgumentException(
                        "expected '" + literal + "' at offset " + pos);
            }
            pos += literal.length();
        }

        private Map<String, Object> parseObject() {
            Map<String, Object> out = new LinkedHashMap<>();
            pos++; // '{'
            skipWhitespace();
            if (!atEnd() && text.charAt(pos) == '}') {
                pos++;
                return out;
            }
            while (true) {
                skipWhitespace();
                if (atEnd() || text.charAt(pos) != '"') {
                    throw new IllegalArgumentException("expected a string key at offset " + pos);
                }
                String key = parseString();
                skipWhitespace();
                if (atEnd() || text.charAt(pos) != ':') {
                    throw new IllegalArgumentException("expected ':' at offset " + pos);
                }
                pos++;
                out.put(key, parseValue());
                skipWhitespace();
                if (atEnd()) {
                    throw new IllegalArgumentException("unterminated object");
                }
                char c = text.charAt(pos);
                if (c == ',') {
                    pos++;
                    continue;
                }
                if (c == '}') {
                    pos++;
                    return out;
                }
                throw new IllegalArgumentException("expected ',' or '}' at offset " + pos);
            }
        }

        private List<Object> parseArray() {
            List<Object> out = new ArrayList<>();
            pos++; // '['
            skipWhitespace();
            if (!atEnd() && text.charAt(pos) == ']') {
                pos++;
                return out;
            }
            while (true) {
                out.add(parseValue());
                skipWhitespace();
                if (atEnd()) {
                    throw new IllegalArgumentException("unterminated array");
                }
                char c = text.charAt(pos);
                if (c == ',') {
                    pos++;
                    continue;
                }
                if (c == ']') {
                    pos++;
                    return out;
                }
                throw new IllegalArgumentException("expected ',' or ']' at offset " + pos);
            }
        }

        private String parseString() {
            pos++; // opening quote
            StringBuilder out = new StringBuilder();
            while (true) {
                if (atEnd()) {
                    throw new IllegalArgumentException("unterminated string");
                }
                char c = text.charAt(pos++);
                if (c == '"') {
                    return out.toString();
                }
                if (c != '\\') {
                    out.append(c);
                    continue;
                }
                if (atEnd()) {
                    throw new IllegalArgumentException("unterminated escape sequence");
                }
                char escape = text.charAt(pos++);
                switch (escape) {
                    case '"':
                        out.append('"');
                        break;
                    case '\\':
                        out.append('\\');
                        break;
                    case '/':
                        out.append('/');
                        break;
                    case 'b':
                        out.append('\b');
                        break;
                    case 'f':
                        out.append('\f');
                        break;
                    case 'n':
                        out.append('\n');
                        break;
                    case 'r':
                        out.append('\r');
                        break;
                    case 't':
                        out.append('\t');
                        break;
                    case 'u':
                        if (pos + 4 > text.length()) {
                            throw new IllegalArgumentException("truncated \\u escape sequence");
                        }
                        String hex = text.substring(pos, pos + 4);
                        pos += 4;
                        try {
                            out.append((char) Integer.parseInt(hex, 16));
                        } catch (NumberFormatException e) {
                            throw new IllegalArgumentException("bad \\u escape sequence: " + hex);
                        }
                        break;
                    default:
                        throw new IllegalArgumentException("unknown escape sequence: \\" + escape);
                }
            }
        }

        private Object parseNumber() {
            int start = pos;
            boolean floating = false;
            if (!atEnd() && (text.charAt(pos) == '-' || text.charAt(pos) == '+')) {
                pos++;
            }
            while (!atEnd()) {
                char c = text.charAt(pos);
                if (c >= '0' && c <= '9') {
                    pos++;
                } else if (c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-') {
                    floating = true;
                    pos++;
                } else {
                    break;
                }
            }
            String raw = text.substring(start, pos);
            if (raw.isEmpty()) {
                throw new IllegalArgumentException("expected a number at offset " + start);
            }
            try {
                if (floating) {
                    return Double.valueOf(raw);
                }
                return Long.valueOf(raw);
            } catch (NumberFormatException e) {
                throw new IllegalArgumentException("bad number: " + raw);
            }
        }
    }
}
