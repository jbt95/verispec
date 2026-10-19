/**
 * The orders API in TypeScript: the same contract, the same rules, the same
 * database as the Python implementation.
 *
 * The stack is deliberately ordinary and dependency free:
 *
 *   - `node:http` for the HTTP layer,
 *   - `node:sqlite` for the database (built into Node),
 *   - an `OrdersService` class with the business rules.
 *
 * The HTTP layer holds no logic of its own: every route is a shell over
 * `OrdersService`.  That is what makes the VeriSpec adapter honest: it calls
 * the same service methods the routes call, and it reads the state back from
 * the database.
 *
 * The contract is `orders.verispec.yaml`, shared with the Python and the Go
 * implementations.  One spec, three implementations, one behaviour.
 */
import { DatabaseSync } from "node:sqlite";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

/** The price of one unit, in cents. */
export const UNIT_PRICE = 10;
/** The largest quantity a single order may ask for. */
export const MAX_QUANTITY = 10;
/** The catalogue the API starts with. */
export const SEED_PRODUCTS: Array<{ id: number; name: string; stock: number }> = [
  { id: 0, name: "widget", stock: 5 },
  { id: 1, name: "gadget", stock: 3 },
];

const SCHEMA = `
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
`;

/** A business rule was violated. The HTTP layer turns it into a status. */
export class DomainError extends Error {
  status = 400;
}
export class NotFound extends Error {
  status = 404;
}
export class Conflict extends Error {
  status = 409;
}
export class Invalid extends Error {
  status = 422;
}

export interface Product {
  id: number;
  name: string;
  stock: number;
  reserved: number;
}

export interface Order {
  id: number;
  product_id: number;
  quantity: number;
  state: string;
  amount: number;
}

export interface Payment {
  idempotency_key: string;
  order_id: number;
  amount: number;
}

/**
 * The business rules, over a SQLite database.
 *
 * Every method either succeeds or throws a `DomainError`.  Nothing here
 * knows about HTTP, which is what lets the VeriSpec adapter reuse it.
 */
export class OrdersService {
  private readonly db: DatabaseSync;

  constructor(path = ":memory:") {
    this.db = new DatabaseSync(path);
    this.reset();
  }

  /** Drop everything and start from the seed catalogue. */
  reset(): void {
    this.db.exec("DROP TABLE IF EXISTS products");
    this.db.exec("DROP TABLE IF EXISTS orders");
    this.db.exec("DROP TABLE IF EXISTS payments");
    this.db.exec("DROP TABLE IF EXISTS counters");
    this.db.exec(SCHEMA);
    for (const p of SEED_PRODUCTS) {
      this.db
        .prepare("INSERT INTO products (id, name, stock, reserved) VALUES (?, ?, ?, 0)")
        .run(p.id, p.name, p.stock);
    }
    this.db.prepare("INSERT INTO counters (name, value) VALUES ('orders', 0)").run();
  }

