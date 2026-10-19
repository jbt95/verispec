// Package ordersapi is the Go implementation of the orders API whose
// contract is `orders.verispec.yaml`.
//
// The stack is deliberately ordinary:
//
//   - net/http for the HTTP layer,
//   - modernc.org/sqlite (a pure Go SQLite, no cgo) for the database,
//   - a service type with the business rules.
//
// The HTTP layer holds no logic of its own: every route is a shell over
// Service.  That is what makes the VeriSpec adapter honest: it calls the
// same service methods the routes call, and it reads the state back from the
// database.  If the adapter reimplemented the rules, the conformance test
// would be testing the adapter.
//
// The lifecycle of an order, and the rule each step enforces:
//
//	place_order  the order exists, with a positive quantity
//	reserve      the stock is available      (no overselling)
//	pay          the payment key is unused  (no double payment)
//	ship         the order was paid
//	cancel       the order was not paid yet
package ordersapi

import (
	"context"
	"database/sql"
	"fmt"
	"sync"

	_ "modernc.org/sqlite" // the pure Go SQLite driver
)

// UnitPrice is the price of one unit, in cents.
const UnitPrice = 10

// MaxQuantity is the largest quantity a single order may ask for.
const MaxQuantity = 10

// seedProducts is the catalogue the API starts with.
var seedProducts = []Product{
	{ID: 0, Name: "widget", Stock: 5},
	{ID: 1, Name: "gadget", Stock: 3},
}

// The status codes a domain error maps to.  They are plain numbers so that
// the service does not have to know about HTTP.
const (
	StatusNotFound = 404
	StatusConflict = 409
	StatusInvalid  = 422
)

// Product is a catalogue entry.
type Product struct {
	ID       int    `json:"id"`
	Name     string `json:"name"`
	Stock    int    `json:"stock"`
	Reserved int    `json:"reserved"`
}

// Order is one order of the API.
type Order struct {
	ID        int    `json:"id"`
	ProductID int    `json:"product_id"`
	Quantity  int    `json:"quantity"`
	State     string `json:"state"`
	Amount    int    `json:"amount"`
}

// Payment is one applied payment, keyed by the client's idempotency key.
type Payment struct {
	IdempotencyKey string
	OrderID        int
	Amount         int
}

// DomainError is a business rule violation.  The HTTP layer turns it into a
// status code and a JSON body.
type DomainError struct {
	// Status is the HTTP status code the error maps to.
	Status int
	// Message is the human readable reason.
	Message string
}

// Error implements the error interface.
func (e *DomainError) Error() string { return e.Message }

// notFound reports a missing entity (HTTP 404).
func notFound(format string, args ...any) *DomainError {
	return &DomainError{Status: StatusNotFound, Message: fmt.Sprintf(format, args...)}
}

// conflict reports a request that clashes with the current state (HTTP 409).
func conflict(format string, args ...any) *DomainError {
	return &DomainError{Status: StatusConflict, Message: fmt.Sprintf(format, args...)}
}

// invalid reports a malformed request (HTTP 422).
func invalid(format string, args ...any) *DomainError {
	return &DomainError{Status: StatusInvalid, Message: fmt.Sprintf(format, args...)}
}

// schema is the database schema, the same one the Python implementation uses.
const schema = `
CREATE TABLE products (
    id       INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL,
    stock    INTEGER NOT NULL,
    reserved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE orders (
    id         INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity   INTEGER NOT NULL,
    state      TEXT    NOT NULL,
    amount     INTEGER NOT NULL
);
CREATE TABLE payments (
    idempotency_key TEXT    PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(id),
    amount          INTEGER NOT NULL
);
CREATE TABLE counters (
    name  TEXT    PRIMARY KEY,
    value INTEGER NOT NULL
);
`

// Service holds the business rules, over a SQLite database.
//
// Every method either succeeds or returns a *DomainError.  Nothing here knows
// about HTTP, which is what lets the VeriSpec adapter reuse it.
//
// The service owns a single database connection and serialises access to it,
// the way the Python implementation uses one sqlite3 connection.  That also
// keeps an in-memory database alive for the lifetime of the service.
type Service struct {
	mu   sync.Mutex
	db   *sql.DB
	conn *sql.Conn
}

// NewService opens the database at path and seeds the catalogue.
//
// The path is a SQLite path; ":memory:" gives a private in-memory database,
// which is what the tests use.
func NewService(path string) (*Service, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("open the database: %w", err)
	}
	// One connection, so that an in-memory database is a single database.
	db.SetMaxOpenConns(1)
	conn, err := db.Conn(context.Background())
	if err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("connect to the database: %w", err)
	}
	s := &Service{db: db, conn: conn}
	if err := s.reset(); err != nil {
		_ = s.Close()
		return nil, err
	}
	return s, nil
}

