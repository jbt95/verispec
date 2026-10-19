/* A deliberately wrong adapter for the `Bank` spec.
 *
 * It loses one unit on every transfer, so the total is not conserved.  The
 * conformance suite asserts that the generated test *fails* on this adapter:
 * a suite that stays green on a broken adapter is not testing anything. */

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
        balance_a -= amount;
        balance_b += amount - 1; /* the bug: one unit is lost */
    } else if (strcmp(action, "transfer_b_to_a") == 0) {
        balance_b -= amount;
        balance_a += amount - 1; /* the bug: one unit is lost */
    } else {
        fprintf(stderr, "bank_adapter_buggy: unknown action %s\n", action);
    }
}

void veri_snapshot(char *out, size_t out_len) {
    snprintf(out, out_len, "{\"balance_a\": %lld, \"balance_b\": %lld}", balance_a, balance_b);
}
