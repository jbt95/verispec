import { ChildProcess, spawn } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as vscode from 'vscode';

/** Name of the diagnostic collection owned by this extension. */
const DIAGNOSTIC_COLLECTION = 'verispec';
const DIAGNOSTIC_SOURCE = 'verispec';
/** Virtual document scheme used to show the generated agent brief. */
const BRIEF_SCHEME = 'verispec-brief';
/** Workspace glob used to discover specs when no document is focused. */
const SPEC_GLOB = '**/*.verispec.{yaml,yml}';
const SPEC_DIALOG_FILTER: Record<string, string[]> = { 'VeriSpec spec': ['yaml', 'yml'] };

/** Result of a single CLI invocation. */
interface CliResult {
    /** Process exit code (3 is used for spawn/tooling failures). */
    code: number;
    stdout: string;
    stderr: string;
    /** Set when the CLI could not be spawned at all (e.g. ENOENT). */
    spawnError?: string;
}

/** Loose shape of `verispec check --format json` output. */
interface CheckResult {
    status?: string;
    bound?: number;
    violated?: string[];
    trace?: any[];
    message?: string;
}

/**
 * Serves the generated agent brief as a read-only virtual document
 * (`verispec-brief:` scheme). Declared before it is instantiated because
 * class declarations are not hoisted.
 */
class BriefContentProvider implements vscode.TextDocumentContentProvider {
    private readonly contents = new Map<string, string>();

    setContent(uri: vscode.Uri, content: string): void {
        this.contents.set(uri.path, content);
        this.contents.set(uri.toString(), content);
    }

    provideTextDocumentContent(uri: vscode.Uri): string {
        return this.contents.get(uri.path) ?? this.contents.get(uri.toString()) ?? '# No brief available.';
    }
}

let diagnostics: vscode.DiagnosticCollection;
let output: vscode.OutputChannel;
const briefProvider = new BriefContentProvider();
const runTokens = new Map<string, number>();
let runCounter = 0;

export function activate(context: vscode.ExtensionContext): void {
    diagnostics = vscode.languages.createDiagnosticCollection(DIAGNOSTIC_COLLECTION);
    output = vscode.window.createOutputChannel('VeriSpec');

    context.subscriptions.push(diagnostics);
    context.subscriptions.push(output);
    context.subscriptions.push(
        vscode.workspace.registerTextDocumentContentProvider(BRIEF_SCHEME, briefProvider)
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('verispec.validateSpec', async (arg?: unknown) => {
            const uri = await resolveSpecUri(arg);
            if (uri) {
                await validateSpec(uri);
            }
        })
    );
    context.subscriptions.push(
        vscode.commands.registerCommand('verispec.checkSpec', async (arg?: unknown) => {
            const uri = await resolveSpecUri(arg);
            if (uri) {
                await checkSpec(uri, true);
            }
        })
    );
    context.subscriptions.push(
        vscode.commands.registerCommand('verispec.checkTrace', async (arg?: unknown) => {
            const uri = await resolveSpecUri(arg);
            if (uri) {
                await checkTrace(uri);
            }
        })
    );
    context.subscriptions.push(
        vscode.commands.registerCommand('verispec.generateBrief', async (arg?: unknown) => {
            const uri = await resolveSpecUri(arg);
            if (uri) {
                await generateBrief(uri);
            }
        })
    );
    context.subscriptions.push(
        vscode.commands.registerCommand('verispec.showReport', async (arg?: unknown) => {
            const uri = await resolveSpecUri(arg);
            if (uri) {
                await showReport(uri);
            }
        })
    );

    // Inline diagnostics: check specs when they are opened and when they are saved.
    context.subscriptions.push(
        vscode.workspace.onDidOpenTextDocument((doc) => maybeAutoCheck(doc))
    );
    context.subscriptions.push(
        vscode.workspace.onDidSaveTextDocument((doc) => maybeAutoCheck(doc))
    );
    context.subscriptions.push(
        vscode.workspace.onDidChangeConfiguration((event) => {
            if (!event.affectsConfiguration('verispec')) {
                return;
            }
            for (const doc of vscode.workspace.textDocuments) {
                void maybeAutoCheck(doc);
            }
        })
    );
}

