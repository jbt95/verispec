package ordersapi

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
)

// errorBody is the body of every error response.
type errorBody struct {
	Detail string `json:"detail"`
}

// NewRouter returns the HTTP layer of the API: a thin shell over Service.
//
// The handlers hold no business logic.  They decode the request, call one
// service method, and turn the result into a status code and a JSON body.
func NewRouter(service *Service) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/products", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			methodNotAllowed(w, http.MethodGet)
			return
		}
		products, err := service.ListProducts()
		if err != nil {
			internalError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, products)
	})
	mux.HandleFunc("/orders", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			orders, err := service.ListOrders()
			if err != nil {
				internalError(w, err)
				return
			}
			writeJSON(w, http.StatusOK, orders)
		case http.MethodPost:
			createOrder(w, r, service)
		default:
			methodNotAllowed(w, http.MethodGet, http.MethodPost)
		}
	})
	mux.HandleFunc("/orders/", func(w http.ResponseWriter, r *http.Request) {
		orderRoute(w, r, service)
	})
	return mux
}

// orderRoute dispatches /orders/{id} and /orders/{id}/{action}.
func orderRoute(w http.ResponseWriter, r *http.Request, service *Service) {
	rest := strings.TrimPrefix(r.URL.Path, "/orders/")
	parts := strings.Split(rest, "/")
	if len(parts) > 2 || parts[0] == "" {
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	orderID, err := strconv.Atoi(parts[0])
	if err != nil {
		writeError(w, http.StatusUnprocessableEntity, "order id must be an integer")
		return
	}
	if len(parts) == 1 {
		if r.Method != http.MethodGet {
			methodNotAllowed(w, http.MethodGet)
			return
		}
		order, err := service.GetOrder(orderID)
		if err != nil {
			writeDomainError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, order)
		return
	}
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	switch parts[1] {
	case "reserve":
		order, err := service.Reserve(orderID)
		if err != nil {
			writeDomainError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, order)
	case "pay":
		payOrder(w, r, service, orderID)
	case "ship":
		order, err := service.Ship(orderID)
		if err != nil {
			writeDomainError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, order)
	case "cancel":
		order, err := service.Cancel(orderID)
		if err != nil {
			writeDomainError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, order)
	default:
		writeError(w, http.StatusNotFound, "not found")
	}
}

// placeOrderBody is the body of POST /orders.
type placeOrderBody struct {
	ProductID *json.Number `json:"product_id"`
	Quantity  *json.Number `json:"quantity"`
}

// createOrder handles POST /orders.
func createOrder(w http.ResponseWriter, r *http.Request, service *Service) {
	var body placeOrderBody
	if err := decodeBody(r, &body); err != nil {
		writeError(w, http.StatusUnprocessableEntity, err.Error())
		return
	}
	if body.ProductID == nil {
		writeError(w, http.StatusUnprocessableEntity, "product_id is required")
		return
	}
	if body.Quantity == nil {
		writeError(w, http.StatusUnprocessableEntity, "quantity is required")
		return
	}
	productID, err := body.ProductID.Int64()
	if err != nil {
		writeError(w, http.StatusUnprocessableEntity, "product_id must be an integer")
		return
	}
	quantity, err := body.Quantity.Int64()
	if err != nil {
		writeError(w, http.StatusUnprocessableEntity, "quantity must be an integer")
		return
	}
	order, err := service.PlaceOrder(int(productID), int(quantity))
	if err != nil {
		writeDomainError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, order)
}

// payOrderBody is the body of POST /orders/{id}/pay.
type payOrderBody struct {
	IdempotencyKey *string `json:"idempotency_key"`
}

// payOrder handles POST /orders/{id}/pay.
func payOrder(w http.ResponseWriter, r *http.Request, service *Service, orderID int) {
	var body payOrderBody
	if err := decodeBody(r, &body); err != nil {
		writeError(w, http.StatusUnprocessableEntity, err.Error())
		return
	}
	if body.IdempotencyKey == nil {
		writeError(w, http.StatusUnprocessableEntity, "idempotency_key is required")
		return
	}
	if *body.IdempotencyKey == "" {
		writeError(w, http.StatusUnprocessableEntity, "idempotency_key must not be empty")
		return
	}
	order, err := service.Pay(orderID, *body.IdempotencyKey)
	if err != nil {
		writeDomainError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, order)
}

// decodeBody decodes a JSON request body.
func decodeBody(r *http.Request, target any) error {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		return errors.New("the request body could not be read")
	}
	if len(strings.TrimSpace(string(body))) == 0 {
		return errors.New("the request body is required")
	}
	dec := json.NewDecoder(strings.NewReader(string(body)))
	dec.UseNumber()
	if err := dec.Decode(target); err != nil {
		return errors.New("the request body is not valid JSON")
	}
	return nil
}

// writeJSON writes a JSON response.
func writeJSON(w http.ResponseWriter, status int, body any) {
	data, err := json.Marshal(body)
	if err != nil {
		internalError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(data)
}

// writeError writes an error response with the given status.
func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, errorBody{Detail: message})
}

// writeDomainError turns a business rule violation into a response.
func writeDomainError(w http.ResponseWriter, err error) {
	var domain *DomainError
	if errors.As(err, &domain) {
		writeError(w, domain.Status, domain.Message)
		return
	}
	internalError(w, err)
}

// internalError reports a failure of the tool itself.
func internalError(w http.ResponseWriter, err error) {
	writeError(w, http.StatusInternalServerError, err.Error())
}

// methodNotAllowed reports a request with the wrong method.
func methodNotAllowed(w http.ResponseWriter, allowed ...string) {
	w.Header().Set("Allow", strings.Join(allowed, ", "))
	writeError(w, http.StatusMethodNotAllowed, "method not allowed")
}