// Close releases the database.
func (s *Service) Close() error {
	if s.conn != nil {
		_ = s.conn.Close()
		s.conn = nil
	}
	if s.db != nil {
		return s.db.Close()
	}
	return nil
}

// Reset drops everything and starts from the seed catalogue.
func (s *Service) Reset() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.reset()
}

func (s *Service) reset() error {
	ctx := context.Background()
	for _, table := range []string{"products", "orders", "payments", "counters"} {
		if _, err := s.conn.ExecContext(ctx, "DROP TABLE IF EXISTS "+table); err != nil {
			return fmt.Errorf("drop %s: %w", table, err)
		}
	}
	if _, err := s.conn.ExecContext(ctx, schema); err != nil {
		return fmt.Errorf("create the schema: %w", err)
	}
	for _, p := range seedProducts {
		_, err := s.conn.ExecContext(ctx,
			"INSERT INTO products (id, name, stock, reserved) VALUES (?, ?, ?, 0)",
			p.ID, p.Name, p.Stock)
		if err != nil {
			return fmt.Errorf("seed the catalogue: %w", err)
		}
	}
	_, err := s.conn.ExecContext(ctx, "INSERT INTO counters (name, value) VALUES ('orders', 0)")
	if err != nil {
		return fmt.Errorf("seed the counters: %w", err)
	}
	return nil
}

// transaction runs fn inside an immediate transaction: the write lock is
// taken up front, so the stock check and the reservation happen atomically.
//
// The transaction is rolled back when fn fails, and also when fn panics, so
// the connection is never left with an open transaction.
func (s *Service) transaction(ctx context.Context, fn func(conn *sql.Conn) error) error {
	if _, err := s.conn.ExecContext(ctx, "BEGIN IMMEDIATE"); err != nil {
		return fmt.Errorf("begin the transaction: %w", err)
	}
	committed := false
	defer func() {
		if !committed {
			_, _ = s.conn.ExecContext(ctx, "ROLLBACK")
		}
	}()
	if err := fn(s.conn); err != nil {
		return err
	}
	if _, err := s.conn.ExecContext(ctx, "COMMIT"); err != nil {
		return fmt.Errorf("commit the transaction: %w", err)
	}
	committed = true
	return nil
}

// --------------------------------------------------------------------------
// reads
// --------------------------------------------------------------------------

// ListProducts returns the catalogue, ordered by id.
func (s *Service) ListProducts() ([]Product, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.listProducts(context.Background())
}