export function deactivate(): void {
    // Every disposable is registered in `context.subscriptions`.
}

/* ------------------------------------------------------------------ */
/* CLI plumbing                                                        */
/* ------------------------------------------------------------------ */

function configuration(): vscode.WorkspaceConfiguration {
    return vscode.workspace.getConfiguration('verispec');
}

function cliPath(): string {
    const configured = configuration().get<string>('cliPath', 'verispec');
    const trimmed = (configured || '').trim();
    return trimmed.length > 0 ? trimmed : 'verispec';
}

function boundSetting(): number {
    const raw = configuration().get<number>('bound', 10);
    if (typeof raw !== 'number' || !Number.isFinite(raw) || raw < 1) {
        return 10;
    }
    return Math.floor(raw);
}

function showInlineDiagnosticsEnabled(): boolean {
    return configuration().get<boolean>('showInlineDiagnostics', true) !== false;
}

function friendlySpawnError(error: unknown): string {
    const code = (error as NodeJS.ErrnoException | undefined)?.code;
    if (code === 'ENOENT') {
        return (
            `Could not run the VeriSpec CLI ('${cliPath()}'). Install it with 'pip install verispec' ` +
            "or set the 'verispec.cliPath' setting to the full path of the executable."
        );
    }
    const detail = error instanceof Error ? error.message : String(error);
    return `Could not run the VeriSpec CLI ('${cliPath()}'): ${detail}`;
}

/**
 * Spawns the VeriSpec CLI without a shell and collects stdout/stderr/exit code.
 * Spawn failures (for example a missing executable) are reported as exit code 3.
 */
function runCli(args: string[]): Promise<CliResult> {
    return new Promise<CliResult>((resolve) => {
        const command = cliPath();
        log(`$ ${command} ${args.join(' ')}`);
        let child: ChildProcess;
        try {
            child = spawn(command, args, { shell: false, windowsHide: true });
        } catch (error) {
            resolve({ code: 3, stdout: '', stderr: '', spawnError: friendlySpawnError(error) });
            return;
        }

        let stdout = '';
        let stderr = '';
        let settled = false;
        const finish = (result: CliResult): void => {
            if (!settled) {
                settled = true;
                resolve(result);
            }
        };

        child.stdout?.on('data', (chunk: Buffer | string) => {
            stdout += chunk.toString();
        });
        child.stderr?.on('data', (chunk: Buffer | string) => {
            stderr += chunk.toString();
        });
        child.on('error', (error: Error) => {
            finish({ code: 3, stdout, stderr, spawnError: friendlySpawnError(error) });
        });
        child.on('close', (code: number | null) => {
            finish({ code: code === null ? 3 : code, stdout, stderr });
        });
    });
}

/* ------------------------------------------------------------------ */
/* Spec resolution                                                     */
/* ------------------------------------------------------------------ */

function isUriLike(value: unknown): boolean {
    return (
        typeof value === 'object' &&
        value !== null &&
        typeof (value as { fsPath?: unknown }).fsPath === 'string'
    );
}

function uriFromArg(arg: unknown): vscode.Uri | undefined {
    if (!arg) {
        return undefined;
    }
    if (Array.isArray(arg)) {
        for (const item of arg) {
            const uri = uriFromArg(item);
            if (uri) {
                return uri;
            }
        }
        return undefined;
    }
    if (isUriLike(arg)) {
        return arg as vscode.Uri;
    }
    return undefined;
}

function isVeriSpecDocument(doc: vscode.TextDocument): boolean {
    return doc.languageId === 'verispec' || /\.verispec\.(?:yaml|yml)$/i.test(doc.fileName);
}

