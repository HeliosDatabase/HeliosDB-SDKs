# HeliosDB SDKs & Integrations

Official client SDKs and third-party integrations for [HeliosDB](https://heliosdb.com) — shared across all editions (Nano, Lite, Full).

All SDKs and integrations connect via the **HeliosDB REST API** (`/v1/...`), which is identical across editions.

## SDKs

| Language | Path | Features |
|----------|------|----------|
| **Go** | [`sdks/go/`](sdks/go/) | REST client for Nano and Lite (branches, vector search, agent memory, time-travel); not Full — `go get github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go@latest` |
| **Go (WASM procedures)** | [`sdks/go/wasm/`](sdks/go/wasm/) | Write HeliosDB Full WASM procedures in Go, compile with TinyGo (the Full server does not run WASM procedures yet) — `go get github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm@latest` |
| **Python** | [`sdks/python/`](sdks/python/) | Full-featured: vectors, agents, branches, LangChain + LlamaIndex integrations |
| **Python (SQLite compat)** | [`sdks/python-sqlite/`](sdks/python-sqlite/) | DB-API 2.0 drop-in replacement for `sqlite3` module |
| **Rust** | [`sdks/rust/`](sdks/rust/) | HTTP client for remote HeliosDB servers (for embedded use, use `heliosdb-nano` crate directly) |
| **TypeScript** | [`sdks/typescript/`](sdks/typescript/) | Type-safe client with fluent query builder |

## Integrations

| Platform | Path | Description |
|----------|------|-------------|
| **VS Code** | [`integrations/vscode/`](integrations/vscode/) | SQL editor, branch explorer, vector search panel, NL queries |
| **Zapier** | [`integrations/zapier/`](integrations/zapier/) | Triggers and actions for workflow automation |
| **n8n** | [`integrations/n8n/`](integrations/n8n/) | Community node with query, vector, branch, and agent operations |
| **Retool** | [`integrations/retool/`](integrations/retool/) | REST API datasource configuration |
| **Make** | [`integrations/make/`](integrations/make/) | Module definition for Make.com (Integromat) |
| **AutoGen** | [`integrations/autogen/`](integrations/autogen/) | Microsoft AutoGen agent integration |

## Installing

The Go modules are fetched directly from this repository with `go get` (tags
`sdks/go/vX.Y.Z` and `sdks/go/wasm/vX.Y.Z`). The Python, TypeScript and Rust SDKs and the integrations
are not yet published to PyPI, npm or crates.io; install them from source, for
example:

```bash
pip install "git+https://github.com/HeliosDatabase/HeliosDB-SDKs.git#subdirectory=sdks/python"
```

See each SDK's README for details.

## HeliosDB Editions

| Edition | Where to find it | Description |
|---------|------------------|-------------|
| **Nano** | [HeliosDatabase/HeliosDB-Nano](https://github.com/HeliosDatabase/HeliosDB-Nano) (Apache-2.0) | Embedded / single-node, PostgreSQL-compatible |
| **Lite** | [Documentation](https://heliosdb.com/docs/lite/) | Self-hosted PostgreSQL-compatible edition |
| **Full** | [Documentation](https://heliosdb.com/docs/full/) | Distributed enterprise edition |

Related: [HeliosDB-Proxy](https://github.com/HeliosDatabase/HeliosDB-Proxy) (PostgreSQL-wire proxy, Apache-2.0).

## Support

- Issues: <https://github.com/HeliosDatabase/HeliosDB-SDKs/issues>
- Discord: <https://discord.gg/yTykuUrFXc>
- Email: support@heliosdb.com

## License

Apache-2.0 — see [LICENSE](LICENSE).
