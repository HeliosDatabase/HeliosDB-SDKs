import * as vscode from 'vscode';

interface QueryRecord {
    sql: string;
    durationMs: number;
    timestamp: Date;
}

export class HistoryTreeProvider implements vscode.TreeDataProvider<HistoryItem> {
    private static readonly MAX_ENTRIES = 50;

    private queries: QueryRecord[] = [];
    private _onDidChangeTreeData = new vscode.EventEmitter<void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    /**
     * Record a query execution in history
     */
    addQuery(sql: string, durationMs: number): void {
        this.queries.unshift({
            sql: sql.trim(),
            durationMs,
            timestamp: new Date()
        });

        // Keep only the most recent entries
        if (this.queries.length > HistoryTreeProvider.MAX_ENTRIES) {
            this.queries = this.queries.slice(0, HistoryTreeProvider.MAX_ENTRIES);
        }

        this._onDidChangeTreeData.fire();
    }

    /**
     * Clear all history entries
     */
    clear(): void {
        this.queries = [];
        this._onDidChangeTreeData.fire();
    }

    getTreeItem(element: HistoryItem): vscode.TreeItem {
        return element;
    }

    getChildren(): HistoryItem[] {
        return this.queries.map(q => new HistoryItem(q));
    }
}

class HistoryItem extends vscode.TreeItem {
    constructor(record: QueryRecord) {
        // Show first line of SQL as label, truncated to 60 chars
        const firstLine = record.sql.split('\n')[0] || record.sql;
        const label = firstLine.length > 60 ? firstLine.substring(0, 57) + '...' : firstLine;

        super(label, vscode.TreeItemCollapsibleState.None);

        const time = record.timestamp.toLocaleTimeString();
        this.description = `${record.durationMs}ms - ${time}`;
        this.tooltip = `${record.sql}\n\nDuration: ${record.durationMs}ms\nTime: ${record.timestamp.toLocaleString()}`;
        this.iconPath = new vscode.ThemeIcon('history');
        this.contextValue = 'historyEntry';

        // Clicking an entry opens it in the active editor
        this.command = {
            command: 'heliosdb.executeQuery',
            title: 'Re-run Query',
            arguments: [record.sql]
        };
    }
}
