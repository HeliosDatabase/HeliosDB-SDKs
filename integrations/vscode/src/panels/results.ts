import * as vscode from 'vscode';
import { QueryResult, ExplainResult } from '../client';

export class QueryResultsPanel {
    private static currentPanel: vscode.WebviewPanel | undefined;

    /**
     * Show query results in a webview panel
     */
    static show(extensionUri: vscode.Uri, result: QueryResult, durationMs: number): void {
        const column = vscode.ViewColumn.Beside;

        if (QueryResultsPanel.currentPanel) {
            QueryResultsPanel.currentPanel.reveal(column);
            QueryResultsPanel.currentPanel.webview.html = QueryResultsPanel.buildResultsHtml(result, durationMs);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            'heliosdbResults',
            'HeliosDB Results',
            column,
            { enableScripts: true, retainContextWhenHidden: true }
        );

        panel.webview.html = QueryResultsPanel.buildResultsHtml(result, durationMs);

        panel.onDidDispose(() => {
            QueryResultsPanel.currentPanel = undefined;
        });

        QueryResultsPanel.currentPanel = panel;
    }

    /**
     * Show an EXPLAIN query plan in a webview panel
     */
    static showExplain(extensionUri: vscode.Uri, result: ExplainResult): void {
        const column = vscode.ViewColumn.Beside;

        if (QueryResultsPanel.currentPanel) {
            QueryResultsPanel.currentPanel.reveal(column);
            QueryResultsPanel.currentPanel.webview.html = QueryResultsPanel.buildExplainHtml(result);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            'heliosdbResults',
            'HeliosDB - Query Plan',
            column,
            { enableScripts: true, retainContextWhenHidden: true }
        );

        panel.webview.html = QueryResultsPanel.buildExplainHtml(result);

        panel.onDidDispose(() => {
            QueryResultsPanel.currentPanel = undefined;
        });

        QueryResultsPanel.currentPanel = panel;
    }

    private static buildResultsHtml(result: QueryResult, durationMs: number): string {
        const columns = result.columns || [];
        const rows = result.rows || [];

        const headerCells = columns.map(c => `<th>${escapeHtml(String(c))}</th>`).join('');
        const bodyRows = rows.map(row => {
            const cells = (Array.isArray(row) ? row : Object.values(row as Record<string, unknown>))
                .map(val => `<td>${escapeHtml(formatValue(val))}</td>`)
                .join('');
            return `<tr>${cells}</tr>`;
        }).join('');

        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: var(--vscode-font-family);
            font-size: var(--vscode-font-size);
            color: var(--vscode-foreground);
            padding: 12px;
            margin: 0;
        }
        .stats {
            margin-bottom: 12px;
            color: var(--vscode-descriptionForeground);
            font-size: 12px;
        }
        .stats span {
            margin-right: 16px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th {
            background: var(--vscode-editor-selectionBackground);
            color: var(--vscode-editor-foreground);
            padding: 6px 10px;
            text-align: left;
            border-bottom: 2px solid var(--vscode-widget-border);
            position: sticky;
            top: 0;
            white-space: nowrap;
        }
        td {
            padding: 4px 10px;
            border-bottom: 1px solid var(--vscode-widget-border);
            max-width: 300px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        tr:hover td {
            background: var(--vscode-list-hoverBackground);
        }
        .null-value {
            color: var(--vscode-descriptionForeground);
            font-style: italic;
        }
        .empty {
            text-align: center;
            padding: 40px;
            color: var(--vscode-descriptionForeground);
        }
    </style>
</head>
<body>
    <div class="stats">
        <span>${result.rowCount} row${result.rowCount !== 1 ? 's' : ''}</span>
        <span>${durationMs}ms</span>
        <span>${columns.length} column${columns.length !== 1 ? 's' : ''}</span>
    </div>
    ${rows.length > 0 ? `
    <table>
        <thead><tr>${headerCells}</tr></thead>
        <tbody>${bodyRows}</tbody>
    </table>
    ` : '<div class="empty">No rows returned</div>'}
</body>
</html>`;
    }

    private static buildExplainHtml(result: ExplainResult): string {
        const optimizations = (result.optimizations || [])
            .map(o => `<li>${escapeHtml(o)}</li>`)
            .join('');

        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: var(--vscode-font-family);
            font-size: var(--vscode-font-size);
            color: var(--vscode-foreground);
            padding: 16px;
            margin: 0;
        }
        h2 {
            font-size: 16px;
            margin: 0 0 12px 0;
            color: var(--vscode-textLink-foreground);
        }
        .plan {
            background: var(--vscode-textCodeBlock-background);
            padding: 12px;
            border-radius: 4px;
            font-family: var(--vscode-editor-font-family);
            font-size: 13px;
            white-space: pre-wrap;
            margin-bottom: 16px;
            border: 1px solid var(--vscode-widget-border);
        }
        .costs {
            margin-bottom: 16px;
            font-size: 13px;
        }
        .costs span {
            margin-right: 20px;
        }
        ul {
            padding-left: 20px;
            font-size: 13px;
        }
        li {
            margin-bottom: 4px;
        }
    </style>
</head>
<body>
    <h2>Query Plan</h2>
    <div class="plan">${escapeHtml(result.plan || 'No plan available')}</div>
    <div class="costs">
        <span>Estimated rows: ${result.costs?.rows ?? 'N/A'}</span>
        <span>Cost: ${result.costs?.cost ?? 'N/A'}</span>
    </div>
    ${optimizations ? `<h2>Optimizations</h2><ul>${optimizations}</ul>` : ''}
</body>
</html>`;
    }
}

function escapeHtml(text: string): string {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function formatValue(val: unknown): string {
    if (val === null || val === undefined) {
        return '<span class="null-value">NULL</span>';
    }
    if (typeof val === 'object') {
        return JSON.stringify(val);
    }
    return String(val);
}
