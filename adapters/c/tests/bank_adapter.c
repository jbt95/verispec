/* A correct adapter for the `Bank` spec.
 *
 * The adapter is a translation layer: it reports what the implementation
 * does, it does not enforce the invariants a second time.  The preconditions
 * are checked here because a precondition that does not hold is a defect in
 * the implementation, and the generated test only calls enabled actions. */

#include <stdio.h>
#include <string.h>

#include "verispec.h"

static long long balance_a = 50;
static long long balance_b = 50;

void veri_reset(void) {
    balance_a = 50;
    balance_b = 50;
}

void veri_apply(const char *action, const char *params_json) {
    long long amount = 0;
    veri_value *params = veri_json_parse(params_json);
    if (params != NULL) {
        const veri_value *value = veri_object_get(params, "amount");
        if (value != NULL && value->kind == VERI_INT) {
            amount = value->i;
        }
        veri_value_free(params);
    }
    if (strcmp(action, "transfer_a_to_b") == 0) {
        if (!(amount > 0 && balance_a >= amount)) {
            fprintf(stderr, "bank_adapter: precondition failed for %s\n", action);
            return;
        }
        balance_a -= amount;
        balance_b += amount;
    } else if (strcmp(action, "transfer_b_to_a") == 0) {
        if (!(amount > 0 && balance_b >= amount)) {
            fprintf(stderr, "bank_adapter: precondition failed for %s\n", action);
            return;
        }
        balance_b -= amount;
        balance_a += amount;
    } else {
        fprintf(stderr, "bank_adapter: unknown action %s\n", action);
    }
}

void veri_snapshot(char *out, size_t out_len) {
    snprintf(out, out_len, "{\"balance_a\": %lld, \"balance_b\": %lld}", balance_a, balance_b);
}