/** Resolves the spec file a command should operate on. */
async function resolveSpecUri(arg?: unknown): Promise<vscode.Uri | undefined> {
    const fromArgument = uriFromArg(arg);
    if (fromArgument) {
        return fromArgument;
    }
    const active = vscode.window.activeTextEditor?.document;
    if (active && isVeriSpecDocument(active)) {
        return active.uri;
    }
    const found = await vscode.workspace.findFiles(SPEC_GLOB, '**/node_modules/**', 20);
    if (found.length > 0) {
        return found[0];
    }
    if (active && /\.(?:yaml|yml)$/i.test(active.fileName)) {
        return active.uri;
    }
    const picked = await vscode.window.showOpenDialog({
        canSelectMany: false,
        filters: SPEC_DIALOG_FILTER,
        openLabel: 'Select spec'
    });
    if (picked && picked.length > 0) {
        return picked[0];
    }
    return undefined;
}

/* ------------------------------------------------------------------ */
/* Commands                                                            */
/* ------------------------------------------------------------------ */

async function validateSpec(uri: vscode.Uri): Promise<void> {
    const result = await runCli(['validate', uri.fsPath]);
    if (result.spawnError) {
        void vscode.window.showErrorMessage(result.spawnError);
        return;
    }
    if (result.code === 0) {
        void vscode.window.showInformationMessage(
            result.stdout.trim() || `Spec ${path.basename(uri.fsPath)} is valid.`
        );
        return;
    }
    const detail = (result.stderr || result.stdout).trim();
    if (result.code === 2) {
        void vscode.window.showErrorMessage(`VeriSpec spec error: ${detail || 'the spec could not be parsed.'}`);
        return;
    }
    void vscode.window.showErrorMessage(
        `verispec validate failed (exit ${result.code})${detail ? `: ${detail}` : ''}`
    );
}

function okMessage(bound: number): string {
    return `no violation up to bound ${bound} — bounded claim only, not a proof for all depths`;
}

function beginRun(uri: vscode.Uri): number {
    runCounter += 1;
    runTokens.set(uri.toString(), runCounter);
    return runCounter;
}

function isCurrentRun(uri: vscode.Uri, token: number): boolean {
    return runTokens.get(uri.toString()) === token;
}

async function checkSpec(uri: vscode.Uri, notify: boolean): Promise<void> {
    const token = beginRun(uri);
    const bound = boundSetting();
    const started = Date.now();

    let result = await runCli(['check', uri.fsPath, '--bound', String(bound), '--format', 'json']);
    let parsed = parseCheckResult(result.stdout);

    if (!parsed && result.code === 2 && looksLikeUnsupportedJsonFlag(result.stderr)) {
        // Older CLI builds do not understand `--format json`: fall back to the
        // human readable output instead of failing the whole command.
        result = await runCli(['check', uri.fsPath, '--bound', String(bound)]);
        parsed = parseCheckText(result.stdout);
    }

    if (result.spawnError) {
        if (isCurrentRun(uri, token)) {
            diagnostics.delete(uri);
        }
        if (notify) {
            void vscode.window.showErrorMessage(result.spawnError);
        }
        log(result.spawnError);
        return;
    }

    if (!isCurrentRun(uri, token)) {
        return;
    }

    const status = parsed?.status ?? statusFromExitCode(result.code);
    const elapsed = Date.now() - started;
    log(`check ${uri.fsPath} finished in ${elapsed} ms (status=${status}, exit=${result.code})`);

    if (status === 'violation') {
        diagnostics.set(uri, buildViolationDiagnostics(uri, parsed ?? {}, result.stdout));
        if (notify) {
            void vscode.window.showErrorMessage(
                'VeriSpec: counterexample found — the violated invariants are reported as diagnostics.'
            );
        }
        return;
    }
    if (status === 'ok') {
        diagnostics.delete(uri);
        if (notify) {
            void vscode.window.showInformationMessage(okMessage(bound));
        }
        return;
    }
    if (status === 'unknown') {
        diagnostics.delete(uri);
        const message = (parsed?.message ?? result.stdout ?? '').trim();
        if (notify) {
            void vscode.window.showWarningMessage(
                `VeriSpec: ${message || 'the solver could not decide within the bound.'}`
            );
        }
        return;
    }
    diagnostics.delete(uri);
    const detail = (result.stderr || result.stdout).trim();
    if (notify) {
        void vscode.window.showErrorMessage(
            `verispec check failed (exit ${result.code})${detail ? `: ${detail}` : ''}`
        );
    }
}

