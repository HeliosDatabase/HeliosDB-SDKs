import * as vscode from 'vscode';
import { HeliosDBClient } from '../client';

export class NLQueryPanel {
    private static currentPanel: vscode.WebviewPanel | undefined;

    /**
     * Show the natural language query panel
     */
    static show(extensionUri: vscode.Uri, client: HeliosDBClient): void {
        const column = vscode.ViewColumn.Beside;

        if (NLQueryPanel.currentPanel) {
            NLQueryPanel.currentPanel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            'heliosdbNLQuery',
            'HeliosDB - Natural Language Query',
            column,
            { enableScripts: true, retainContextWhenHidden: true }
        );

        panel.webview.html = NLQueryPanel.buildHtml();
        NLQueryPanel.setupMessageHandler(panel, client);

        panel.onDidDispose(() => {
            NLQueryPanel.currentPanel = undefined;
        });

        NLQueryPanel.currentPanel = panel;
    }

    private static setupMessageHandler(
        panel: vscode.WebviewPanel,
        client: HeliosDBClient
    ): void {
        panel.webview.onDidReceiveMessage(async (message: { command: string; question?: string }) => {
            if (message.command === 'query') {
                const question = message.question || '';

                try {
                    const result = await client.nlQuery(question);
                    panel.webview.postMessage({
                        command: 'result',
                        sql: result.sql,
                        data: result.result
                    });
                } catch (error) {
                    panel.webview.postMessage({ command: 'error', message: String(error) });
                }
            }
        });
    }

    private static buildHtml(): string {
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
            margin: 0 0 8px 0;
            color: var(--vscode-textLink-foreground);
        }
        .subtitle {
            color: var(--vscode-descriptionForeground);
            font-size: 12px;
            margin-bottom: 16px;
        }
        .input-row {
            display: flex;
            gap: 8px;
            margin-bottom: 16px;
        }
        input[type="text"] {
            flex: 1;
            background: var(--vscode-input-background);
            color: var(--vscode-input-foreground);
            border: 1px solid var(--vscode-input-border);
            padding: 8px 12px;
            border-radius: 3px;
            font-size: 13px;
            outline: none;
        }
        input:focus {
            border-color: var(--vscode-focusBorder);
        }
        button {
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
            border: none;
            padding: 8px 20px;
            border-radius: 3px;
            cursor: pointer;
            font-size: 13px;
            white-space: nowrap;
        }
        button:hover {
            background: var(--vscode-button-hoverBackground);
        }
        .generated-sql {
            background: var(--vscode-textCodeBlock-background);
            border: 1px solid var(--vscode-widget-border);
            border-radius: 4px;
            padding: 10px 14px;
            margin-bottom: 16px;
            font-family: var(--vscode-editor-font-family);
            font-size: 13px;
            white-space: pre-wrap;
        }
        .sql-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--vscode-descriptionForeground);
            margin-bottom: 6px;
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
            max-width: 250px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        tr:hover td {
            background: var(--vscode-list-hoverBackground);
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
            padding: 30px;
            color: var(--vscode-descriptionForeground);
        }
        #output { margin-top: 4px; }
    </style>
</head>
<body>
    <h2>Natural Language Query</h2>
    <div class="subtitle">Ask a question in plain English and HeliosDB will generate and execute the SQL.</div>
    <div class="input-row">
        <input type="text" id="question" placeholder="e.g., Show me the top 10 users by order count..." />
        <button id="askBtn">Ask</button>
    </div>
    <div id="output"></div>

    <script>
        const vscode = acquireVsCodeApi();
        const questionInput = document.getElementById('question');
        const askBtn = document.getElementById('askBtn');
        const outputDiv = document.getElementById('output');

        askBtn.addEventListener('click', doQuery);
        questionInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') doQuery();
        });

        function doQuery() {
            const question = questionInput.value.trim();
            if (!question) return;
            outputDiv.innerHTML = '<div class="loading">Thinking...</div>';
            vscode.postMessage({ command: 'query', question: question });
        }

        window.addEventListener('message', function(event) {
            var msg = event.data;
            if (msg.command === 'result') {
                renderResult(msg.sql, msg.data);
            } else if (msg.command === 'error') {
                outputDiv.innerHTML = '<div class="error">' + escapeHtml(msg.message) + '</div>';
            }
        });

        function renderResult(sql, data) {
            var html = '';

            // Show generated SQL
            if (sql) {
                html += '<div class="sql-label">Generated SQL</div>';
                html += '<div class="generated-sql">' + escapeHtml(sql) + '</div>';
            }

            // Show result table
            var columns = data.columns || [];
            var rows = data.rows || [];

            if (columns.length === 0 || rows.length === 0) {
                html += '<div class="empty">No results</div>';
                outputDiv.innerHTML = html;
                return;
            }

            html += '<table><thead><tr>';
            columns.forEach(function(c) {
                html += '<th>' + escapeHtml(String(c)) + '</th>';
            });
            html += '</tr></thead><tbody>';

            rows.forEach(function(row) {
                html += '<tr>';
                var cells = Array.isArray(row) ? row : Object.values(row);
                cells.forEach(function(val) {
                    var display = val === null || val === undefined ? 'NULL' : String(val);
                    html += '<td>' + escapeHtml(display) + '</td>';
                });
                html += '</tr>';
            });

            html += '</tbody></table>';
            outputDiv.innerHTML = html;
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
