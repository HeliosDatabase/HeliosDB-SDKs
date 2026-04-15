import * as vscode from 'vscode';
import { HeliosDBClient, Table, Column } from '../client';

type ExplorerItem = TableItem | ColumnItem;

export class ExplorerTreeProvider implements vscode.TreeDataProvider<ExplorerItem> {
    private client: HeliosDBClient | undefined;
    private tables: Table[] = [];
    private _onDidChangeTreeData = new vscode.EventEmitter<ExplorerItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    setClient(client: HeliosDBClient): void {
        this.client = client;
        this.refresh();
    }

    refresh(): void {
        this._onDidChangeTreeData.fire(undefined);
    }

    getTreeItem(element: ExplorerItem): vscode.TreeItem {
        return element;
    }

    async getChildren(element?: ExplorerItem): Promise<ExplorerItem[]> {
        if (!this.client) {
            return [];
        }

        // Root level: list tables
        if (!element) {
            try {
                this.tables = await this.client.listTables();
                return this.tables.map(t => new TableItem(t));
            } catch {
                return [];
            }
        }

        // Table level: list columns
        if (element instanceof TableItem) {
            return element.table.columns.map(c => new ColumnItem(c));
        }

        return [];
    }
}

class TableItem extends vscode.TreeItem {
    constructor(public readonly table: Table) {
        super(table.name, vscode.TreeItemCollapsibleState.Collapsed);
        this.description = table.rowCount !== undefined ? `${table.rowCount} rows` : '';
        this.tooltip = `Table: ${table.name}`;
        this.iconPath = new vscode.ThemeIcon('table');
        this.contextValue = 'table';
    }
}

class ColumnItem extends vscode.TreeItem {
    constructor(column: Column) {
        super(column.name, vscode.TreeItemCollapsibleState.None);
        const nullable = column.nullable ? ', nullable' : ', not null';
        const pk = column.primaryKey ? ' (PK)' : '';
        this.description = `${column.dataType}${nullable}${pk}`;
        this.tooltip = `${column.name}: ${column.dataType}${nullable}${pk}`;
        this.iconPath = new vscode.ThemeIcon(
            column.primaryKey ? 'key' : 'symbol-field'
        );
        this.contextValue = 'column';
    }
}
