/* The trace recorder of the C SDK writes the canonical VeriSpec trace:
 * a JSON list of {"action", "params", "state"}, with the initial state
 * first.  The file is the language agnostic bridge: `verispec check-trace`
 * replays it against the spec. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "verispec.h"

static int fail(const char *message) {
    fprintf(stderr, "verispec trace: %s\n", message);
    return 1;
}

int main(void) {
    veri_trace *trace = veri_trace_new();
    char *text;
    veri_value *parsed;
    const veri_value *step;
    const veri_value *params;

    if (trace == NULL) {
        return fail("out of memory");
    }
    if (!veri_trace_record(trace, "init", "{}", "{\"balance_a\": 50, \"balance_b\": 50}")) {
        veri_trace_free(trace);
        return fail("the init step was not recorded");
    }
    if (!veri_trace_record(trace, "transfer_a_to_b", "{\"amount\": 20}",
                           "{\"balance_a\": 30, \"balance_b\": 70}")) {
        veri_trace_free(trace);
        return fail("the transfer step was not recorded");
    }
    if (veri_trace_record(trace, "broken", "not json", "{}")) {
        veri_trace_free(trace);
        return fail("invalid JSON must be rejected");
    }

    text = veri_trace_to_json(trace);
    if (text == NULL) {
        veri_trace_free(trace);
        return fail("the trace was not written");
    }
    parsed = veri_json_parse(text);
    if (parsed == NULL) {
        free(text);
        veri_trace_free(trace);
        return fail("the trace is not valid JSON");
    }
    if (veri_array_length(parsed) != 2) {
        free(text);
        veri_value_free(parsed);
        veri_trace_free(trace);
        return fail("the trace must hold two steps");
    }
    step = veri_array_get(parsed, 0);
    if (step == NULL || veri_object_get(step, "action") == NULL) {
        free(text);
        veri_value_free(parsed);
        veri_trace_free(trace);
        return fail("the first step must name an action");
    }
    if (strcmp(veri_object_get(step, "action")->s, "init") != 0) {
        free(text);
        veri_value_free(parsed);
        veri_trace_free(trace);
        return fail("the first step must be the init step");
    }
    params = veri_object_get(step, "params");
    if (params == NULL || params->kind != VERI_OBJ || params->count != 0) {
        free(text);
        veri_value_free(parsed);
        veri_trace_free(trace);
        return fail("the init step must have empty parameters");
    }
    step = veri_array_get(parsed, 1);
    params = veri_object_get(step, "params");
    if (params == NULL || veri_object_get(params, "amount") == NULL) {
        free(text);
        veri_value_free(parsed);
        veri_trace_free(trace);
        return fail("the second step must carry the parameters");
    }

    free(text);
    veri_value_free(parsed);
    veri_trace_free(trace);
    printf("verispec trace: OK\n");
    return 0;
}
