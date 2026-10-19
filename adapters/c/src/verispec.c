/* The VeriSpec adapter SDK for C: the runtime, the JSON codec and the trace
 * recorder.  See verispec.h for the contract. */

#include "verispec.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/* values                                                              */
/* ------------------------------------------------------------------ */

static veri_value *value_new(veri_kind kind) {
    veri_value *v = (veri_value *)calloc(1, sizeof(veri_value));
    if (v == NULL) {
        return NULL;
    }
    v->kind = kind;
    return v;
}

veri_value *veri_value_new_null(void) {
    return value_new(VERI_NULL);
}

veri_value *veri_value_new_bool(int b) {
    veri_value *v = value_new(VERI_BOOL);
    if (v != NULL) {
        v->b = b ? 1 : 0;
    }
    return v;
}

veri_value *veri_value_new_int(long long i) {
    veri_value *v = value_new(VERI_INT);
    if (v != NULL) {
        v->i = i;
        v->d = (double)i;
    }
    return v;
}

veri_value *veri_value_new_float(double d) {
    veri_value *v = value_new(VERI_FLOAT);
    if (v != NULL) {
        v->d = d;
    }
    return v;
}

veri_value *veri_value_new_str(const char *s) {
    veri_value *v = value_new(VERI_STR);
    if (v == NULL) {
        return NULL;
    }
    v->s = s == NULL ? NULL : strdup(s);
    if (s != NULL && v->s == NULL) {
        free(v);
        return NULL;
    }
    return v;
}

veri_value *veri_value_new_object(void) {
    return value_new(VERI_OBJ);
}

veri_value *veri_value_new_array(void) {
    return value_new(VERI_ARR);
}

void veri_value_free(veri_value *v) {
    size_t i;
    if (v == NULL) {
        return;
    }
    free(v->s);
    if (v->keys != NULL) {
        for (i = 0; i < v->count; i++) {
            free(v->keys[i]);
        }
        free(v->keys);
    }
    if (v->vals != NULL) {
        for (i = 0; i < v->count; i++) {
            veri_value_free(v->vals[i]);
        }
        free(v->vals);
    }
    if (v->items != NULL) {
        for (i = 0; i < v->count; i++) {
            veri_value_free(v->items[i]);
        }
        free(v->items);
    }
    free(v);
}

veri_value *veri_value_clone(const veri_value *v) {
    veri_value *out;
    size_t i;
    if (v == NULL) {
        return NULL;
    }
    switch (v->kind) {
        case VERI_STR:
            return veri_value_new_str(v->s);
        case VERI_BOOL:
            return veri_value_new_bool(v->b);
        case VERI_INT:
            return veri_value_new_int(v->i);
        case VERI_FLOAT:
            return veri_value_new_float(v->d);
        case VERI_OBJ:
            out = veri_value_new_object();
            if (out == NULL) {
                return NULL;
            }
            for (i = 0; i < v->count; i++) {
                veri_value *child = veri_value_clone(v->vals[i]);
                if (child == NULL) {
                    veri_value_free(out);
                    return NULL;
                }
                veri_object_set(out, v->keys[i], child);
            }
            return out;
        case VERI_ARR:
            out = veri_value_new_array();
            if (out == NULL) {
                return NULL;
            }
            for (i = 0; i < v->count; i++) {
                veri_value *child = veri_value_clone(v->items[i]);
                if (child == NULL) {
                    veri_value_free(out);
                    return NULL;
                }
                veri_array_append(out, child);
            }
            return out;
        default:
            return veri_value_new_null();
    }
}

static int number_equal(const veri_value *a, const veri_value *b) {
    if (a->kind == VERI_INT && b->kind == VERI_INT) {
        return a->i == b->i;
    }
    return a->d == b->d;
}

