import * as vscode from 'vscode';
import { HeliosDBClient, VectorStore } from '../client';

export class VectorStoresTreeProvider implements vscode.TreeDataProvider<VectorStoreItem> {
    private client: HeliosDBClient | undefined;
    private _onDidChangeTreeData = new vscode.EventEmitter<void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    setClient(client: HeliosDBClient): void {
        this.client = client;
        this.refresh();
    }

    refresh(): void {
        this._onDidChangeTreeData.fire();
    }

    getTreeItem(element: VectorStoreItem): vscode.TreeItem {
        return element;
    }

    async getChildren(): Promise<VectorStoreItem[]> {
        if (!this.client) {
            return [];
        }

        try {
            const stores = await this.client.listVectorStores();
            return stores.map(s => new VectorStoreItem(s));
        } catch {
            return [];
        }
    }
}

class VectorStoreItem extends vscode.TreeItem {
    constructor(store: VectorStore) {
        super(store.name, vscode.TreeItemCollapsibleState.None);
        this.description = `${store.dimensions}d, ${store.metric}, ${store.count} vectors`;
        this.tooltip = `Vector Store: ${store.name}\nDimensions: ${store.dimensions}\nMetric: ${store.metric}\nVector count: ${store.count}`;
        this.iconPath = new vscode.ThemeIcon('pulse');
        this.contextValue = 'vectorStore';

        this.command = {
            command: 'heliosdb.vectorSearch',
            title: 'Search',
            arguments: [store.name]
        };
    }
}
