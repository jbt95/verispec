// The package is ESM by default ("type": "module"), so the CommonJS build
// needs its own marker to be loaded as CommonJS by Node.
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const target = join(here, "..", "dist", "cjs");
mkdirSync(target, { recursive: true });
writeFileSync(join(target, "package.json"), JSON.stringify({ type: "commonjs" }, null, 2) + "\n");
console.log("wrote dist/cjs/package.json");