int veri_value_equal(const veri_value *a, const veri_value *b) {
    size_t i;
    if (a == NULL || b == NULL) {
        return a == b;
    }
    if ((a->kind == VERI_INT || a->kind == VERI_FLOAT) &&
        (b->kind == VERI_INT || b->kind == VERI_FLOAT)) {
        return number_equal(a, b);
    }
    if (a->kind != b->kind) {
        return 0;
    }
    switch (a->kind) {
        case VERI_NULL:
            return 1;
        case VERI_BOOL:
            return a->b == b->b;
        case VERI_STR:
            return strcmp(a->s ? a->s : "", b->s ? b->s : "") == 0;
        case VERI_OBJ:
            if (a->count != b->count) {
                return 0;
            }
            for (i = 0; i < a->count; i++) {
                const veri_value *other = veri_object_get(b, a->keys[i]);
                if (other == NULL || !veri_value_equal(a->vals[i], other)) {
                    return 0;
                }
            }
            return 1;
        case VERI_ARR:
            if (a->count != b->count) {
                return 0;
            }
            for (i = 0; i < a->count; i++) {
                if (!veri_value_equal(a->items[i], b->items[i])) {
                    return 0;
                }
            }
            return 1;
        default:
            return 0;
    }
}

void veri_object_set(veri_value *object, const char *key, veri_value *value) {
    char **keys;
    veri_value **vals;
    size_t i;
    if (object == NULL || object->kind != VERI_OBJ || key == NULL) {
        veri_value_free(value);
        return;
    }
    for (i = 0; i < object->count; i++) {
        if (strcmp(object->keys[i], key) == 0) {
            veri_value_free(object->vals[i]);
            object->vals[i] = value;
            return;
        }
    }
    keys = (char **)realloc(object->keys, (object->count + 1) * sizeof(char *));
    if (keys == NULL) {
        veri_value_free(value);
        return;
    }
    object->keys = keys;
    vals = (veri_value **)realloc(object->vals, (object->count + 1) * sizeof(veri_value *));
    if (vals == NULL) {
        veri_value_free(value);
        return;
    }
    object->vals = vals;
    object->keys[object->count] = strdup(key);
    object->vals[object->count] = value;
    object->count += 1;
}

void veri_array_append(veri_value *array, veri_value *value) {
    veri_value **items;
    if (array == NULL || array->kind != VERI_ARR) {
        veri_value_free(value);
        return;
    }
    items = (veri_value **)realloc(array->items, (array->count + 1) * sizeof(veri_value *));
    if (items == NULL) {
        veri_value_free(value);
        return;
    }
    array->items = items;
    array->items[array->count] = value;
    array->count += 1;
}

const veri_value *veri_object_get(const veri_value *object, const char *key) {
    size_t i;
    if (object == NULL || object->kind != VERI_OBJ || key == NULL) {
        return NULL;
    }
    for (i = 0; i < object->count; i++) {
        if (strcmp(object->keys[i], key) == 0) {
            return object->vals[i];
        }
    }
    return NULL;
}

size_t veri_array_length(const veri_value *array) {
    if (array == NULL || array->kind != VERI_ARR) {
        return 0;
    }
    return array->count;
}

const veri_value *veri_array_get(const veri_value *array, size_t index) {
    if (array == NULL || array->kind != VERI_ARR || index >= array->count) {
        return NULL;
    }
    return array->items[index];
}

/* ------------------------------------------------------------------ */
/* JSON                                                                */
/* ------------------------------------------------------------------ */

typedef struct {
    const char *p;
    int failed;
} veri_parser;

static void skip_ws(veri_parser *ps) {
    while (*ps->p == ' ' || *ps->p == '\t' || *ps->p == '\n' || *ps->p == '\r') {
        ps->p++;
    }
}

static veri_value *parse_value(veri_parser *ps);