func (s *Service) listProducts(ctx context.Context) ([]Product, error) {
	rows, err := s.conn.QueryContext(ctx, "SELECT id, name, stock, reserved FROM products ORDER BY id")
	if err != nil {
		return nil, fmt.Errorf("read the catalogue: %w", err)
	}
	defer rows.Close()
	out := []Product{}
	for rows.Next() {
		var p Product
		if err := rows.Scan(&p.ID, &p.Name, &p.Stock, &p.Reserved); err != nil {
			return nil, fmt.Errorf("read the catalogue: %w", err)
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

// ListOrders returns the orders, ordered by id.
func (s *Service) ListOrders() ([]Order, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.listOrders(context.Background())
}

func (s *Service) listOrders(ctx context.Context) ([]Order, error) {
	rows, err := s.conn.QueryContext(ctx,
		"SELECT id, product_id, quantity, state, amount FROM orders ORDER BY id")
	if err != nil {
		return nil, fmt.Errorf("read the orders: %w", err)
	}
	defer rows.Close()
	out := []Order{}
	for rows.Next() {
		var o Order
		if err := rows.Scan(&o.ID, &o.ProductID, &o.Quantity, &o.State, &o.Amount); err != nil {
			return nil, fmt.Errorf("read the orders: %w", err)
		}
		out = append(out, o)
	}
	return out, rows.Err()
}

// GetOrder returns one order.
func (s *Service) GetOrder(orderID int) (Order, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.getOrder(context.Background(), orderID)
}

func (s *Service) getOrder(ctx context.Context, orderID int) (Order, error) {
	var o Order
	err := s.conn.QueryRowContext(ctx,
		"SELECT id, product_id, quantity, state, amount FROM orders WHERE id = ?", orderID).
		Scan(&o.ID, &o.ProductID, &o.Quantity, &o.State, &o.Amount)
	if err == sql.ErrNoRows {
		return Order{}, notFound("order %d does not exist", orderID)
	}
	if err != nil {
		return Order{}, fmt.Errorf("read the order: %w", err)
	}
	return o, nil
}

// ListPayments returns the applied payments, ordered by order id.
func (s *Service) ListPayments() ([]Payment, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.listPayments(context.Background())
}

func (s *Service) listPayments(ctx context.Context) ([]Payment, error) {
	rows, err := s.conn.QueryContext(ctx,
		"SELECT idempotency_key, order_id, amount FROM payments ORDER BY order_id")
	if err != nil {
		return nil, fmt.Errorf("read the payments: %w", err)
	}
	defer rows.Close()
	out := []Payment{}
	for rows.Next() {
		var p Payment
		if err := rows.Scan(&p.IdempotencyKey, &p.OrderID, &p.Amount); err != nil {
			return nil, fmt.Errorf("read the payments: %w", err)
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

// Revenue is what the paid orders are worth.
func (s *Service) Revenue() (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.revenue(context.Background())
}

func (s *Service) revenue(ctx context.Context) (int, error) {
	var total int
	err := s.conn.QueryRowContext(ctx, "SELECT COALESCE(SUM(amount), 0) FROM payments").Scan(&total)
	if err != nil {
		return 0, fmt.Errorf("read the revenue: %w", err)
	}
	return total, nil
}

// OrdersCreated is the number of orders the API has created.
func (s *Service) OrdersCreated() (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.ordersCreated(context.Background())
}

func (s *Service) ordersCreated(ctx context.Context) (int, error) {
	var value int
	err := s.conn.QueryRowContext(ctx, "SELECT value FROM counters WHERE name = 'orders'").Scan(&value)
	if err == sql.ErrNoRows {
		return 0, nil
	}
	if err != nil {
		return 0, fmt.Errorf("read the order counter: %w", err)
	}
	return value, nil
}

// --------------------------------------------------------------------------
// the business rules
// --------------------------------------------------------------------------

// PlaceOrder creates an order for a product.  The order starts as `pending`.
func (s *Service) PlaceOrder(productID int, quantity int) (Order, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.placeOrder(context.Background(), productID, quantity)
}

func (s *Service) placeOrder(ctx context.Context, productID int, quantity int) (Order, error) {
	if quantity < 1 || quantity > MaxQuantity {
		return Order{}, invalid("quantity must be between 1 and %d", MaxQuantity)
	}
	product, err := s.getProduct(ctx, productID)
	if err != nil {
		return Order{}, err
	}
	if product == nil {
		return Order{}, notFound("product %d does not exist", productID)
	}
	var orderID int
	err = s.transaction(ctx, func(conn *sql.Conn) error {
		var err error
		orderID, err = s.ordersCreated(ctx)
		if err != nil {
			return err
		}
		amount := quantity * UnitPrice
		if _, err := conn.ExecContext(ctx,
			"INSERT INTO orders (id, product_id, quantity, state, amount) VALUES (?, ?, ?, ?, ?)",
			orderID, productID, quantity, "pending", amount); err != nil {
			return fmt.Errorf("insert the order: %w", err)
		}
		if _, err := conn.ExecContext(ctx,
			"UPDATE counters SET value = value + 1 WHERE name = 'orders'"); err != nil {
			return fmt.Errorf("bump the order counter: %w", err)
		}
		return nil
	})
	if err != nil {
		return Order{}, err
	}
	return s.getOrder(ctx, orderID)
}

// Reserve reserves the stock for an order.
//
// The check and the write are in one transaction, so two concurrent requests
// cannot both take the last unit: this is the rule the `no_overselling`
// invariant states.
func (s *Service) Reserve(orderID int) (Order, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.reserve(context.Background(), orderID)
}

func (s *Service) reserve(ctx context.Context, orderID int) (Order, error) {
	order, err := s.getOrder(ctx, orderID)
	if err != nil {
		return Order{}, err
	}
	if order.State != "pending" {
		return Order{}, conflict("order %d is %s, it cannot be reserved", orderID, order.State)
	}
	err = s.transaction(ctx, func(conn *sql.Conn) error {
		product, err := s.getProduct(ctx, order.ProductID)
		if err != nil {
			return err
		}
		if product == nil {
			return notFound("product %d does not exist", order.ProductID)
		}
		available := product.Stock - product.Reserved
		if available < order.Quantity {
			return conflict("not enough stock for product %d: %d available, %d requested",
				order.ProductID, available, order.Quantity)
		}
		if _, err := conn.ExecContext(ctx,
			"UPDATE products SET reserved = reserved + ? WHERE id = ?",
			order.Quantity, order.ProductID); err != nil {
			return fmt.Errorf("reserve the stock: %w", err)
		}
		if _, err := conn.ExecContext(ctx,
			"UPDATE orders SET state = 'reserved' WHERE id = ?", orderID); err != nil {
			return fmt.Errorf("move the order to reserved: %w", err)
		}
		return nil
	})
	if err != nil {
		return Order{}, err
	}
	return s.getOrder(ctx, orderID)
}

// Pay pays for a reserved order, at most once per idempotency key.
func (s *Service) Pay(orderID int, idempotencyKey string) (Order, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.pay(context.Background(), orderID, idempotencyKey)
}

func (s *Service) pay(ctx context.Context, orderID int, idempotencyKey string) (Order, error) {
	order, err := s.getOrder(ctx, orderID)
	if err != nil {
		return Order{}, err
	}
	if order.State != "reserved" {
		return Order{}, conflict("order %d is %s, it cannot be paid", orderID, order.State)
	}
	var used int
	err = s.conn.QueryRowContext(ctx,
		"SELECT order_id FROM payments WHERE idempotency_key = ?", idempotencyKey).Scan(&used)
	if err == nil {
		return Order{}, conflict("idempotency key %s was already used", idempotencyKey)
	}
	if err != sql.ErrNoRows {
		return Order{}, fmt.Errorf("read the payments: %w", err)
	}
	err = s.transaction(ctx, func(conn *sql.Conn) error {
		if _, err := conn.ExecContext(ctx,
			"INSERT INTO payments (idempotency_key, order_id, amount) VALUES (?, ?, ?)",
			idempotencyKey, orderID, order.Amount); err != nil {
			return fmt.Errorf("insert the payment: %w", err)
		}
		if _, err := conn.ExecContext(ctx,
			"UPDATE orders SET state = 'paid' WHERE id = ?", orderID); err != nil {
			return fmt.Errorf("move the order to paid: %w", err)
		}
		return nil
	})
	if err != nil {
		return Order{}, err
	}
	return s.getOrder(ctx, orderID)
}

// Ship ships a paid order: the reserved stock leaves the warehouse.
func (s *Service) Ship(orderID int) (Order, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.ship(context.Background(), orderID)
}

func (s *Service) ship(ctx context.Context, orderID int) (Order, error) {
	order, err := s.getOrder(ctx, orderID)
	if err != nil {
		return Order{}, err
	}
	if order.State != "paid" {
		return Order{}, conflict("order %d is %s, it cannot be shipped", orderID, order.State)
	}
	err = s.transaction(ctx, func(conn *sql.Conn) error {
		if _, err := conn.ExecContext(ctx,
			"UPDATE products SET stock = stock - ?, reserved = reserved - ? WHERE id = ?",
			order.Quantity, order.Quantity, order.ProductID); err != nil {
			return fmt.Errorf("consume the stock: %w", err)
		}
		if _, err := conn.ExecContext(ctx,
			"UPDATE orders SET state = 'shipped' WHERE id = ?", orderID); err != nil {
			return fmt.Errorf("move the order to shipped: %w", err)
		}
		return nil
	})
	if err != nil {
		return Order{}, err
	}
	return s.getOrder(ctx, orderID)
}

// Cancel cancels an order that was not paid yet, releasing the reservation.
func (s *Service) Cancel(orderID int) (Order, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.cancel(context.Background(), orderID)
}

func (s *Service) cancel(ctx context.Context, orderID int) (Order, error) {
	order, err := s.getOrder(ctx, orderID)
	if err != nil {
		return Order{}, err
	}
	if order.State != "pending" && order.State != "reserved" {
		return Order{}, conflict("order %d is %s, it cannot be cancelled", orderID, order.State)
	}
	err = s.transaction(ctx, func(conn *sql.Conn) error {
		if order.State == "reserved" {
			if _, err := conn.ExecContext(ctx,
				"UPDATE products SET reserved = reserved - ? WHERE id = ?",
				order.Quantity, order.ProductID); err != nil {
				return fmt.Errorf("release the reservation: %w", err)
			}
		}
		if _, err := conn.ExecContext(ctx,
			"UPDATE orders SET state = 'cancelled' WHERE id = ?", orderID); err != nil {
			return fmt.Errorf("move the order to cancelled: %w", err)
		}
		return nil
	})
	if err != nil {
		return Order{}, err
	}
	return s.getOrder(ctx, orderID)
}

// getProduct reads one catalogue entry.  A missing product is a nil product
// and no error: the callers turn it into the right domain error.
func (s *Service) getProduct(ctx context.Context, productID int) (*Product, error) {
	var p Product
	err := s.conn.QueryRowContext(ctx,
		"SELECT id, name, stock, reserved FROM products WHERE id = ?", productID).
		Scan(&p.ID, &p.Name, &p.Stock, &p.Reserved)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read the product: %w", err)
	}
	return &p, nil
}
