/* The VeriSpec adapter SDK for C.
 *
 * A VeriSpec adapter connects a real implementation to a human owned spec.
 * The contract is three functions:
 *
 *     void veri_reset(void);
 *     void veri_apply(const char *action, const char *params_json);
 *     void veri_snapshot(char *out, size_t out_len);
 *
 * `veri_apply` receives the action name and the parameters as a JSON object.
 * `veri_snapshot` writes the state as a JSON object whose keys are exactly
 * the state variables of the spec, and nothing else.
 *
 * The C adapter supports *scalar* specs only: `int`, `bool`, `str` and
 * `enum[...]`.  A spec with a map, a set, a sequence or a record needs the
 * Rust adapter.
 *
 * The runtime helpers below have the same names and the same semantics as in
 * the other backends:
 *
 *     len(x)                 length of a collection or a string
 *     map_get(m, k)          read a key (the value type default when missing)
 *     map_set(m, k, v)       a new map with the key set
 *     set_add(s, v)          a new set with the element added
 *     set_has(s, v)          element membership
 *     seq_get(q, i)          element at an index (the default when out of range)
 *     seq_append(q, v)       a new sequence
 *     keys(m) / values(m)    keys / values of a map
 *     contains_key(m, k)     key membership in a map
 *     contains(l, v)         element membership in a list
 *
 * Maps are JSON objects (string keys), sets and sequences are JSON arrays and
 * records are JSON objects keyed by field name.  That keeps the generated
 * code, the trace format and the adapter contract in sync.
 *
 * This SDK does not claim a proof: the evidence a VeriSpec run produces is
 * *conformance-tested*, never "proven".
 */

#ifndef VERISPEC_H
#define VERISPEC_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ */
/* the adapter contract                                                */
/* ------------------------------------------------------------------ */

/* The three functions an adapter provides.  They are declared here so that
 * an adapter can include this header and get the signatures; the generated
 * conformance test declares them as well, so an adapter that does not
 * include this header still links. */

/* Put the implementation in the initial state of the spec. */
void veri_reset(void);

/* Apply one action.  `params_json` is a JSON object with the action's
 * parameters, for example {"amount": 20}. */
void veri_apply(const char *action, const char *params_json);

/* Write the current state as a JSON object with exactly the spec's
 * variables, for example {"balance_a":50,"balance_b":50}. */
void veri_snapshot(char *out, size_t out_len);

/* ------------------------------------------------------------------ */
/* values                                                              */
/* ------------------------------------------------------------------ */

typedef enum {
    VERI_NULL = 0,
    VERI_BOOL,
    VERI_INT,
    VERI_FLOAT,
    VERI_STR,
    VERI_OBJ,
    VERI_ARR
} veri_kind;

/* A JSON value.  The members are public on purpose: an adapter may read
 * them directly (`value->i`, `value->s`, ...). */
typedef struct veri_value veri_value;

struct veri_value {
    veri_kind kind;
    int b;                /* VERI_BOOL */
    long long i;          /* VERI_INT */
    double d;             /* VERI_FLOAT */
    char *s;              /* VERI_STR, owned */
    char **keys;          /* VERI_OBJ, owned */
    veri_value **vals;    /* VERI_OBJ, owned */
    size_t count;         /* VERI_OBJ: members, VERI_ARR: items */
    veri_value **items;   /* VERI_ARR, owned */
};

veri_value *veri_value_new_null(void);
veri_value *veri_value_new_bool(int b);
veri_value *veri_value_new_int(long long i);
veri_value *veri_value_new_float(double d);
veri_value *veri_value_new_str(const char *s);
veri_value *veri_value_new_object(void);
veri_value *veri_value_new_array(void);
void veri_value_free(veri_value *v);
veri_value *veri_value_clone(const veri_value *v);

/* Deep equality.  Numbers are compared by value, objects key by key. */
int veri_value_equal(const veri_value *a, const veri_value *b);

/* `value` becomes the property of `object` (the object owns it). */
void veri_object_set(veri_value *object, const char *key, veri_value *value);
/* `value` becomes an item of `array` (the array owns it). */
void veri_array_append(veri_value *array, veri_value *value);
const veri_value *veri_object_get(const veri_value *object, const char *key);
size_t veri_array_length(const veri_value *array);
const veri_value *veri_array_get(const veri_value *array, size_t index);

