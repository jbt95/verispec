// Command ordersapi runs the orders API.
//
//	go run ./cmd/ordersapi --addr :8080 --db orders.db
package main

import (
	"context"
	"errors"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"verispec.dev/examples/orders-api/ordersapi"
)

func main() {
	addr := flag.String("addr", ":8080", "the address to listen on")
	dbPath := flag.String("db", ":memory:", "the SQLite database file")
	flag.Parse()

	service, err := ordersapi.NewService(*dbPath)
	if err != nil {
		log.Fatalf("open the database: %v", err)
	}
	defer service.Close()

	server := &http.Server{
		Addr:              *addr,
		Handler:           ordersapi.NewRouter(service),
		ReadHeaderTimeout: 10 * time.Second,
	}

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-stop
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(ctx)
	}()

	log.Printf("the orders API listens on %s", *addr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatalf("the server stopped: %v", err)
	}
}
