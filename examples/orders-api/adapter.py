"""The VeriSpec adapter for the orders API.

The adapter is deliberately thin.  It calls the same service methods the
HTTP routes call, and it reads the state back from the database.  It does
not reimplement a single business rule: if it did, the conformance test
would be testing the adapter instead of the API.

The model is bounded (two products, two orders, two idempotency keys) while
the implementation is not: a real database holds as many orders as you ask
for.  When the implementation goes past the bounds of the model, the adapter
says so instead of quietly hiding the extra rows, because a check that
silently drops state is not a check.
"""

from __future__ import annotations

from typing import Any

from orders_api import OrdersService

#: What the bounded model covers.
MODEL_PRODUCTS = (0, 1)
MODEL_ORDERS = (0, 1)
MODEL_KEYS = ("k1", "k2")


class ModelOverflow(Exception):
    """The implementation went past the bounds of the bounded model."""


class OrdersApiAdapter:
    """Expose the orders service to VeriSpec."""

    def __init__(self, service: OrdersService | None = None):
        self.service = service if service is not None else OrdersService()

    # -- the adapter contract --------------------------------------------

    def reset(self) -> None:
        """Put the implementation in the initial state of the spec."""
        self.service.reset()

    def apply(self, action: str, **params: Any) -> None:
        """Apply one action of the spec."""
        if action == "place_order":
            order = self.service.place_order(params["product"], params["quantity"])
            if order["id"] != params["order_id"]:
                raise ModelOverflow(
                    f"the model expects order {params['order_id']} but the service created "
                    f"order {order['id']}: the model is out of bounds"
                )
        elif action == "reserve":
            order = self.service.get_order(params["order_id"])
            if order["product_id"] != params["product"]:
                raise ModelOverflow(
                    f"order {params['order_id']} is for product {order['product_id']}, "
                    f"not {params['product']}"
                )
            self.service.reserve(params["order_id"])
        elif action == "pay":
            self.service.pay(params["order_id"], params["key"])
        elif action == "ship":
            self.service.ship(params["order_id"])
        elif action == "cancel":
            self.service.cancel(params["order_id"])
        else:
            raise ValueError(f"unknown action {action!r}")

    def snapshot(self) -> dict[str, Any]:
        """The current state, with exactly the spec's variables."""
        products = {p["id"]: p for p in self.service.list_products()}
        orders = {o["id"]: o for o in self.service.list_orders()}
        payments = self.service.list_payments()

        self._check_domain("product", set(products) - set(MODEL_PRODUCTS))
        self._check_domain("order", set(orders) - set(MODEL_ORDERS))
        self._check_domain(
            "idempotency key", {p["idempotency_key"] for p in payments} - set(MODEL_KEYS)
        )

        return {
            "stock": {pid: p["stock"] for pid, p in products.items()},
            "reserved": {pid: p["reserved"] for pid, p in products.items()},
            "order_product": {oid: o["product_id"] for oid, o in orders.items()},
            "order_quantity": {oid: o["quantity"] for oid, o in orders.items()},
            "order_state": {oid: o["state"] for oid, o in orders.items()},
            "order_amount": {oid: o["amount"] for oid, o in orders.items()},
            "paid_keys": {p["idempotency_key"] for p in payments},
            "revenue": self.service.revenue(),
            "orders_created": self.service.orders_created(),
        }

    # -- helpers ----------------------------------------------------------

    def _check_domain(self, what: str, extra: set) -> None:
        if extra:
            raise ModelOverflow(
                f"the bounded model does not cover {what} {sorted(extra)}: "
                "the implementation is past the bounds of the spec"
            )


class BuggyOrdersApiAdapter(OrdersApiAdapter):
    """The deliberately broken implementation.

    It cancels an order without releasing the reserved stock, so the
    warehouse keeps stock that was never sold.  The conformance test must
    FAIL on it: a suite that stays green on this adapter is not testing
    anything.
    """

    def apply(self, action: str, **params: Any) -> None:
        if action == "cancel":
            # BUG: the reservation is not released.
            self.service.cancel(params["order_id"], release_reservation=False)
            return
        super().apply(action, **params)


class OversellingOrdersApiAdapter(OrdersApiAdapter):
    """The implementation that oversells.

    It reserves stock without checking that the stock is there, the classic
    bug of a check that is not atomic with the write.  The conformance test
    cannot catch it: the generated test only takes actions the spec allows.
    The *trace* path can, because a trace records the states the
    implementation really went through, and the recorded state violates
    `no_overselling`.
    """

    def apply(self, action: str, **params: Any) -> None:
        if action == "reserve":
            order = self.service.get_order(params["order_id"])
            # BUG: no check that there is enough stock.
            with self.service.transaction() as conn:
                conn.execute(
                    "UPDATE products SET reserved = reserved + ? WHERE id = ?",
                    (order["quantity"], order["product_id"]),
                )
                conn.execute(
                    "UPDATE orders SET state = 'reserved' WHERE id = ?", (params["order_id"],)
                )
            return
        super().apply(action, **params)
