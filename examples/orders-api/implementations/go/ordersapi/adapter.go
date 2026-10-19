package ordersapi

import (
	"fmt"
	"sort"
	"strconv"
)

// What the bounded model covers.  The ids are stringified, because that is
// how the trace format and the generated test key the maps.
var (
	modelProducts = []string{"0", "1"}
	modelOrders   = []string{"0", "1"}
	modelKeys     = []string{"k1", "k2"}
)

// ModelOverflow is the error the adapter raises when the implementation goes
// past the bounds of the bounded model.
//
// The model is bounded (two products, two orders, two idempotency keys) while
// the implementation is not: a real database holds as many orders as you ask
// for.  When the implementation goes past the bounds of the model, the adapter
// says so instead of quietly hiding the extra rows, because a check that
// silently drops state is not a check.
type ModelOverflow struct {
	// Message says what went out of bounds.
	Message string
}

// Error implements the error interface.
func (e *ModelOverflow) Error() string { return e.Message }

// VeriSpecAdapter is the Go adapter contract:
//
//	Reset()
//	Apply(action string, params map[string]any)
//	Snapshot() map[string]any
//
// The snapshot contains exactly the spec's state variables, and nothing else.
type VeriSpecAdapter interface {
	Reset()
	Apply(action string, params map[string]any)
	Snapshot() map[string]any
}

// OrdersApiAdapter exposes the orders service to VeriSpec.
//
// The adapter is deliberately thin.  It calls the same service methods the
// HTTP routes call, and it reads the state back from the database.  It does
// not reimplement a single business rule: if it did, the conformance test
// would be testing the adapter instead of the API.
//
// It implements the Go adapter contract:
//
//	Reset()
//	Apply(action string, params map[string]any)
//	Snapshot() map[string]any
//
// The zero value is usable: the service is created on demand, so the
// generated test can build the adapter with &OrdersApiAdapter{}.
type OrdersApiAdapter struct {
	service *Service
}

// NewOrdersApiAdapter returns an adapter over a fresh in-memory database.
func NewOrdersApiAdapter() *OrdersApiAdapter {
	return &OrdersApiAdapter{}
}

// NewOrdersApiAdapterWith returns an adapter over an existing service.
func NewOrdersApiAdapterWith(service *Service) *OrdersApiAdapter {
	return &OrdersApiAdapter{service: service}
}

// svc returns the service, creating it on first use.
func (a *OrdersApiAdapter) svc() *Service {
	if a.service == nil {
		service, err := NewService(":memory:")
		if err != nil {
			panic(err)
		}
		a.service = service
	}
	return a.service
}

// Reset puts the implementation in the initial state of the spec.
func (a *OrdersApiAdapter) Reset() {
	if err := a.svc().Reset(); err != nil {
		panic(err)
	}
}

// Apply applies one action of the spec.
func (a *OrdersApiAdapter) Apply(action string, params map[string]any) {
	switch action {
	case "place_order":
		orderID := paramInt(params, "order_id")
		product := paramInt(params, "product")
		quantity := paramInt(params, "quantity")
		order, err := a.svc().PlaceOrder(product, quantity)
		if err != nil {
			panic(err)
		}
		if order.ID != orderID {
			panic(&ModelOverflow{Message: fmt.Sprintf(
				"the model expects order %d but the service created order %d: the model is out of bounds",
				orderID, order.ID)})
		}
	case "reserve":
		orderID := paramInt(params, "order_id")
		product := paramInt(params, "product")
		order, err := a.svc().GetOrder(orderID)
		if err != nil {
			panic(err)
		}
		if order.ProductID != product {
			panic(&ModelOverflow{Message: fmt.Sprintf(
				"order %d is for product %d, not %d", orderID, order.ProductID, product)})
		}
		if _, err := a.svc().Reserve(orderID); err != nil {
			panic(err)
		}
	case "pay":
		orderID := paramInt(params, "order_id")
		key := paramString(params, "key")
		if _, err := a.svc().Pay(orderID, key); err != nil {
			panic(err)
		}
	case "ship":
		orderID := paramInt(params, "order_id")
		if _, err := a.svc().Ship(orderID); err != nil {
			panic(err)
		}
	case "cancel":
		orderID := paramInt(params, "order_id")
		if _, err := a.svc().Cancel(orderID); err != nil {
			panic(err)
		}
	default:
		panic(fmt.Errorf("unknown action %q", action))
	}
}

