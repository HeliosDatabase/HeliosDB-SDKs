import * as vscode from 'vscode';
import { HeliosDBClient, VectorSearchResult } from '../client';

export class VectorSearchPanel {
    private static currentPanel: vscode.WebviewPanel | undefined;

    /**
     * Show the vector search panel for a given store
     */
    static show(extensionUri: vscode.Uri, client: HeliosDBClient, storeName: string): void {
        const column = vscode.ViewColumn.Beside;

        if (VectorSearchPanel.currentPanel) {
            VectorSearchPanel.currentPanel.reveal(column);
            VectorSearchPanel.currentPanel.webview.html = VectorSearchPanel.buildHtml(storeName);
            VectorSearchPanel.setupMessageHandler(VectorSearchPanel.currentPanel, client, storeName);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            'heliosdbVectorSearch',
            `Vector Search: ${storeName}`,
            column,
            { enableScripts: true, retainContextWhenHidden: true }
        );

        panel.webview.html = VectorSearchPanel.buildHtml(storeName);
        VectorSearchPanel.setupMessageHandler(panel, client, storeName);

        panel.onDidDispose(() => {
            VectorSearchPanel.currentPanel = undefined;
        });

        VectorSearchPanel.currentPanel = panel;
    }

    private static setupMessageHandler(
        panel: vscode.WebviewPanel,
        client: HeliosDBClient,
        storeName: string
    ): void {
        panel.webview.onDidReceiveMessage(async (message: { command: string; query?: string; topK?: number }) => {
            if (message.command === 'search') {
                const query = message.query || '';
                const topK = message.topK || 10;

                try {
                    const results = await client.vectorSearch(storeName, query, topK);
                    panel.webview.postMessage({ command: 'results', data: results });
                } catch (error) {
                    panel.webview.postMessage({ command: 'error', message: String(error) });
                }
            }
        });
    }

    private static buildHtml(storeName: string): string {
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
            margin: 0 0 16px 0;
            color: var(--vscode-textLink-foreground);
        }
        .search-form {
            display: flex;
            gap: 8px;
            margin-bottom: 16px;
            align-items: flex-end;
        }
        .field {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .field label {
            font-size: 11px;
            color: var(--vscode-descriptionForeground);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        input[type="text"], input[type="number"] {
            background: var(--vscode-input-background);
            color: var(--vscode-input-foreground);
            border: 1px solid var(--vscode-input-border);
            padding: 6px 10px;
            border-radius: 3px;
            font-size: 13px;
            outline: none;
        }
        input[type="text"] {
            flex: 1;
            min-width: 200px;
        }
        input[type="number"] {
            width: 60px;
        }
        input:focus {
            border-color: var(--vscode-focusBorder);
        }
        button {
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
            border: none;
            padding: 6px 16px;
            border-radius: 3px;
            cursor: pointer;
            font-size: 13px;
            white-space: nowrap;
        }
        button:hover {
            background: var(--vscode-button-hoverBackground);
        }
        .result {
            border: 1px solid var(--vscode-widget-border);
            border-radius: 4px;
            padding: 10px 14px;
            margin-bottom: 8px;
        }
        .result-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 6px;
        }
        .result-id {
            font-weight: bold;
            font-size: 13px;
        }
        .result-score {
            color: var(--vscode-textLink-foreground);
            font-size: 12px;
        }
        .result-content {
            font-size: 13px;
            margin-bottom: 6px;
            line-height: 1.4;
        }
        .result-metadata {
            font-size: 11px;
            color: var(--vscode-descriptionForeground);
            font-family: var(--vscode-editor-font-family);
        }
        .loading {
            text-align: center;
            padding: 20px;
            color: var(--vscode-descriptionForeground);
        }
        .error {
            color: var(--vscode-errorForeground);
            padding: 10px;
            border: 1px solid var(--vscode-inputValidation-errorBorder);
            border-radius: 4px;
            margin-bottom: 12px;
        }
        .empty {
            text-align: center;
            padding: 40px;
            color: var(--vscode-descriptionForeground);
        }
        #results { margin-top: 4px; }
    </style>
</head>
<body>
    <h2>Vector Search: ${escapeHtmlInTemplate(storeName)}</h2>
    <div class="search-form">
        <div class="field" style="flex:1">
            <label>Query text</label>
            <input type="text" id="query" placeholder="Enter search query..." />
        </div>
        <div class="field">
            <label>Top K</label>
            <input type="number" id="topK" value="10" min="1" max="100" />
        </div>
        <button id="searchBtn">Search</button>
    </div>
    <div id="results"></div>

    <script>
        const vscode = acquireVsCodeApi();
        const queryInput = document.getElementById('query');
        const topKInput = document.getElementById('topK');
        const searchBtn = document.getElementById('searchBtn');
        const resultsDiv = document.getElementById('results');

        searchBtn.addEventListener('click', doSearch);
        queryInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') doSearch();
        });

        function doSearch() {
            const query = queryInput.value.trim();
            if (!query) return;
            resultsDiv.innerHTML = '<div class="loading">Searching...</div>';
            vscode.postMessage({
                command: 'search',
                query: query,
                topK: parseInt(topKInput.value) || 10
            });
        }

        window.addEventListener('message', function(event) {
            const msg = event.data;
            if (msg.command === 'results') {
                renderResults(msg.data);
            } else if (msg.command === 'error') {
                resultsDiv.innerHTML = '<div class="error">' + escapeHtml(msg.message) + '</div>';
            }
        });

        function renderResults(results) {
            if (!results || results.length === 0) {
                resultsDiv.innerHTML = '<div class="empty">No results found</div>';
                return;
            }
            resultsDiv.innerHTML = results.map(function(r) {
                return '<div class="result">'
                    + '<div class="result-header">'
                    + '<span class="result-id">' + escapeHtml(r.id || '') + '</span>'
                    + '<span class="result-score">Score: ' + (r.score != null ? r.score.toFixed(4) : 'N/A') + '</span>'
                    + '</div>'
                    + (r.content ? '<div class="result-content">' + escapeHtml(r.content) + '</div>' : '')
                    + (r.metadata ? '<div class="result-metadata">' + escapeHtml(JSON.stringify(r.metadata)) + '</div>' : '')
                    + '</div>';
            }).join('');
        }

        function escapeHtml(text) {
            var div = document.createElement('div');
            div.appendChild(document.createTextNode(text));
            return div.innerHTML;
        }
    </script>
</body>
</html>`;
    }
}

function escapeHtmlInTemplate(text: string): string {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
