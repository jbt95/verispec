/**
 * Run the orders API: `node dist/serve.js [port]`.
 */
import { startServer } from "./orders-api.js";

const port = Number(process.argv[2] ?? 3000);
const { port: actual } = await startServer(port);
console.log(`orders API listening on http://localhost:${actual}`);
console.log("try: curl -X POST localhost:%d/orders -H 'content-type: application/json' -d '{\"product_id\":0,\"quantity\":2}'", actual);