/* ------------------------------------------------------------------ */
/* JSON                                                                */
/* ------------------------------------------------------------------ */

/* Parse a JSON document.  Returns NULL when the text is not valid JSON. */
veri_value *veri_json_parse(const char *text);
/* Write a value as JSON.  The result is malloc'ed; free it with free(). */
char *veri_json_write(const veri_value *v);
/* Write a JSON string literal (with the quotes) into `out`. */
void veri_json_quote(const char *in, char *out, size_t out_len);

/* ------------------------------------------------------------------ */
/* the runtime                                                         */
/* ------------------------------------------------------------------ */

/* The length of a collection (an array or an object). */
long long veri_len(const veri_value *v);
/* The length of a string, in bytes. */
long long veri_str_len(const char *s);
int veri_str_eq(const char *a, const char *b);
int veri_str_contains(const char *haystack, const char *needle);

long long veri_abs(long long x);
/* Floor division, like the `//` of the spec language. */
long long veri_floordiv(long long a, long long b);
/* The remainder with the sign of the divisor, like the `%` of the spec. */
long long veri_mod(long long a, long long b);
long long veri_min(long long a, long long b);
long long veri_max(long long a, long long b);

/* The string form of a map key or a set element. */
const char *veri_key(const veri_value *k);

veri_value *veri_map_get(const veri_value *m, const char *key, const veri_value *def);
veri_value *veri_map_set(const veri_value *m, const char *key, const veri_value *v,
                          const veri_value *domain);
veri_value *veri_set_add(const veri_value *s, const veri_value *v, const veri_value *domain);
int veri_set_has(const veri_value *s, const veri_value *v);
veri_value *veri_seq_get(const veri_value *q, long long i, const veri_value *def);
veri_value *veri_seq_append(const veri_value *q, const veri_value *v);
veri_value *veri_keys(const veri_value *m);
veri_value *veri_values(const veri_value *m, const veri_value *def);
int veri_contains_key(const veri_value *m, const char *key);
int veri_contains(const veri_value *l, const veri_value *v);
veri_value *veri_record_get(const veri_value *r, const char *field, const veri_value *def);

/* ------------------------------------------------------------------ */
/* a deterministic generator                                            */
/* ------------------------------------------------------------------ */

/* A 64 bit xorshift generator.  The conformance run must be reproducible, so
 * the generator is seeded with a constant and never reads the clock. */
typedef struct {
    unsigned long long state;
} veri_rng;

void veri_rng_init(veri_rng *rng, unsigned long long seed);
unsigned long long veri_rng_next(veri_rng *rng);
/* A value in `lo ..= hi`. */
long long veri_rng_range(veri_rng *rng, long long lo, long long hi);
int veri_rng_bool(veri_rng *rng);

/* ------------------------------------------------------------------ */
/* traces                                                              */
/* ------------------------------------------------------------------ */

/* The canonical trace format is a JSON list of
 * {"action": ..., "params": {...}, "state": {...}}, and the first entry is
 * the initial state:
 *
 *     veri_trace *trace = veri_trace_new();
 *     veri_reset();
 *     veri_trace_record(trace, "init", "{}", state_json);
 *     veri_apply("deposit", "{\"amount\": 10}");
 *     veri_trace_record(trace, "deposit", params_json, state_json);
 *     veri_trace_write(trace, "trace.json");
 *
 * The file is the language agnostic bridge: `verispec check-trace` decides
 * whether the run honours the contract. */
typedef struct veri_trace veri_trace;

veri_trace *veri_trace_new(void);
void veri_trace_free(veri_trace *t);
/* Record one step.  `params_json` and `state_json` must be valid JSON.
 * Returns 0 when they are not. */
int veri_trace_record(veri_trace *t, const char *action, const char *params_json,
                      const char *state_json);
/* The trace as a JSON list.  The result is malloc'ed; free it with free(). */
char *veri_trace_to_json(const veri_trace *t);
/* Write the trace to a file.  Returns 0 on success. */
int veri_trace_write(const veri_trace *t, const char *path);

#ifdef __cplusplus
}
#endif

#endif /* VERISPEC_H */
