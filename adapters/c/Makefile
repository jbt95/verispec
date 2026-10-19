# The VeriSpec conformance suite for the C adapter SDK.
#
#   make test     build and run the suite
#   make          build the two binaries
#   make clean    remove the build directory
#
# The suite builds the *generated* conformance test (tests/test_bank.c,
# produced by `verispec gen-tests examples/bank.yaml --lang c`) twice:
#
#   * against the correct adapter, which must pass, and
#   * against the deliberately broken adapter, which must fail.
#
# A suite that stays green on the broken adapter is not testing anything.

CC ?= cc
CFLAGS ?= -std=c11 -Wall -Wextra -O1
INCLUDES := -Iinclude
BUILD := build

RUNTIME := src/verispec.c
TEST := tests/test_bank.c

.PHONY: all test clean generate

all: $(BUILD)/bank_ok $(BUILD)/bank_buggy $(BUILD)/trace_test

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/bank_ok: $(TEST) tests/bank_adapter.c $(RUNTIME) include/verispec.h | $(BUILD)
	$(CC) $(CFLAGS) $(INCLUDES) -o $@ $(TEST) tests/bank_adapter.c $(RUNTIME)

$(BUILD)/bank_buggy: $(TEST) tests/bank_adapter_buggy.c $(RUNTIME) include/verispec.h | $(BUILD)
	$(CC) $(CFLAGS) $(INCLUDES) -o $@ $(TEST) tests/bank_adapter_buggy.c $(RUNTIME)

$(BUILD)/trace_test: tests/test_trace.c $(RUNTIME) include/verispec.h | $(BUILD)
	$(CC) $(CFLAGS) $(INCLUDES) -o $@ tests/test_trace.c $(RUNTIME)

test: all
	@echo "== the correct adapter must pass =="
	@./$(BUILD)/bank_ok
	@echo "== the broken adapter must fail =="
	@if ./$(BUILD)/bank_buggy > /dev/null 2>&1; then \
		echo "FAIL: the broken adapter was not detected"; \
		exit 1; \
	else \
		echo "ok: the broken adapter was detected"; \
	fi
	@echo "== the trace recorder must write the canonical trace =="
	@./$(BUILD)/trace_test
	@echo "verispec C conformance suite: OK"

# Regenerate the conformance test from the spec.  The VeriSpec CLI must be on
# the PATH (or VERISPEC points at it).
generate:
	verispec gen-tests ../../examples/bank.yaml --lang c -o $(TEST)

clean:
	rm -rf $(BUILD)