async function checkTrace(specUri: vscode.Uri): Promise<void> {
    const tracePath = await resolveTracePath();
    if (!tracePath) {
        return;
    }
    const result = await runCli(['check-trace', specUri.fsPath, tracePath]);
    if (result.spawnError) {
        void vscode.window.showErrorMessage(result.spawnError);
        return;
    }
    if (result.code === 0) {
        void vscode.window.showInformationMessage(
            result.stdout.trim() || 'The trace conforms to the spec.'
        );
        return;
    }
    const failing = firstFailingStep(result.stdout) ?? (result.stderr || result.stdout).trim();
    void vscode.window.showErrorMessage(`VeriSpec: the trace does not conform to the spec — ${failing}`);
}

async function generateBrief(uri: vscode.Uri): Promise<void> {
    const result = await runCli(['brief', uri.fsPath]);
    if (result.spawnError) {
        void vscode.window.showErrorMessage(result.spawnError);
        return;
    }
    if (result.code !== 0) {
        const detail = (result.stderr || result.stdout).trim();
        void vscode.window.showErrorMessage(
            `verispec brief failed (exit ${result.code})${detail ? `: ${detail}` : ''}`
        );
        return;
    }
    const content = result.stdout || '# No brief was produced.\n';
    const briefUri = vscode.Uri.parse(`${BRIEF_SCHEME}:${path.basename(uri.fsPath)}`);
    briefProvider.setContent(briefUri, content);
    const doc = await vscode.workspace.openTextDocument(briefUri);
    await vscode.languages.setTextDocumentLanguage(doc, 'markdown');
    await vscode.window.showTextDocument(doc, { preview: false, preserveFocus: false });
}

async function showReport(uri: vscode.Uri): Promise<void> {
    const outputFile = path.join(
        os.tmpdir(),
        `verispec-report-${sanitizeFileName(path.basename(uri.fsPath))}-${Date.now()}.html`
    );
    const result = await runCli(['report', uri.fsPath, '--output', outputFile]);
    if (result.spawnError) {
        void vscode.window.showErrorMessage(result.spawnError);
        return;
    }
    if (result.code !== 0) {
        const detail = (result.stderr || result.stdout).trim();
        void vscode.window.showErrorMessage(
            `verispec report failed (exit ${result.code})${detail ? `: ${detail}` : ''}`
        );
        return;
    }
    if (!fs.existsSync(outputFile)) {
        void vscode.window.showErrorMessage('verispec report did not produce an output file.');
        return;
    }
    openReportPanel(outputFile);
}

/* ------------------------------------------------------------------ */
/* Automatic inline diagnostics                                        */
/* ------------------------------------------------------------------ */

async function maybeAutoCheck(doc: vscode.TextDocument): Promise<void> {
    if (!isVeriSpecDocument(doc)) {
        return;
    }
    if (!showInlineDiagnosticsEnabled()) {
        return;
    }
    if (doc.uri.scheme !== 'file') {
        return;
    }
    if (!vscode.workspace.isTrusted) {
        return;
    }
    await checkSpec(doc.uri, false);
}

/* ------------------------------------------------------------------ */
/* Result parsing                                                      */
/* ------------------------------------------------------------------ */

function statusFromExitCode(code: number): string {
    if (code === 0) {
        return 'ok';
    }
    if (code === 1) {
        return 'violation';
    }
    return 'error';
}

function looksLikeUnsupportedJsonFlag(stderr: string): boolean {
    return /unrecognized arguments|invalid choice|unexpected argument/i.test(stderr || '');
}

function parseCheckResult(stdout: string): CheckResult | undefined {
    const text = (stdout || '').trim();
    if (!text) {
        return undefined;
    }
    const start = text.indexOf('{');
    const end = text.lastIndexOf('}');
    if (start < 0 || end <= start) {
        return undefined;
    }
    try {
        const value: unknown = JSON.parse(text.slice(start, end + 1));
        if (value && typeof value === 'object' && !Array.isArray(value)) {
            return value as CheckResult;
        }
    } catch {
        return undefined;
    }
    return undefined;
}

