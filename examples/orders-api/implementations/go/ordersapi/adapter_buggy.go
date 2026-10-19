package ordersapi

import (
	"context"
	"database/sql"
	"fmt"
)

// --------------------------------------------------------------------------
// the deliberately broken implementations
// --------------------------------------------------------------------------

// BuggyOrdersApiAdapter is the deliberately broken implementation.
//
// It cancels an order without releasing the reserved stock, so the warehouse
// keeps stock that was never sold.  The conformance test must FAIL on it: a
// suite that stays green on this adapter is not testing anything.
type BuggyOrdersApiAdapter struct {
	OrdersApiAdapter
}

// Apply applies one action of the spec.
func (a *BuggyOrdersApiAdapter) Apply(action string, params map[string]any) {
	if action == "cancel" {
		orderID := paramInt(params, "order_id")
		order, err := a.svc().GetOrder(orderID)
		if err != nil {
			panic(err)
		}
		// BUG: the reservation is not released.
		if err := a.svc().cancelWithoutReleasing(order); err != nil {
			panic(err)
		}
		return
	}
	a.OrdersApiAdapter.Apply(action, params)
}

// OversellingOrdersApiAdapter is the implementation that oversells.
//
// It reserves stock without checking that the stock is there, the classic bug
// of a check that is not atomic with the write.  The conformance test cannot
// catch it: the generated test only takes actions the spec allows.  The
// *trace* path can, because a trace records the states the implementation
// really went through, and the recorded state violates `no_overselling`.
type OversellingOrdersApiAdapter struct {
	OrdersApiAdapter
}

// Apply applies one action of the spec.
func (a *OversellingOrdersApiAdapter) Apply(action string, params map[string]any) {
	if action == "reserve" {
		orderID := paramInt(params, "order_id")
		order, err := a.svc().GetOrder(orderID)
		if err != nil {
			panic(err)
		}
		// BUG: no check that there is enough stock.
		if err := a.svc().reserveWithoutChecking(order); err != nil {
			panic(err)
		}
		return
	}
	a.OrdersApiAdapter.Apply(action, params)
}

// cancelWithoutReleasing cancels an order and forgets the reservation.
//
// BUG: the stock stays reserved, so the warehouse holds stock that was never
// sold.  Only the broken adapter calls this.
func (s *Service) cancelWithoutReleasing(order Order) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	ctx := context.Background()
	return s.transaction(ctx, func(conn *sql.Conn) error {
		if _, err := conn.ExecContext(ctx,
			"UPDATE orders SET state = 'cancelled' WHERE id = ?", order.ID); err != nil {
			return fmt.Errorf("move the order to cancelled: %w", err)
		}
		return nil
	})
}

// reserveWithoutChecking reserves stock without looking at the shelf.
//
// BUG: the reservation can exceed the stock.  Only the broken adapter calls
// this.
func (s *Service) reserveWithoutChecking(order Order) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	ctx := context.Background()
	return s.transaction(ctx, func(conn *sql.Conn) error {
		if _, err := conn.ExecContext(ctx,
			"UPDATE products SET reserved = reserved + ? WHERE id = ?",
			order.Quantity, order.ProductID); err != nil {
			return fmt.Errorf("reserve the stock: %w", err)
		}
		if _, err := conn.ExecContext(ctx,
			"UPDATE orders SET state = 'reserved' WHERE id = ?", order.ID); err != nil {
			return fmt.Errorf("move the order to reserved: %w", err)
		}
		return nil
	})
}
