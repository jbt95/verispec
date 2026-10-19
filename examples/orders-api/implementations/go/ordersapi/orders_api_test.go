// The tests for the Go orders API.
//
// Four layers, each with a different question:
//
//  1. The API tests (TestTheApi): does the API enforce the business rules?
//     These are ordinary HTTP tests, over the real handlers.
//  2. The conformance test (TestVeriSpecConformance, generated): does the
//     implementation obey the *spec*?  It is driven against the adapter that
//     calls the real service.
//  3. The teeth test (TestTheSuiteHasTeeth): does the conformance test fail
//     on the broken implementation?  A suite that stays green on a
//     deliberately broken adapter proves nothing.
//  4. The trace test (trace_test.go): does a recorded session conform to the
//     spec, and is the committed trace up to date?
package ordersapi_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"verispec.dev/examples/orders-api/ordersapi"
)

// newServer returns a server over a fresh in-memory database.
func newServer(t *testing.T) *httptest.Server {
	t.Helper()
	service, err := ordersapi.NewService(":memory:")
	if err != nil {
		t.Fatalf("open the database: %v", err)
	}
	t.Cleanup(func() { _ = service.Close() })
	server := httptest.NewServer(ordersapi.NewRouter(service))
	t.Cleanup(server.Close)
	return server
}