  /** An immediate transaction: the write lock is taken up front. */
  private transaction<T>(body: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const out = body();
      this.db.exec("COMMIT");
      return out;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  // -- reads --------------------------------------------------------------

  listProducts(): Product[] {
    return this.db.prepare("SELECT * FROM products ORDER BY id").all() as unknown as Product[];
  }

  listOrders(): Order[] {
    return this.db.prepare("SELECT * FROM orders ORDER BY id").all() as unknown as Order[];
  }

  getOrder(orderId: number): Order {
    const row = this.db.prepare("SELECT * FROM orders WHERE id = ?").get(orderId) as
      | unknown as Order
      | undefined;
    if (!row) throw new NotFound(`order ${orderId} does not exist`);
    return row;
  }

  listPayments(): Payment[] {
    return this.db.prepare("SELECT * FROM payments ORDER BY order_id").all() as unknown as Payment[];
  }

  revenue(): number {
    const row = this.db.prepare("SELECT COALESCE(SUM(amount), 0) AS total FROM payments").get() as
      | unknown as { total: number }
      | undefined;
    return row ? Number(row.total) : 0;
  }

  ordersCreated(): number {
    const row = this.db.prepare("SELECT value FROM counters WHERE name = 'orders'").get() as
      | unknown as { value: number }
      | undefined;
    return row ? Number(row.value) : 0;
  }

  // -- the business rules -------------------------------------------------

  /** Create an order for a product. The order starts as `pending`. */
  placeOrder(productId: number, quantity: number): Order {
    if (!Number.isInteger(quantity)) throw new Invalid("quantity must be an integer");
    if (quantity < 1 || quantity > MAX_QUANTITY) {
      throw new Invalid(`quantity must be between 1 and ${MAX_QUANTITY}`);
    }
    const product = this.db.prepare("SELECT * FROM products WHERE id = ?").get(productId) as
      | unknown as Product
      | undefined;
    if (!product) throw new NotFound(`product ${productId} does not exist`);
    return this.transaction(() => {
      const orderId = this.ordersCreated();
      const amount = quantity * UNIT_PRICE;
      this.db
        .prepare("INSERT INTO orders (id, product_id, quantity, state, amount) VALUES (?, ?, ?, ?, ?)")
        .run(orderId, productId, quantity, "pending", amount);
      this.db.prepare("UPDATE counters SET value = value + 1 WHERE name = 'orders'").run();
      return this.getOrder(orderId);
    });
  }

  /**
   * Reserve the stock for an order.
   *
   * The check and the write are in one transaction, so two concurrent
   * requests cannot both take the last unit: this is the rule the
   * `no_overselling` invariant states.
   */
  reserve(orderId: number): Order {
    const order = this.getOrder(orderId);
    if (order.state !== "pending") {
      throw new Conflict(`order ${orderId} is ${order.state}, it cannot be reserved`);
    }
    const { product_id: productId, quantity } = order;
    return this.transaction(() => {
      const product = this.db.prepare("SELECT * FROM products WHERE id = ?").get(productId) as
        | unknown as Product
        | undefined;
      if (!product) throw new NotFound(`product ${productId} does not exist`);
      const available = product.stock - product.reserved;
      if (available < quantity) {
        throw new Conflict(
          `not enough stock for product ${productId}: ${available} available, ${quantity} requested`,
        );
      }
      this.db
        .prepare("UPDATE products SET reserved = reserved + ? WHERE id = ?")
        .run(quantity, productId);
      this.db.prepare("UPDATE orders SET state = 'reserved' WHERE id = ?").run(orderId);
      return this.getOrder(orderId);
    });
  }

  /** Pay for a reserved order, at most once per idempotency key. */
  pay(orderId: number, idempotencyKey: string): Order {
    const order = this.getOrder(orderId);
    if (order.state !== "reserved") {
      throw new Conflict(`order ${orderId} is ${order.state}, it cannot be paid`);
    }
    const used = this.db
      .prepare("SELECT order_id FROM payments WHERE idempotency_key = ?")
      .get(idempotencyKey);
    if (used) throw new Conflict(`idempotency key ${idempotencyKey} was already used`);
    return this.transaction(() => {
      this.db
        .prepare("INSERT INTO payments (idempotency_key, order_id, amount) VALUES (?, ?, ?)")
        .run(idempotencyKey, orderId, order.amount);
      this.db.prepare("UPDATE orders SET state = 'paid' WHERE id = ?").run(orderId);
      return this.getOrder(orderId);
    });
  }

  /** Ship a paid order: the reserved stock leaves the warehouse. */
  ship(orderId: number): Order {
    const order = this.getOrder(orderId);
    if (order.state !== "paid") {
      throw new Conflict(`order ${orderId} is ${order.state}, it cannot be shipped`);
    }
    return this.transaction(() => {
      this.db
        .prepare("UPDATE products SET stock = stock - ?, reserved = reserved - ? WHERE id = ?")
        .run(order.quantity, order.quantity, order.product_id);
      this.db.prepare("UPDATE orders SET state = 'shipped' WHERE id = ?").run(orderId);
      return this.getOrder(orderId);
    });
  }

  /**
   * Cancel an order that was not paid yet, releasing the reservation.
   *
   * `releaseReservation` exists so that the deliberately broken adapter can
   * skip the release, which is the bug the conformance test has to catch.
   */
  cancel(orderId: number, options: { releaseReservation?: boolean } = {}): Order {
    const release = options.releaseReservation !== false;
    const order = this.getOrder(orderId);
    if (order.state !== "pending" && order.state !== "reserved") {
      throw new Conflict(`order ${orderId} is ${order.state}, it cannot be cancelled`);
    }
    return this.transaction(() => {
      if (release && order.state === "reserved") {
        this.db
          .prepare("UPDATE products SET reserved = reserved - ? WHERE id = ?")
          .run(order.quantity, order.product_id);
      }
      this.db.prepare("UPDATE orders SET state = 'cancelled' WHERE id = ?").run(orderId);
      return this.getOrder(orderId);
    });
  }
}

// --------------------------------------------------------------------------
// The HTTP layer: a thin shell over the service
// --------------------------------------------------------------------------

async function readJson(req: IncomingMessage): Promise<any> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function send(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json" });
  res.end(payload);
}

/** The request handler: every route is a shell over the service. */
export function createHandler(service: OrdersService) {
  return async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    const url = new URL(req.url ?? "/", "http://localhost");
    const path = url.pathname;
    const method = req.method ?? "GET";
    try {
      if (method === "GET" && path === "/products") {
        send(res, 200, service.listProducts());
        return;
      }
      if (method === "GET" && path === "/orders") {
        send(res, 200, service.listOrders());
        return;
      }
      const orderMatch = /^\/orders\/(\d+)$/.exec(path);
      if (method === "GET" && orderMatch) {
        send(res, 200, service.getOrder(Number(orderMatch[1])));
        return;
      }
      if (method === "POST" && path === "/orders") {
        const body = await readJson(req);
        send(res, 201, service.placeOrder(Number(body.product_id), Number(body.quantity)));
        return;
      }
      const actionMatch = /^\/orders\/(\d+)\/(reserve|pay|ship|cancel)$/.exec(path);
      if (method === "POST" && actionMatch) {
        const orderId = Number(actionMatch[1]);
        const action = actionMatch[2];
        if (action === "reserve") send(res, 200, service.reserve(orderId));
        else if (action === "ship") send(res, 200, service.ship(orderId));
        else if (action === "cancel") send(res, 200, service.cancel(orderId));
        else {
          const body = await readJson(req);
          send(res, 200, service.pay(orderId, String(body.idempotency_key)));
        }
        return;
      }
      send(res, 404, { detail: "not found" });
    } catch (error) {
      if (error instanceof DomainError || error instanceof NotFound || error instanceof Conflict || error instanceof Invalid) {
        send(res, (error as { status: number }).status, { detail: String(error.message) });
        return;
      }
      throw error;
    }
  };
}

/** Start the API. `port` 0 picks a free port. */
export function startServer(port = 3000, service = new OrdersService()) {
  const server = createServer(createHandler(service));
  return new Promise<{ port: number; close: () => void }>((resolve) => {
    server.listen(port, () => {
      const address = server.address();
      const actual = typeof address === "object" && address ? address.port : port;
      resolve({ port: actual, close: () => server.close() });
    });
  });
}