static int hex_value(char c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

static void utf8_encode(unsigned long cp, char *out, size_t *len) {
    if (cp < 0x80) {
        out[(*len)++] = (char)cp;
    } else if (cp < 0x800) {
        out[(*len)++] = (char)(0xC0 | (cp >> 6));
        out[(*len)++] = (char)(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
        out[(*len)++] = (char)(0xE0 | (cp >> 12));
        out[(*len)++] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[(*len)++] = (char)(0x80 | (cp & 0x3F));
    } else {
        out[(*len)++] = (char)(0xF0 | (cp >> 18));
        out[(*len)++] = (char)(0x80 | ((cp >> 12) & 0x3F));
        out[(*len)++] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[(*len)++] = (char)(0x80 | (cp & 0x3F));
    }
}

static veri_value *parse_string(veri_parser *ps) {
    size_t cap = 32;
    size_t len = 0;
    char *buf = (char *)malloc(cap);
    veri_value *out;
    if (buf == NULL) {
        ps->failed = 1;
        return NULL;
    }
    ps->p++; /* the opening quote */
    while (*ps->p != '\0' && *ps->p != '"') {
        char c = *ps->p++;
        if (c == '\\') {
            char e = *ps->p++;
            if (e == '\0') {
                break;
            }
            switch (e) {
                case 'n': c = '\n'; break;
                case 't': c = '\t'; break;
                case 'r': c = '\r'; break;
                case 'b': c = '\b'; break;
                case 'f': c = '\f'; break;
                case '"': c = '"'; break;
                case '\\': c = '\\'; break;
                case '/': c = '/'; break;
                case 'u': {
                    unsigned long cp = 0;
                    int i;
                    for (i = 0; i < 4; i++) {
                        int h = hex_value(*ps->p++);
                        if (h < 0) {
                            ps->failed = 1;
                            free(buf);
                            return NULL;
                        }
                        cp = (cp << 4) | (unsigned long)h;
                    }
                    if (cp >= 0xD800 && cp <= 0xDBFF && ps->p[0] == '\\' && ps->p[1] == 'u') {
                        unsigned long low = 0;
                        int ok = 1;
                        ps->p += 2;
                        for (i = 0; i < 4; i++) {
                            int h = hex_value(*ps->p++);
                            if (h < 0) {
                                ok = 0;
                                break;
                            }
                            low = (low << 4) | (unsigned long)h;
                        }
                        if (ok && low >= 0xDC00 && low <= 0xDFFF) {
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
                        } else {
                            ok = 0;
                        }
                        if (!ok) {
                            ps->failed = 1;
                            free(buf);
                            return NULL;
                        }
                    }
                    if (len + 4 >= cap) {
                        char *bigger;
                        cap *= 2;
                        bigger = (char *)realloc(buf, cap);
                        if (bigger == NULL) {
                            ps->failed = 1;
                            free(buf);
                            return NULL;
                        }
                        buf = bigger;
                    }
                    utf8_encode(cp, buf, &len);
                    continue;
                }
                default:
                    ps->failed = 1;
                    free(buf);
                    return NULL;
            }
        }
        if (len + 1 >= cap) {
            char *bigger = (char *)realloc(buf, cap * 2);
            if (bigger == NULL) {
                ps->failed = 1;
                free(buf);
                return NULL;
            }
            buf = bigger;
            cap *= 2;
        }
        buf[len++] = c;
    }
    if (*ps->p != '"') {
        ps->failed = 1;
        free(buf);
        return NULL;
    }
    ps->p++;
    buf[len] = '\0';
    out = veri_value_new_str(buf);
    free(buf);
    if (out == NULL) {
        ps->failed = 1;
    }
    return out;
}

static veri_value *parse_number(veri_parser *ps) {
    const char *start = ps->p;
    int is_float = 0;
    char *end = NULL;
    if (*ps->p == '-' || *ps->p == '+') {
        ps->p++;
    }
    while (*ps->p >= '0' && *ps->p <= '9') {
        ps->p++;
    }
    if (*ps->p == '.') {
        is_float = 1;
        ps->p++;
        while (*ps->p >= '0' && *ps->p <= '9') {
            ps->p++;
        }
    }
    if (*ps->p == 'e' || *ps->p == 'E') {
        is_float = 1;
        ps->p++;
        if (*ps->p == '-' || *ps->p == '+') {
            ps->p++;
        }
        while (*ps->p >= '0' && *ps->p <= '9') {
            ps->p++;
        }
    }
    if (ps->p == start) {
        ps->failed = 1;
        return NULL;
    }
    if (is_float) {
        double d = strtod(start, &end);
        return veri_value_new_float(d);
    }
    return veri_value_new_int(strtoll(start, &end, 10));
}

static veri_value *parse_array(veri_parser *ps) {
    veri_value *array = veri_value_new_array();
    if (array == NULL) {
        ps->failed = 1;
        return NULL;
    }
    ps->p++; /* '[' */
    skip_ws(ps);
    if (*ps->p == ']') {
        ps->p++;
        return array;
    }
    for (;;) {
        veri_value *item;
        skip_ws(ps);
        item = parse_value(ps);
        if (item == NULL) {
            veri_value_free(array);
            ps->failed = 1;
            return NULL;
        }
        veri_array_append(array, item);
        skip_ws(ps);
        if (*ps->p == ',') {
            ps->p++;
            continue;
        }
        if (*ps->p == ']') {
            ps->p++;
            return array;
        }
        veri_value_free(array);
        ps->failed = 1;
        return NULL;
    }
}

static veri_value *parse_object(veri_parser *ps) {
    veri_value *object = veri_value_new_object();
    if (object == NULL) {
        ps->failed = 1;
        return NULL;
    }
    ps->p++; /* '{' */
    skip_ws(ps);
    if (*ps->p == '}') {
        ps->p++;
        return object;
    }
    for (;;) {
        veri_value *key;
        veri_value *value;
        skip_ws(ps);
        if (*ps->p != '"') {
            veri_value_free(object);
            ps->failed = 1;
            return NULL;
        }
        key = parse_string(ps);
        if (key == NULL) {
            veri_value_free(object);
            ps->failed = 1;
            return NULL;
        }
        skip_ws(ps);
        if (*ps->p != ':') {
            veri_value_free(key);
            veri_value_free(object);
            ps->failed = 1;
            return NULL;
        }
        ps->p++;
        skip_ws(ps);
        value = parse_value(ps);
        if (value == NULL) {
            veri_value_free(key);
            veri_value_free(object);
            ps->failed = 1;
            return NULL;
        }
        veri_object_set(object, key->s ? key->s : "", value);
        veri_value_free(key);
        skip_ws(ps);
        if (*ps->p == ',') {
            ps->p++;
            continue;
        }
        if (*ps->p == '}') {
            ps->p++;
            return object;
        }
        veri_value_free(object);
        ps->failed = 1;
        return NULL;
    }
}

static veri_value *parse_value(veri_parser *ps) {
    skip_ws(ps);
    switch (*ps->p) {
        case '{':
            return parse_object(ps);
        case '[':
            return parse_array(ps);
        case '"':
            return parse_string(ps);
        case 't':
            if (strncmp(ps->p, "true", 4) == 0) {
                ps->p += 4;
                return veri_value_new_bool(1);
            }
            break;
        case 'f':
            if (strncmp(ps->p, "false", 5) == 0) {
                ps->p += 5;
                return veri_value_new_bool(0);
            }
            break;
        case 'n':
            if (strncmp(ps->p, "null", 4) == 0) {
                ps->p += 4;
                return veri_value_new_null();
            }
            break;
        default:
            if (*ps->p == '-' || *ps->p == '+' || (*ps->p >= '0' && *ps->p <= '9')) {
                return parse_number(ps);
            }
            break;
    }
    ps->failed = 1;
    return NULL;
}

veri_value *veri_json_parse(const char *text) {
    veri_parser ps;
    veri_value *value;
    if (text == NULL) {
        return NULL;
    }
    ps.p = text;
    ps.failed = 0;
    value = parse_value(&ps);
    if (value == NULL || ps.failed) {
        veri_value_free(value);
        return NULL;
    }
    skip_ws(&ps);
    if (*ps.p != '\0') {
        veri_value_free(value);
        return NULL;
    }
    return value;
}

typedef struct {
    char *data;
    size_t len;
    size_t cap;
} veri_buffer;

static void buffer_reserve(veri_buffer *buf, size_t extra) {
    if (buf->len + extra + 1 > buf->cap) {
        size_t cap = buf->cap == 0 ? 64 : buf->cap;
        char *bigger;
        while (cap < buf->len + extra + 1) {
            cap *= 2;
        }
        bigger = (char *)realloc(buf->data, cap);
        if (bigger == NULL) {
            return;
        }
        buf->data = bigger;
        buf->cap = cap;
    }
}

static void buffer_puts(veri_buffer *buf, const char *text) {
    size_t n = strlen(text);
    buffer_reserve(buf, n);
    if (buf->data == NULL) {
        return;
    }
    memcpy(buf->data + buf->len, text, n);
    buf->len += n;
    buf->data[buf->len] = '\0';
}

static void buffer_putc(veri_buffer *buf, char c) {
    buffer_reserve(buf, 1);
    if (buf->data == NULL) {
        return;
    }
    buf->data[buf->len++] = c;
    buf->data[buf->len] = '\0';
}

static void buffer_putll(veri_buffer *buf, long long value) {
    char text[32];
    snprintf(text, sizeof text, "%lld", value);
    buffer_puts(buf, text);
}

static void write_json(const veri_value *v, veri_buffer *buf) {
    size_t i;
    if (v == NULL) {
        buffer_puts(buf, "null");
        return;
    }
    switch (v->kind) {
        case VERI_NULL:
            buffer_puts(buf, "null");
            break;
        case VERI_BOOL:
            buffer_puts(buf, v->b ? "true" : "false");
            break;
        case VERI_INT:
            buffer_putll(buf, v->i);
            break;
        case VERI_FLOAT: {
            char text[64];
            snprintf(text, sizeof text, "%.17g", v->d);
            buffer_puts(buf, text);
            break;
        }
        case VERI_STR:
            buffer_putc(buf, '"');
            buffer_puts(buf, v->s ? v->s : "");
            buffer_putc(buf, '"');
            break;
        case VERI_OBJ:
            buffer_putc(buf, '{');
            for (i = 0; i < v->count; i++) {
                if (i > 0) {
                    buffer_putc(buf, ',');
                }
                buffer_putc(buf, '"');
                buffer_puts(buf, v->keys[i]);
                buffer_puts(buf, "\":");
                write_json(v->vals[i], buf);
            }
            buffer_putc(buf, '}');
            break;
        case VERI_ARR:
            buffer_putc(buf, '[');
            for (i = 0; i < v->count; i++) {
                if (i > 0) {
                    buffer_putc(buf, ',');
                }
                write_json(v->items[i], buf);
            }
            buffer_putc(buf, ']');
            break;
        default:
            buffer_puts(buf, "null");
            break;
    }
}

char *veri_json_write(const veri_value *v) {
    veri_buffer buf;
    buf.data = NULL;
    buf.len = 0;
    buf.cap = 0;
    buffer_reserve(&buf, 0);
    if (buf.data == NULL) {
        return NULL;
    }
    buf.data[0] = '\0';
    write_json(v, &buf);
    return buf.data;
}

void veri_json_quote(const char *in, char *out, size_t out_len) {
    size_t len = 0;
    if (out == NULL || out_len == 0) {
        return;
    }
    if (len + 1 < out_len) {
        out[len++] = '"';
    }
    for (; in != NULL && *in != '\0' && len + 2 < out_len; in++) {
        unsigned char c = (unsigned char)*in;
        switch (c) {
            case '"':
                out[len++] = '\\';
                out[len++] = '"';
                break;
            case '\\':
                out[len++] = '\\';
                out[len++] = '\\';
                break;
            case '\n':
                out[len++] = '\\';
                out[len++] = 'n';
                break;
            case '\r':
                out[len++] = '\\';
                out[len++] = 'r';
                break;
            case '\t':
                out[len++] = '\\';
                out[len++] = 't';
                break;
            default:
                if (c < 0x20) {
                    char text[8];
                    snprintf(text, sizeof text, "\\u%04x", c);
                    memcpy(out + len, text, 6);
                    len += 6;
                } else {
                    out[len++] = (char)c;
                }
                break;
        }
    }
    if (len + 1 < out_len) {
        out[len++] = '"';
    }
    out[len] = '\0';
}

/* ------------------------------------------------------------------ */
/* the runtime                                                         */
/* ------------------------------------------------------------------ */

long long veri_len(const veri_value *v) {
    if (v == NULL) {
        return 0;
    }
    if (v->kind == VERI_ARR || v->kind == VERI_OBJ) {
        return (long long)v->count;
    }
    if (v->kind == VERI_STR) {
        return veri_str_len(v->s);
    }
    return 0;
}

long long veri_str_len(const char *s) {
    if (s == NULL) {
        return 0;
    }
    return (long long)strlen(s);
}

int veri_str_eq(const char *a, const char *b) {
    return strcmp(a ? a : "", b ? b : "") == 0;
}

int veri_str_contains(const char *haystack, const char *needle) {
    if (haystack == NULL || needle == NULL) {
        return 0;
    }
    return strstr(haystack, needle) != NULL;
}

long long veri_abs(long long x) {
    return x < 0 ? -x : x;
}

long long veri_floordiv(long long a, long long b) {
    long long q;
    long long r;
    if (b == 0) {
        return 0;
    }
    q = a / b;
    r = a % b;
    if (r != 0 && ((r < 0) != (b < 0))) {
        q -= 1;
    }
    return q;
}

long long veri_mod(long long a, long long b) {
    long long r;
    if (b == 0) {
        return 0;
    }
    r = a % b;
    if (r != 0 && ((r < 0) != (b < 0))) {
        r += b;
    }
    return r;
}

long long veri_min(long long a, long long b) {
    return a < b ? a : b;
}

long long veri_max(long long a, long long b) {
    return a > b ? a : b;
}

const char *veri_key(const veri_value *k) {
    static char buffer[64];
    if (k == NULL) {
        return "";
    }
    switch (k->kind) {
        case VERI_STR:
            return k->s ? k->s : "";
        case VERI_INT:
            snprintf(buffer, sizeof buffer, "%lld", k->i);
            return buffer;
        case VERI_BOOL:
            return k->b ? "true" : "false";
        default:
            return "";
    }
}

veri_value *veri_map_get(const veri_value *m, const char *key, const veri_value *def) {
    const veri_value *found = veri_object_get(m, key);
    if (found != NULL) {
        return veri_value_clone(found);
    }
    return veri_value_clone(def);
}

veri_value *veri_map_set(const veri_value *m, const char *key, const veri_value *v,
                          const veri_value *domain) {
    veri_value *out = veri_value_new_object();
    size_t i;
    int inside = 0;
    if (out == NULL) {
        return NULL;
    }
    if (m != NULL && m->kind == VERI_OBJ) {
        for (i = 0; i < m->count; i++) {
            veri_object_set(out, m->keys[i], veri_value_clone(m->vals[i]));
        }
    }
    if (domain != NULL && domain->kind == VERI_ARR) {
        for (i = 0; i < domain->count; i++) {
            if (strcmp(veri_key(domain->items[i]), key ? key : "") == 0) {
                inside = 1;
                break;
            }
        }
    }
    if (!inside) {
        return out;
    }
    veri_object_set(out, key, veri_value_clone(v));
    return out;
}

veri_value *veri_set_add(const veri_value *s, const veri_value *v, const veri_value *domain) {
    veri_value *out = veri_value_new_array();
    size_t i;
    int inside = 0;
    int present = 0;
    if (out == NULL) {
        return NULL;
    }
    if (s != NULL && s->kind == VERI_ARR) {
        for (i = 0; i < s->count; i++) {
            veri_array_append(out, veri_value_clone(s->items[i]));
            if (veri_value_equal(s->items[i], v)) {
                present = 1;
            }
        }
    }
    if (domain != NULL && domain->kind == VERI_ARR) {
        for (i = 0; i < domain->count; i++) {
            if (strcmp(veri_key(domain->items[i]), veri_key(v)) == 0) {
                inside = 1;
                break;
            }
        }
    }
    if (inside && !present) {
        veri_array_append(out, veri_value_clone(v));
    }
    return out;
}

int veri_set_has(const veri_value *s, const veri_value *v) {
    size_t i;
    if (s == NULL || s->kind != VERI_ARR) {
        return 0;
    }
    for (i = 0; i < s->count; i++) {
        if (veri_value_equal(s->items[i], v)) {
            return 1;
        }
    }
    return 0;
}

veri_value *veri_seq_get(const veri_value *q, long long i, const veri_value *def) {
    if (q == NULL || q->kind != VERI_ARR || i < 0 || (size_t)i >= q->count) {
        return veri_value_clone(def);
    }
    return veri_value_clone(q->items[i]);
}

veri_value *veri_seq_append(const veri_value *q, const veri_value *v) {
    veri_value *out = veri_value_new_array();
    size_t i;
    if (out == NULL) {
        return NULL;
    }
    if (q != NULL && q->kind == VERI_ARR) {
        for (i = 0; i < q->count; i++) {
            veri_array_append(out, veri_value_clone(q->items[i]));
        }
    }
    veri_array_append(out, veri_value_clone(v));
    return out;
}

veri_value *veri_keys(const veri_value *m) {
    veri_value *out = veri_value_new_array();
    size_t i;
    if (out == NULL) {
        return NULL;
    }
    if (m != NULL && m->kind == VERI_OBJ) {
        for (i = 0; i < m->count; i++) {
            veri_array_append(out, veri_value_new_str(m->keys[i]));
        }
    }
    return out;
}

veri_value *veri_values(const veri_value *m, const veri_value *def) {
    veri_value *out = veri_value_new_array();
    size_t i;
    if (out == NULL) {
        return NULL;
    }
    if (m != NULL && m->kind == VERI_OBJ) {
        for (i = 0; i < m->count; i++) {
            veri_array_append(out, veri_value_clone(m->vals[i]));
        }
    } else {
        veri_value_free(out);
        out = veri_value_clone(def);
        if (out == NULL) {
            out = veri_value_new_array();
        }
    }
    return out;
}

int veri_contains_key(const veri_value *m, const char *key) {
    return veri_object_get(m, key) != NULL;
}

int veri_contains(const veri_value *l, const veri_value *v) {
    size_t i;
    if (l == NULL || l->kind != VERI_ARR) {
        return 0;
    }
    for (i = 0; i < l->count; i++) {
        if (veri_value_equal(l->items[i], v)) {
            return 1;
        }
    }
    return 0;
}

veri_value *veri_record_get(const veri_value *r, const char *field, const veri_value *def) {
    const veri_value *found = veri_object_get(r, field);
    if (found != NULL) {
        return veri_value_clone(found);
    }
    return veri_value_clone(def);
}

/* ------------------------------------------------------------------ */
/* the generator                                                       */
/* ------------------------------------------------------------------ */

void veri_rng_init(veri_rng *rng, unsigned long long seed) {
    if (rng == NULL) {
        return;
    }
    rng->state = seed | 1ULL;
}

unsigned long long veri_rng_next(veri_rng *rng) {
    unsigned long long x;
    if (rng == NULL) {
        return 0;
    }
    x = rng->state;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    rng->state = x;
    return x * 0x2545F4914F6CDD1DULL;
}

long long veri_rng_range(veri_rng *rng, long long lo, long long hi) {
    unsigned long long span;
    if (hi <= lo) {
        return lo;
    }
    span = (unsigned long long)(hi - lo + 1);
    return lo + (long long)(veri_rng_next(rng) % span);
}

int veri_rng_bool(veri_rng *rng) {
    return (veri_rng_next(rng) & 1ULL) == 1ULL;
}

/* ------------------------------------------------------------------ */
/* traces                                                              */
/* ------------------------------------------------------------------ */

struct veri_trace {
    char **actions;
    veri_value **params;
    veri_value **states;
    size_t count;
    size_t cap;
};

veri_trace *veri_trace_new(void) {
    return (veri_trace *)calloc(1, sizeof(veri_trace));
}

void veri_trace_free(veri_trace *t) {
    size_t i;
    if (t == NULL) {
        return;
    }
    for (i = 0; i < t->count; i++) {
        free(t->actions[i]);
        veri_value_free(t->params[i]);
        veri_value_free(t->states[i]);
    }
    free(t->actions);
    free(t->params);
    free(t->states);
    free(t);
}

int veri_trace_record(veri_trace *t, const char *action, const char *params_json,
                      const char *state_json) {
    veri_value *params;
    veri_value *state;
    if (t == NULL || action == NULL) {
        return 0;
    }
    params = veri_json_parse(params_json == NULL ? "{}" : params_json);
    if (params == NULL) {
        return 0;
    }
    state = veri_json_parse(state_json == NULL ? "{}" : state_json);
    if (state == NULL) {
        veri_value_free(params);
        return 0;
    }
    if (t->count == t->cap) {
        size_t cap = t->cap == 0 ? 8 : t->cap * 2;
        char **actions = (char **)realloc(t->actions, cap * sizeof(char *));
        veri_value **params_slots = (veri_value **)realloc(t->params, cap * sizeof(veri_value *));
        veri_value **state_slots = (veri_value **)realloc(t->states, cap * sizeof(veri_value *));
        if (actions == NULL || params_slots == NULL || state_slots == NULL) {
            free(actions);
            free(params_slots);
            free(state_slots);
            veri_value_free(params);
            veri_value_free(state);
            return 0;
        }
        t->actions = actions;
        t->params = params_slots;
        t->states = state_slots;
        t->cap = cap;
    }
    t->actions[t->count] = strdup(action);
    t->params[t->count] = params;
    t->states[t->count] = state;
    t->count += 1;
    return 1;
}

char *veri_trace_to_json(const veri_trace *t) {
    veri_buffer buf;
    size_t i;
    buf.data = NULL;
    buf.len = 0;
    buf.cap = 0;
    buffer_reserve(&buf, 0);
    if (buf.data == NULL) {
        return NULL;
    }
    buf.data[0] = '\0';
    buffer_putc(&buf, '[');
    if (t != NULL) {
        for (i = 0; i < t->count; i++) {
            char quoted[512];
            char *params = veri_json_write(t->params[i]);
            char *state = veri_json_write(t->states[i]);
            if (i > 0) {
                buffer_putc(&buf, ',');
            }
            veri_json_quote(t->actions[i], quoted, sizeof quoted);
            buffer_puts(&buf, "{\"action\":");
            buffer_puts(&buf, quoted);
            buffer_puts(&buf, ",\"params\":");
            buffer_puts(&buf, params ? params : "{}");
            buffer_puts(&buf, ",\"state\":");
            buffer_puts(&buf, state ? state : "{}");
            buffer_putc(&buf, '}');
            free(params);
            free(state);
        }
    }
    buffer_putc(&buf, ']');
    return buf.data;
}

int veri_trace_write(const veri_trace *t, const char *path) {
    char *text;
    FILE *f;
    if (path == NULL) {
        return 0;
    }
    text = veri_trace_to_json(t);
    if (text == NULL) {
        return 0;
    }
    f = fopen(path, "w");
    if (f == NULL) {
        free(text);
        return 0;
    }
    fputs(text, f);
    fclose(f);
    free(text);
    return 1;
}
