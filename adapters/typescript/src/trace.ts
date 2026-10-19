/**
 * The VeriSpec trace recorder for TypeScript and JavaScript.
 *
 * A trace is how a real implementation reports what it did, in the language
 * agnostic JSON format that `verispec check-trace` replays:
 *
 *     [
 *       {"action": "init", "params": {}, "state": {...}},
 *       {"action": "deposit", "params": {"amount": 10}, "state": {...}}
 *     ]
 *
 * The recorder calls the adapter, so the trace cannot drift from what the
 * implementation actually did.
 */

/** The adapter contract: three methods, and the state is exactly the spec's variables. */
export interface Adapter {
  reset(): void;
  apply(action: string, params: Record<string, any>): void;
  snapshot(): Record<string, any>;
}

/** One recorded step. */
export interface TraceStep {
  action: string;
  params: Record<string, any>;
  state: Record<string, any>;
}

/** The trace recorder. */
export class TraceRecorder {
  private readonly adapter: Adapter;
  private readonly steps: TraceStep[] = [];

  constructor(adapter: Adapter) {
    this.adapter = adapter;
  }

  /** Reset the implementation and record the initial state. */
  reset(): void {
    this.adapter.reset();
    this.steps.length = 0;
    this.steps.push({ action: "init", params: {}, state: this.snapshot() });
  }

  /** Apply one action and record the resulting state. */
  apply(action: string, params: Record<string, any> = {}): void {
    this.adapter.apply(action, params);
    this.steps.push({ action, params: { ...params }, state: this.snapshot() });
  }

  /** The state the implementation reports, as a plain JSON object. */
  snapshot(): Record<string, any> {
    return JSON.parse(JSON.stringify(this.adapter.snapshot()));
  }

  /** The recorded steps. */
  get trace(): TraceStep[] {
    return this.steps;
  }

  /** The trace as a JSON document. */
  toJson(indent = 2): string {
    return JSON.stringify(this.steps, null, indent);
  }

  /** Write the trace to a file. */
  write(path: string): void {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { writeFileSync } = require("node:fs");
    writeFileSync(path, this.toJson() + "\n", "utf8");
  }
}

/**
 * Record a sequence of actions with an adapter and return the trace.
 */
export function recordTrace(
  adapter: Adapter,
  actions: Array<[string, Record<string, any>?]>,
): TraceStep[] {
  const recorder = new TraceRecorder(adapter);
  recorder.reset();
  for (const [action, params] of actions) {
    recorder.apply(action, params ?? {});
  }
  return recorder.trace;
}
