"""A small orders API: the business logic that `orders.verispec.yaml` describes.

The stack is deliberately ordinary:

* **FastAPI** for the HTTP layer,
* **sqlite3** for the database (the standard library, no ORM),
* a **service class** with the business rules.

The HTTP layer holds no logic of its own: every route is a shell over
`OrdersService`.  That is what makes the VeriSpec adapter honest: it calls
the same service methods the routes call, and it reads the state back from
the database.  If the adapter reimplemented the rules, the conformance test
would be testing the adapter.

Run it:

    uvicorn orders_api:app --reload

The lifecycle of an order, and the rule each step enforces:

    place_order  the order exists, with a positive quantity
    reserve      the stock is available      (no overselling)
    pay          the payment key is unused  (no double payment)
    ship         the order was paid
    cancel       the order was not paid yet
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

#: The price of one unit, in cents.
UNIT_PRICE = 10
#: The largest quantity a single order may ask for.
MAX_QUANTITY = 10
#: The catalogue the API starts with.
SEED_PRODUCTS = ((0, "widget", 5), (1, "gadget", 3))

SCHEMA = """
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
"""


class DomainError(Exception):
    """A business rule was violated. The HTTP layer turns it into a status."""

    status_code = 400


class NotFound(DomainError):
    status_code = 404


class Conflict(DomainError):
    """The request conflicts with the current state. HTTP 409."""

    status_code = 409


class Invalid(DomainError):
    """The request is malformed. HTTP 422."""

    status_code = 422


class OrdersService:
    """The business rules, over a SQLite database.

    Every method either succeeds or raises a `DomainError`.  Nothing here
    knows about HTTP, which is what lets the VeriSpec adapter reuse it.
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        # isolation_level=None: transactions are explicit, so the stock check
        # and the reservation happen atomically.
        self.conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.reset()

    # -- lifecycle --------------------------------------------------------

    def reset(self) -> None:
        """Drop everything and start from the seed catalogue."""
        self.conn.executescript("DROP TABLE IF EXISTS products;")
        self.conn.executescript("DROP TABLE IF EXISTS orders;")
        self.conn.executescript("DROP TABLE IF EXISTS payments;")
        self.conn.executescript("DROP TABLE IF EXISTS counters;")
        self.conn.executescript(SCHEMA)
        for pid, name, stock in SEED_PRODUCTS:
            self.conn.execute(
                "INSERT INTO products (id, name, stock, reserved) VALUES (?, ?, ?, 0)",
                (pid, name, stock),
            )
        self.conn.execute("INSERT INTO counters (name, value) VALUES ('orders', 0)")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """An immediate transaction: the write lock is taken up front."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    # -- reads ------------------------------------------------------------

    def list_products(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM products ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def list_orders(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM orders ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def get_order(self, order_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            raise NotFound(f"order {order_id} does not exist")
        return dict(row)

    def list_payments(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM payments ORDER BY order_id").fetchall()
        return [dict(r) for r in rows]

    def revenue(self) -> int:
        row = self.conn.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM payments").fetchone()
        return int(row["total"])

    def orders_created(self) -> int:
        row = self.conn.execute("SELECT value FROM counters WHERE name = 'orders'").fetchone()
        return int(row["value"]) if row else 0

    # -- the business rules -----------------------------------------------

    def place_order(self, product_id: int, quantity: int) -> dict[str, Any]:
        """Create an order for a product. The order starts as `pending`."""
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise Invalid("quantity must be an integer")
        if quantity < 1 or quantity > MAX_QUANTITY:
            raise Invalid(f"quantity must be between 1 and {MAX_QUANTITY}")
        product = self.conn.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None:
            raise NotFound(f"product {product_id} does not exist")
        with self.transaction() as conn:
            order_id = self.orders_created()
            amount = quantity * UNIT_PRICE
            conn.execute(
                "INSERT INTO orders (id, product_id, quantity, state, amount) VALUES (?, ?, ?, ?, ?)",
                (order_id, product_id, quantity, "pending", amount),
            )
            conn.execute("UPDATE counters SET value = value + 1 WHERE name = 'orders'")
        return self.get_order(order_id)

    def reserve(self, order_id: int) -> dict[str, Any]:
        """Reserve the stock for an order.

        The check and the write are in one transaction, so two concurrent
        requests cannot both take the last unit: this is the rule the
        `no_overselling` invariant states.
        """
        order = self.get_order(order_id)
        if order["state"] != "pending":
            raise Conflict(f"order {order_id} is {order['state']}, it cannot be reserved")
        product_id = order["product_id"]
        quantity = order["quantity"]
        with self.transaction() as conn:
            product = conn.execute(
                "SELECT * FROM products WHERE id = ?", (product_id,)
            ).fetchone()
            if product is None:
                raise NotFound(f"product {product_id} does not exist")
            available = product["stock"] - product["reserved"]
            if available < quantity:
                raise Conflict(
                    f"not enough stock for product {product_id}: "
                    f"{available} available, {quantity} requested"
                )
            conn.execute(
                "UPDATE products SET reserved = reserved + ? WHERE id = ?", (quantity, product_id)
            )
            conn.execute("UPDATE orders SET state = 'reserved' WHERE id = ?", (order_id,))
        return self.get_order(order_id)

    def pay(self, order_id: int, idempotency_key: str) -> dict[str, Any]:
        """Pay for a reserved order, at most once per idempotency key."""
        order = self.get_order(order_id)
        if order["state"] != "reserved":
            raise Conflict(f"order {order_id} is {order['state']}, it cannot be paid")
        used = self.conn.execute(
            "SELECT order_id FROM payments WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if used is not None:
            raise Conflict(f"idempotency key {idempotency_key} was already used")
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO payments (idempotency_key, order_id, amount) VALUES (?, ?, ?)",
                (idempotency_key, order_id, order["amount"]),
            )
            conn.execute("UPDATE orders SET state = 'paid' WHERE id = ?", (order_id,))
        return self.get_order(order_id)

    def ship(self, order_id: int) -> dict[str, Any]:
        """Ship a paid order: the reserved stock leaves the warehouse."""
        order = self.get_order(order_id)
        if order["state"] != "paid":
            raise Conflict(f"order {order_id} is {order['state']}, it cannot be shipped")
        with self.transaction() as conn:
            conn.execute(
                "UPDATE products SET stock = stock - ?, reserved = reserved - ? WHERE id = ?",
                (order["quantity"], order["quantity"], order["product_id"]),
            )
            conn.execute("UPDATE orders SET state = 'shipped' WHERE id = ?", (order_id,))
        return self.get_order(order_id)

    def cancel(self, order_id: int, release_reservation: bool = True) -> dict[str, Any]:
        """Cancel an order that was not paid yet, releasing the reservation.

        `release_reservation` exists so that the deliberately broken adapter
        can skip the release, which is the bug the conformance test has to
        catch.
        """
        order = self.get_order(order_id)
        if order["state"] not in ("pending", "reserved"):
            raise Conflict(f"order {order_id} is {order['state']}, it cannot be cancelled")
        with self.transaction() as conn:
            if release_reservation and order["state"] == "reserved":
                conn.execute(
                    "UPDATE products SET reserved = reserved - ? WHERE id = ?",
                    (order["quantity"], order["product_id"]),
                )
            conn.execute("UPDATE orders SET state = 'cancelled' WHERE id = ?", (order_id,))
        return self.get_order(order_id)


# --------------------------------------------------------------------------
# The HTTP layer: a thin shell over the service
# --------------------------------------------------------------------------


class PlaceOrderBody(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, le=MAX_QUANTITY)


class PayOrderBody(BaseModel):
    idempotency_key: str = Field(min_length=1)


service = OrdersService()
app = FastAPI(title="Orders API", version="1.0.0")


def get_service() -> OrdersService:
    """The service the routes use. The tests reset it between cases."""
    return service


@app.exception_handler(DomainError)
async def domain_error_handler(request, exc: DomainError):  # noqa: ANN001
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.get("/products")
def list_products() -> list[dict[str, Any]]:
    return get_service().list_products()


@app.get("/orders")
def list_orders() -> list[dict[str, Any]]:
    return get_service().list_orders()


@app.get("/orders/{order_id}")
def get_order(order_id: int) -> dict[str, Any]:
    return get_service().get_order(order_id)


@app.post("/orders", status_code=201)
def create_order(body: PlaceOrderBody) -> dict[str, Any]:
    return get_service().place_order(body.product_id, body.quantity)


@app.post("/orders/{order_id}/reserve")
def reserve_order(order_id: int) -> dict[str, Any]:
    return get_service().reserve(order_id)


@app.post("/orders/{order_id}/pay")
def pay_order(order_id: int, body: PayOrderBody) -> dict[str, Any]:
    return get_service().pay(order_id, body.idempotency_key)


@app.post("/orders/{order_id}/ship")
def ship_order(order_id: int) -> dict[str, Any]:
    return get_service().ship(order_id)


@app.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int) -> dict[str, Any]:
    return get_service().cancel(order_id)