/** Best-effort parser for the human readable `verispec check` output. */
function parseCheckText(stdout: string): CheckResult | undefined {
    const text = (stdout || '').trim();
    if (!text) {
        return undefined;
    }
    const result: CheckResult = {};
    const counterexample = /COUNTEREXAMPLE found within bound (\d+): invariant\(s\) violated:\s*(.+)/i.exec(text);
    if (counterexample) {
        result.status = 'violation';
        result.bound = Number(counterexample[1]);
        result.violated = counterexample[2]
            .split(',')
            .map((name) => name.trim())
            .filter((name) => name.length > 0);
    } else if (/no violation up to bound/i.test(text)) {
        result.status = 'ok';
        result.message = text;
        return result;
    } else {
        return undefined;
    }

    const steps: any[] = [];
    const stepPattern = /^\s*step\s+(\d+):.*$/gm;
    let match: RegExpExecArray | null;
    while ((match = stepPattern.exec(text)) !== null) {
        steps.push({ step: Number(match[1]), raw: match[0].trim() });
    }
    result.trace = steps;
    return result;
}

function firstFailingStep(text: string): string | undefined {
    const match = /step\s+(\d+):\s*(.+)/.exec(text || '');
    if (!match) {
        return undefined;
    }
    return `step ${match[1]}: ${match[2].trim()}`;
}

function describeStep(step: any): string {
    if (step === null || step === undefined) {
        return 'counterexample step';
    }
    if (typeof step !== 'object') {
        return String(step);
    }
    if (typeof step.raw === 'string') {
        return step.raw;
    }
    const index = typeof step.step === 'number' ? step.step : '?';
    const action = typeof step.action === 'string' ? step.action : '(stutter)';
    const params = step.params === undefined || step.params === null ? '{}' : JSON.stringify(step.params);
    const state = step.state === undefined || step.state === null ? '{}' : JSON.stringify(step.state);
    return `step ${index}: action ${action} params=${params} state=${state}`;
}

/* ------------------------------------------------------------------ */
/* Diagnostics                                                         */
/* ------------------------------------------------------------------ */

function rangeFor(doc: vscode.TextDocument, pattern: RegExp): vscode.Range {
    const text = doc.getText();
    const match = pattern.exec(text);
    if (match && typeof match.index === 'number') {
        const line = doc.positionAt(match.index).line;
        return new vscode.Range(line, 0, line, 0);
    }
    return new vscode.Range(0, 0, 0, 0);
}

function buildViolationDiagnostics(
    uri: vscode.Uri,
    result: CheckResult,
    rawText: string
): vscode.Diagnostic[] {
    const doc = vscode.workspace.textDocuments.find((candidate) => candidate.uri.toString() === uri.toString());
    const range = doc ? rangeFor(doc, /^[ \t]*invariants[ \t]*:/m) : new vscode.Range(0, 0, 0, 0);
    const violated = result.violated ?? [];
    const headline =
        violated.length > 0
            ? `Invariant(s) violated: ${violated.join(', ')}`
            : `Invariant violation found within bound ${result.bound ?? '?'}`;

    const diagnostic = new vscode.Diagnostic(range, headline, vscode.DiagnosticSeverity.Error);
    diagnostic.source = DIAGNOSTIC_SOURCE;
    diagnostic.code = 'verispec.invariant-violation';

    const steps = Array.isArray(result.trace) ? result.trace : [];
    const related: vscode.DiagnosticRelatedInformation[] = [];
    for (const step of steps) {
        const description = describeStep(step);
        const info = new vscode.Diagnostic(range, description, vscode.DiagnosticSeverity.Information);
        info.source = DIAGNOSTIC_SOURCE;
        info.code = 'verispec.counterexample';
        related.push(new vscode.DiagnosticRelatedInformation(new vscode.Location(uri, range), description));
    }
    if (steps.length === 0) {
        const firstLine = (rawText || '').trim().split('\n')[0];
        if (firstLine) {
            related.push(new vscode.DiagnosticRelatedInformation(new vscode.Location(uri, range), firstLine));
        }
    }
    diagnostic.relatedInformation = related;
    return [diagnostic];
}

