/**
 * The VeriSpec adapter for the orders API, in TypeScript.
 *
 * The adapter is deliberately thin.  It calls the same service methods the
 * HTTP routes call, and it reads the state back from the database.  It does
 * not reimplement a single business rule: if it did, the conformance test
 * would be testing the adapter instead of the API.
 *
 * The model is bounded (two products, two orders, two idempotency keys)
 * while the implementation is not: a real database holds as many orders as
 * you ask for.  When the implementation goes past the bounds of the model,
 * the adapter says so instead of quietly hiding the extra rows, because a
 * check that silently drops state is not a check.
 */
import { OrdersService } from "./orders-api.js";

/** What the bounded model covers. */
export const MODEL_PRODUCTS = [0, 1];
export const MODEL_ORDERS = [0, 1];
export const MODEL_KEYS = ["k1", "k2"];

/** The implementation went past the bounds of the bounded model. */
export class ModelOverflow extends Error {}

/** The VeriSpec adapter contract. */
export interface Adapter {
  reset(): void;
  apply(action: string, params: Record<string, any>): void;
  snapshot(): Record<string, any>;
}

export class OrdersApiAdapter implements Adapter {
  protected readonly service: OrdersService;

  constructor(service: OrdersService = new OrdersService()) {
    this.service = service;
  }

  /** Put the implementation in the initial state of the spec. */
  reset(): void {
    this.service.reset();
  }

  /** Apply one action of the spec. */
  apply(action: string, params: Record<string, any>): void {
    if (action === "place_order") {
      const order = this.service.placeOrder(Number(params.product), Number(params.quantity));
      if (order.id !== Number(params.order_id)) {
        throw new ModelOverflow(
          `the model expects order ${params.order_id} but the service created ` +
            `order ${order.id}: the model is out of bounds`,
        );
      }
    } else if (action === "reserve") {
      const order = this.service.getOrder(Number(params.order_id));
      if (order.product_id !== Number(params.product)) {
        throw new ModelOverflow(
          `order ${params.order_id} is for product ${order.product_id}, not ${params.product}`,
        );
      }
      this.service.reserve(Number(params.order_id));
    } else if (action === "pay") {
      this.service.pay(Number(params.order_id), String(params.key));
    } else if (action === "ship") {
      this.service.ship(Number(params.order_id));
    } else if (action === "cancel") {
      this.service.cancel(Number(params.order_id));
    } else {
      throw new Error(`unknown action '${action}'`);
    }
  }

  /** The current state, with exactly the spec's variables. */
  snapshot(): Record<string, any> {
    const products = this.service.listProducts();
    const orders = this.service.listOrders();
    const payments = this.service.listPayments();

    this.checkDomain(
      "product",
      products.map((p) => p.id).filter((id) => !MODEL_PRODUCTS.includes(id)),
    );
    this.checkDomain(
      "order",
      orders.map((o) => o.id).filter((id) => !MODEL_ORDERS.includes(id)),
    );
    this.checkDomain(
      "idempotency key",
      payments.map((p) => p.idempotency_key).filter((k) => !MODEL_KEYS.includes(k)),
    );

    const stock: Record<string, number> = {};
    const reserved: Record<string, number> = {};
    for (const p of products) {
      stock[String(p.id)] = p.stock;
      reserved[String(p.id)] = p.reserved;
    }
    const orderProduct: Record<string, number> = {};
    const orderQuantity: Record<string, number> = {};
    const orderState: Record<string, string> = {};
    const orderAmount: Record<string, number> = {};
    for (const o of orders) {
      orderProduct[String(o.id)] = o.product_id;
      orderQuantity[String(o.id)] = o.quantity;
      orderState[String(o.id)] = o.state;
      orderAmount[String(o.id)] = o.amount;
    }
    const paidKeys = payments.map((p) => p.idempotency_key).sort();

    return {
      stock,
      reserved,
      order_product: orderProduct,
      order_quantity: orderQuantity,
      order_state: orderState,
      order_amount: orderAmount,
      paid_keys: paidKeys,
      revenue: this.service.revenue(),
      orders_created: this.service.ordersCreated(),
    };
  }

  private checkDomain(what: string, extra: number[] | string[]): void {
    if (extra.length > 0) {
      throw new ModelOverflow(
        `the bounded model does not cover ${what} ${JSON.stringify(extra)}: ` +
          "the implementation is past the bounds of the spec",
      );
    }
  }
}

/** The factory the generated conformance test uses. */
export function makeAdapter(): Adapter {
  return new OrdersApiAdapter();
}

/**
 * The deliberately broken implementation.
 *
 * It cancels an order without releasing the reserved stock, so the
 * warehouse keeps stock that was never sold.  The conformance test must FAIL
 * on it: a suite that stays green on this adapter is not testing anything.
 */
export class BuggyOrdersApiAdapter extends OrdersApiAdapter {
  apply(action: string, params: Record<string, any>): void {
    if (action === "cancel") {
      // BUG: the reservation is not released.
      this.service.cancel(Number(params.order_id), { releaseReservation: false });
      return;
    }
    super.apply(action, params);
  }
}
