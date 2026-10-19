# The Go adapter conformance suite.
#
#   make test    run the generated conformance test and the adapter suite
#   make gen     regenerate the conformance test from the spec
#   make fmt     gofmt the sources

GO ?= go
PYTHON ?= python3
SPEC ?= ../../examples/bank.yaml
ADAPTER ?= verispec.dev/adapters/go/bank.BankAdapter
PACKAGE ?= bank_test

.PHONY: test gen fmt

test:
	$(GO) test ./...

gen:
	$(PYTHON) -m verispec.cli gen-tests $(SPEC) --lang go \
		--package $(PACKAGE) --adapter $(ADAPTER) \
		-o bank/bank_verispec_test.go

fmt:
	gofmt -w bank/*.go