/* ------------------------------------------------------------------ */
/* Report webview                                                      */
/* ------------------------------------------------------------------ */

function sanitizeFileName(name: string): string {
    return name.replace(/[^A-Za-z0-9._-]+/g, '_');
}

function makeNonce(): string {
    const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let nonce = '';
    for (let index = 0; index < 32; index += 1) {
        nonce += alphabet.charAt(Math.floor(Math.random() * alphabet.length));
    }
    return nonce;
}

function openReportPanel(reportFile: string): void {
    const reportUri = vscode.Uri.file(reportFile);
    const panel = vscode.window.createWebviewPanel(
        'verispec.report',
        `VeriSpec report — ${path.basename(reportFile)}`,
        vscode.ViewColumn.Active,
        {
            enableScripts: true,
            localResourceRoots: [vscode.Uri.file(path.dirname(reportFile))]
        }
    );
    const nonce = makeNonce();
    const csp = [
        "default-src 'none'",
        `frame-src ${panel.webview.cspSource}`,
        "style-src 'unsafe-inline'",
        `script-src 'nonce-${nonce}'`
    ].join('; ');
    const frameSource = panel.webview.asWebviewUri(reportUri).toString();
    panel.webview.html = [
        '<!DOCTYPE html>',
        '<html lang="en">',
        '<head>',
        '<meta charset="UTF-8">',
        `<meta http-equiv="Content-Security-Policy" content="${csp}">`,
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        '<title>VeriSpec report</title>',
        '</head>',
        '<body style="margin:0;padding:0;font-family:system-ui,sans-serif;">',
        '<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;border-bottom:1px solid rgba(128,128,128,0.35);">',
        `<span style="font-size:12px;">Report: ${escapeHtml(path.basename(reportFile))}</span>`,
        '<button id="verispec-open-external" style="font-size:12px;padding:2px 8px;">Open in browser</button>',
        '</div>',
        `<iframe src="${frameSource}" title="VeriSpec report" style="width:100%;height:calc(100vh - 33px);border:0;"></iframe>`,
        `<script nonce="${nonce}">`,
        'const vscodeApi = acquireVsCodeApi();',
        "document.getElementById('verispec-open-external').addEventListener('click', () => {",
        "  vscodeApi.postMessage({ type: 'openExternal' });",
        '});',
        '</script>',
        '</body>',
        '</html>'
    ].join('\n');

    panel.webview.onDidReceiveMessage((message: unknown) => {
        if (message && (message as { type?: string }).type === 'openExternal') {
            void vscode.env.openExternal(reportUri);
        }
    });
}

function escapeHtml(value: string): string {
    return value
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/* ------------------------------------------------------------------ */
/* Misc helpers                                                        */
/* ------------------------------------------------------------------ */

async function resolveTracePath(): Promise<string | undefined> {
    const configured = (configuration().get<string>('traceFile', '') || '').trim();
    if (configured.length > 0) {
        if (!fs.existsSync(configured)) {
            void vscode.window.showErrorMessage(`VeriSpec: trace file not found: ${configured}`);
            return undefined;
        }
        return configured;
    }
    const active = vscode.window.activeTextEditor?.document;
    if (active && active.uri.scheme === 'file' && active.fileName.toLowerCase().endsWith('.json')) {
        return active.fileName;
    }
    const picked = await vscode.window.showOpenDialog({
        canSelectMany: false,
        filters: { JSON: ['json'] },
        openLabel: 'Check trace'
    });
    if (picked && picked.length > 0) {
        return picked[0].fsPath;
    }
    return undefined;
}

function log(message: string): void {
    if (output) {
        output.appendLine(`[${new Date().toISOString()}] ${message}`);
    }
}
