import * as vscode from 'vscode';
import { HeliosDBClient, Branch } from '../client';

export class BranchesTreeProvider implements vscode.TreeDataProvider<BranchItem> {
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

    getTreeItem(element: BranchItem): vscode.TreeItem {
        return element;
    }

    async getChildren(): Promise<BranchItem[]> {
        if (!this.client) {
            return [];
        }

        try {
            const branches = await this.client.listBranches();
            const currentBranch = this.client.getCurrentBranch();
            return branches.map(b => new BranchItem(b, b.name === currentBranch));
        } catch {
            return [];
        }
    }
}

class BranchItem extends vscode.TreeItem {
    constructor(branch: Branch, isCurrent: boolean) {
        super(branch.name, vscode.TreeItemCollapsibleState.None);
        this.description = isCurrent ? '(current)' : (branch.parent || '');
        this.tooltip = `Branch: ${branch.name}${branch.parent ? ` (from ${branch.parent})` : ''}\nCreated: ${branch.createdAt}`;
        this.iconPath = new vscode.ThemeIcon(
            isCurrent ? 'git-branch' : 'git-commit'
        );
        this.contextValue = 'branch';

        if (!isCurrent) {
            this.command = {
                command: 'heliosdb.switchBranch',
                title: 'Switch to Branch',
                arguments: [branch.name]
            };
        }
    }
}