// Snapshot returns the current state, with exactly the spec's variables.
//
// The maps are keyed by the stringified id, the JSON representation the trace
// format uses, and the set of paid keys is sorted.
func (a *OrdersApiAdapter) Snapshot() map[string]any {
	products, err := a.svc().ListProducts()
	if err != nil {
		panic(err)
	}
	orders, err := a.svc().ListOrders()
	if err != nil {
		panic(err)
	}
	payments, err := a.svc().ListPayments()
	if err != nil {
		panic(err)
	}
	revenue, err := a.svc().Revenue()
	if err != nil {
		panic(err)
	}
	ordersCreated, err := a.svc().OrdersCreated()
	if err != nil {
		panic(err)
	}

	stock := map[string]any{}
	reserved := map[string]any{}
	productIDs := []string{}
	for _, p := range products {
		key := strconv.Itoa(p.ID)
		productIDs = append(productIDs, key)
		stock[key] = p.Stock
		reserved[key] = p.Reserved
	}
	checkDomain("product", productIDs, modelProducts)

	orderProduct := map[string]any{}
	orderQuantity := map[string]any{}
	orderState := map[string]any{}
	orderAmount := map[string]any{}
	orderIDs := []string{}
	for _, o := range orders {
		key := strconv.Itoa(o.ID)
		orderIDs = append(orderIDs, key)
		orderProduct[key] = o.ProductID
		orderQuantity[key] = o.Quantity
		orderState[key] = o.State
		orderAmount[key] = o.Amount
	}
	checkDomain("order", orderIDs, modelOrders)

	paidKeys := []any{}
	usedKeys := []string{}
	for _, p := range payments {
		usedKeys = append(usedKeys, p.IdempotencyKey)
	}
	checkDomain("idempotency key", usedKeys, modelKeys)
	sort.Strings(usedKeys)
	for _, key := range usedKeys {
		paidKeys = append(paidKeys, key)
	}

	return map[string]any{
		"stock":          stock,
		"reserved":       reserved,
		"order_product":  orderProduct,
		"order_quantity": orderQuantity,
		"order_state":    orderState,
		"order_amount":   orderAmount,
		"paid_keys":      paidKeys,
		"revenue":        revenue,
		"orders_created": ordersCreated,
	}
}

// checkDomain fails loudly when the implementation holds something the
// bounded model does not cover.
func checkDomain(what string, got []string, allowed []string) {
	extra := []string{}
	for _, item := range got {
		if !contains(allowed, item) {
			extra = append(extra, item)
		}
	}
	if len(extra) == 0 {
		return
	}
	sort.Strings(extra)
	panic(&ModelOverflow{Message: fmt.Sprintf(
		"the bounded model does not cover %s %v: the implementation is past the bounds of the spec",
		what, extra)})
}

// contains reports whether the list holds the value.
func contains(list []string, value string) bool {
	for _, item := range list {
		if item == value {
			return true
		}
	}
	return false
}

// paramInt reads an integer parameter.
func paramInt(params map[string]any, name string) int {
	switch v := params[name].(type) {
	case int:
		return v
	case int64:
		return int(v)
	case float64:
		return int(v)
	}
	return 0
}

// paramString reads a string parameter.
func paramString(params map[string]any, name string) string {
	if v, ok := params[name].(string); ok {
		return v
	}
	return ""
}