// request sends a request to the server and returns the status and the body.
func request(t *testing.T, server *httptest.Server, method string, path string, body any) (int, []byte) {
	t.Helper()
	var payload []byte
	if body != nil {
		var err error
		payload, err = json.Marshal(body)
		if err != nil {
			t.Fatalf("encode the body: %v", err)
		}
	}
	req, err := http.NewRequest(method, server.URL+path, bytes.NewReader(payload))
	if err != nil {
		t.Fatalf("build the request: %v", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := server.Client().Do(req)
	if err != nil {
		t.Fatalf("send the request: %v", err)
	}
	defer resp.Body.Close()
	var buf bytes.Buffer
	if _, err := buf.ReadFrom(resp.Body); err != nil {
		t.Fatalf("read the response: %v", err)
	}
	return resp.StatusCode, buf.Bytes()
}

// decode reads a JSON body into a map.
func decode(t *testing.T, body []byte) map[string]any {
	t.Helper()
	out := map[string]any{}
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatalf("decode the body %s: %v", body, err)
	}
	return out
}

// detail is the message of an error response.
func detail(t *testing.T, body []byte) string {
	t.Helper()
	return decode(t, body)["detail"].(string)
}

// --------------------------------------------------------------------------
// 1. the API
// --------------------------------------------------------------------------

func TestTheApi(t *testing.T) {
	t.Run("the catalogue is seeded", func(t *testing.T) {
		server := newServer(t)
		status, body := request(t, server, http.MethodGet, "/products", nil)
		if status != http.StatusOK {
			t.Fatalf("expected 200, got %d: %s", status, body)
		}
		var products []map[string]any
		if err := json.Unmarshal(body, &products); err != nil {
			t.Fatalf("decode the catalogue %s: %v", body, err)
		}
		if len(products) != 2 {
			t.Fatalf("expected 2 products, got %d", len(products))
		}
		if products[0]["id"] != float64(0) || products[1]["id"] != float64(1) {
			t.Fatalf("unexpected product ids: %v", products)
		}
		if products[0]["stock"] != float64(5) {
			t.Fatalf("expected 5 units in stock, got %v", products[0]["stock"])
		}
		if products[1]["stock"] != float64(3) {
			t.Fatalf("expected 3 units in stock, got %v", products[1]["stock"])
		}
	})

	t.Run("placing an order", func(t *testing.T) {
		server := newServer(t)
		status, body := request(t, server, http.MethodPost, "/orders",
			map[string]any{"product_id": 0, "quantity": 2})
		if status != http.StatusCreated {
			t.Fatalf("expected 201, got %d: %s", status, body)
		}
		order := decode(t, body)
		if order["state"] != "pending" {
			t.Fatalf("expected a pending order, got %v", order["state"])
		}
		if order["amount"] != float64(20) {
			t.Fatalf("expected an amount of 20, got %v", order["amount"])
		}
		if order["quantity"] != float64(2) {
			t.Fatalf("expected a quantity of 2, got %v", order["quantity"])
		}
	})

	t.Run("an unknown product is a 404", func(t *testing.T) {
		server := newServer(t)
		status, body := request(t, server, http.MethodPost, "/orders",
			map[string]any{"product_id": 9, "quantity": 1})
		if status != http.StatusNotFound {
			t.Fatalf("expected 404, got %d: %s", status, body)
		}
	})

	t.Run("a bad quantity is a 422", func(t *testing.T) {
		server := newServer(t)
		for _, quantity := range []int{0, 99} {
			status, body := request(t, server, http.MethodPost, "/orders",
				map[string]any{"product_id": 0, "quantity": quantity})
			if status != http.StatusUnprocessableEntity {
				t.Fatalf("expected 422 for the quantity %d, got %d: %s", quantity, status, body)
			}
		}
	})

	t.Run("reserving more than the stock is a 409", func(t *testing.T) {
		server := newServer(t)
		// the first order takes all the stock of the product
		request(t, server, http.MethodPost, "/orders", map[string]any{"product_id": 0, "quantity": 5})
		request(t, server, http.MethodPost, "/orders/0/reserve", nil)
		// the second order cannot be reserved: there is nothing left
		request(t, server, http.MethodPost, "/orders", map[string]any{"product_id": 0, "quantity": 1})
		status, body := request(t, server, http.MethodPost, "/orders/1/reserve", nil)
		if status != http.StatusConflict {
			t.Fatalf("expected 409, got %d: %s", status, body)
		}
		if got := detail(t, body); got != "not enough stock for product 0: 0 available, 1 requested" {
			t.Fatalf("unexpected detail: %q", got)
		}
	})

	t.Run("reserving twice is a 409", func(t *testing.T) {
		server := newServer(t)
		request(t, server, http.MethodPost, "/orders", map[string]any{"product_id": 0, "quantity": 1})
		if status, body := request(t, server, http.MethodPost, "/orders/0/reserve", nil); status != http.StatusOK {
			t.Fatalf("expected 200, got %d: %s", status, body)
		}
		if status, body := request(t, server, http.MethodPost, "/orders/0/reserve", nil); status != http.StatusConflict {
			t.Fatalf("expected 409, got %d: %s", status, body)
		}
	})

	t.Run("paying twice is a 409", func(t *testing.T) {
		server := newServer(t)
		request(t, server, http.MethodPost, "/orders", map[string]any{"product_id": 0, "quantity": 1})
		request(t, server, http.MethodPost, "/orders/0/reserve", nil)
		status, body := request(t, server, http.MethodPost, "/orders/0/pay",
			map[string]any{"idempotency_key": "k1"})
		if status != http.StatusOK {
			t.Fatalf("expected 200, got %d: %s", status, body)
		}
		status, body = request(t, server, http.MethodPost, "/orders/0/pay",
			map[string]any{"idempotency_key": "k2"})
		if status != http.StatusConflict {
			t.Fatalf("expected 409, got %d: %s", status, body)
		}
	})

	t.Run("reusing an idempotency key is a 409", func(t *testing.T) {
		server := newServer(t)
		request(t, server, http.MethodPost, "/orders", map[string]any{"product_id": 0, "quantity": 1})
		request(t, server, http.MethodPost, "/orders", map[string]any{"product_id": 1, "quantity": 1})
		request(t, server, http.MethodPost, "/orders/0/reserve", nil)
		request(t, server, http.MethodPost, "/orders/1/reserve", nil)
		status, body := request(t, server, http.MethodPost, "/orders/0/pay",
			map[string]any{"idempotency_key": "k1"})
		if status != http.StatusOK {
			t.Fatalf("expected 200, got %d: %s", status, body)
		}
		status, body = request(t, server, http.MethodPost, "/orders/1/pay",
			map[string]any{"idempotency_key": "k1"})
		if status != http.StatusConflict {
			t.Fatalf("expected 409, got %d: %s", status, body)
		}
		if got := detail(t, body); got != "idempotency key k1 was already used" {
			t.Fatalf("unexpected detail: %q", got)
		}
	})

	t.Run("shipping without paying is a 409", func(t *testing.T) {
		server := newServer(t)
		request(t, server, http.MethodPost, "/orders", map[string]any{"product_id": 0, "quantity": 1})
		status, body := request(t, server, http.MethodPost, "/orders/0/ship", nil)
		if status != http.StatusConflict {
			t.Fatalf("expected 409, got %d: %s", status, body)
		}
	})

	t.Run("shipping consumes the stock", func(t *testing.T) {
		server := newServer(t)
		request(t, server, http.MethodPost, "/orders", map[string]any{"product_id": 0, "quantity": 2})
		request(t, server, http.MethodPost, "/orders/0/reserve", nil)
		request(t, server, http.MethodPost, "/orders/0/pay", map[string]any{"idempotency_key": "k1"})
		status, body := request(t, server, http.MethodPost, "/orders/0/ship", nil)
		if status != http.StatusOK {
			t.Fatalf("expected 200, got %d: %s", status, body)
		}
		_, body = request(t, server, http.MethodGet, "/products", nil)
		var products []map[string]any
		if err := json.Unmarshal(body, &products); err != nil {
			t.Fatalf("decode the catalogue %s: %v", body, err)
		}
		if products[0]["stock"] != float64(3) { // 5 - 2
			t.Fatalf("expected 3 units in stock, got %v", products[0]["stock"])
		}
		if products[0]["reserved"] != float64(0) {
			t.Fatalf("expected no reservation, got %v", products[0]["reserved"])
		}
	})

	t.Run("cancelling a paid order is a 409", func(t *testing.T) {
		server := newServer(t)
		request(t, server, http.MethodPost, "/orders", map[string]any{"product_id": 0, "quantity": 1})
		request(t, server, http.MethodPost, "/orders/0/reserve", nil)
		request(t, server, http.MethodPost, "/orders/0/pay", map[string]any{"idempotency_key": "k1"})
		status, body := request(t, server, http.MethodPost, "/orders/0/cancel", nil)
		if status != http.StatusConflict {
			t.Fatalf("expected 409, got %d: %s", status, body)
		}
	})

	t.Run("cancelling releases the reservation", func(t *testing.T) {
		server := newServer(t)
		request(t, server, http.MethodPost, "/orders", map[string]any{"product_id": 0, "quantity": 2})
		request(t, server, http.MethodPost, "/orders/0/reserve", nil)
		_, body := request(t, server, http.MethodGet, "/products", nil)
		if reserved := productAt(t, body, 0)["reserved"]; reserved != float64(2) {
			t.Fatalf("expected 2 units reserved, got %v", reserved)
		}
		status, body := request(t, server, http.MethodPost, "/orders/0/cancel", nil)
		if status != http.StatusOK {
			t.Fatalf("expected 200, got %d: %s", status, body)
		}
		_, body = request(t, server, http.MethodGet, "/products", nil)
		product := productAt(t, body, 0)
		if product["reserved"] != float64(0) {
			t.Fatalf("expected the reservation to be released, got %v", product["reserved"])
		}
		if product["stock"] != float64(5) {
			t.Fatalf("expected the stock to be untouched, got %v", product["stock"])
		}
	})

	t.Run("an unknown order is a 404", func(t *testing.T) {
		server := newServer(t)
		status, body := request(t, server, http.MethodGet, "/orders/7", nil)
		if status != http.StatusNotFound {
			t.Fatalf("expected 404, got %d: %s", status, body)
		}
		if got := detail(t, body); got != "order 7 does not exist" {
			t.Fatalf("unexpected detail: %q", got)
		}
	})
}

// productAt returns the catalogue entry with the given id.
func productAt(t *testing.T, body []byte, id int) map[string]any {
	t.Helper()
	var products []map[string]any
	if err := json.Unmarshal(body, &products); err != nil {
		t.Fatalf("decode the catalogue %s: %v", body, err)
	}
	for _, p := range products {
		if p["id"] == float64(id) {
			return p
		}
	}
	t.Fatalf("the catalogue has no product %d", id)
	return nil
}

// --------------------------------------------------------------------------
// 2. the conformance test
// --------------------------------------------------------------------------

// TestTheSuiteHasTeeth proves the conformance suite has teeth: the generated
// test must FAIL on the implementation that leaks the reservation.
//
// The generated test drives the adapter with actions the spec *allows*, so it
// catches an implementation that computes the wrong state.  It cannot catch
// an implementation that accepts an action the spec forbids: the generated
// test never takes that path.  The trace path covers that case, and the
// API's own tests cover it as well.
func TestTheSuiteHasTeeth(t *testing.T) {
	old := veriMakeAdapter
	veriMakeAdapter = func() veriAdapter { return &ordersapi.BuggyOrdersApiAdapter{} }
	defer func() { veriMakeAdapter = old }()

	err := veriRunConformance()
	if err == nil {
		t.Fatal("the generated conformance test passed on the broken adapter: the suite proves nothing")
	}
	// The broken adapter has to be caught for the right reason: the
	// reservation it forgets to release.
	if !strings.Contains(err.Error(), "reserved") {
		t.Fatalf("the broken adapter was caught for the wrong reason: %v", err)
	}
}
