import * as vscode from 'vscode';

interface ConnectionConfig {
    name: string;
    url: string;
    connected: boolean;
}

export class ConnectionTreeProvider implements vscode.TreeDataProvider<ConnectionItem> {
    private connections: ConnectionConfig[] = [];
    private _onDidChangeTreeData = new vscode.EventEmitter<void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    constructor(private context: vscode.ExtensionContext) {
        // Load saved connections from workspace configuration
        const savedConnections = vscode.workspace
            .getConfiguration('heliosdb')
            .get<Array<{ name: string; url: string }>>('connections', []);

        this.connections = savedConnections.map(c => ({
            name: c.name,
            url: c.url,
            connected: false
        }));
    }

    addConnection(name: string, url: string): void {
        this.connections.push({ name, url, connected: false });
        this._onDidChangeTreeData.fire();
    }

    removeConnection(name: string): void {
        this.connections = this.connections.filter(c => c.name !== name);
        this._onDidChangeTreeData.fire();
    }

    setConnected(name: string, connected: boolean): void {
        const conn = this.connections.find(c => c.name === name);
        if (conn) {
            conn.connected = connected;
            this._onDidChangeTreeData.fire();
        }
    }

    refresh(): void {
        this._onDidChangeTreeData.fire();
    }

    getTreeItem(element: ConnectionItem): vscode.TreeItem {
        return element;
    }

    getChildren(): ConnectionItem[] {
        return this.connections.map(c => new ConnectionItem(c.name, c.url, c.connected));
    }
}

class ConnectionItem extends vscode.TreeItem {
    constructor(name: string, url: string, connected: boolean) {
        super(name, vscode.TreeItemCollapsibleState.None);
        this.description = url;
        this.tooltip = `${name} - ${url} (${connected ? 'Connected' : 'Disconnected'})`;
        this.iconPath = new vscode.ThemeIcon(connected ? 'database' : 'circle-outline');
        this.contextValue = connected ? 'connectedConnection' : 'disconnectedConnection';
    }
}
