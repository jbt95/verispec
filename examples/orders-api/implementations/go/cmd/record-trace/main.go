// Command record-trace records the canonical session of the orders API and
// writes it in the canonical trace format.
//
//	go run ./cmd/record-trace -o ../../../traces/orders-session-go.json
//
// The trace it writes is the one the conformance tests compare with, and the
// one `verispec check-trace` replays against the spec.
package main

import (
	"flag"
	"log"

	"verispec.dev/examples/orders-api/ordersapi"
)

func main() {
	output := flag.String("o", "", "the file to write the trace to")
	flag.Parse()
	if *output == "" {
		log.Fatal("usage: record-trace -o <file>")
	}

	recorder, err := ordersapi.RecordSession()
	if err != nil {
		log.Fatalf("record the session: %v", err)
	}
	if err := recorder.Write(*output); err != nil {
		log.Fatalf("write the trace: %v", err)
	}
	log.Printf("wrote %d steps to %s", len(recorder.Steps()), *output)
}
