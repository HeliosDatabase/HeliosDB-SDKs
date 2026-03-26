# HeliosDB SDKs & Integrations

Official client SDKs and third-party integrations for [HeliosDB](https://github.com/Dimensigon) — shared across all editions (Nano, Lite, Full).

All SDKs and integrations connect via the **HeliosDB REST API** (`/v1/...`), which is identical across editions.

## SDKs

| Language | Path | Features |
|----------|------|----------|
| **Go** | [`sdks/go/`](sdks/go/) | Branches, vector search, agent memory, time-travel |
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

## HeliosDB Editions

| Edition | Repository | Description |
|---------|-----------|-------------|
| **Nano** | [HDB-HeliosDB-Nano](https://github.com/Dimensigon/HDB-HeliosDB-Nano) | Embedded/single-user, PostgreSQL-compatible |
| **Lite** | [HDB-HeliosDB-Lite](https://github.com/dimensigon/HDB-HeliosDB-Lite) | Extended with gRPC and HA scaffolding |
| **Full** | Coming soon | Enterprise distributed |

## License

AGPL-3.0-only — see [LICENSE](LICENSE).
