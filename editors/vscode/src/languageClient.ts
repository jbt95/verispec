import { ExtensionContext, workspace } from 'vscode';
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
    TransportKind
} from 'vscode-languageclient/node';

let client: LanguageClient | undefined;

function cliPath(): string {
    const configured = workspace.getConfiguration('verispec').get<string>('cliPath', 'verispec');
    const trimmed = (configured || '').trim();
    return trimmed.length > 0 ? trimmed : 'verispec';
}

export function startLanguageClient(context: ExtensionContext): void {
    if (client) {
        return;
    }
    const command = cliPath();
    const serverOptions: ServerOptions = {
        command,
        args: ['lsp'],
        transport: TransportKind.stdio
    };
    const clientOptions: LanguageClientOptions = {
        documentSelector: [
            { language: 'verispec', scheme: 'file' },
            { pattern: '**/*.verispec.yaml', scheme: 'file' },
            { pattern: '**/*.verispec.yml', scheme: 'file' }
        ],
        synchronize: {
            fileEvents: workspace.createFileSystemWatcher('**/*.{verispec.yaml,verispec.yml}')
        }
    };
    client = new LanguageClient('verispec', 'VeriSpec', serverOptions, clientOptions);
    context.subscriptions.push(client);
    void client.start();
}

export async function stopLanguageClient(): Promise<void> {
    if (!client) {
        return;
    }
    await client.stop();
    client = undefined;
}
