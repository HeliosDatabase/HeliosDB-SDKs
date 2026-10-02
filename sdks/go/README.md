# HeliosDB Go client

Go client for the HeliosDB REST API (`/v1/...`): SQL queries, branches,
vector search, agent memory and time-travel queries. It works with HeliosDB
Nano and HeliosDB Lite, which serve these `/v1/...` endpoints.

It does **not** work with HeliosDB Full. Full's HTTP API is served under
`/api/v1/...` and has no branch endpoints, so every call from this client
returns 404. For Full, use a standard Go PostgreSQL driver such as
[pgx](https://github.com/jackc/pgx) over the PostgreSQL wire protocol (port
5432 by default). You can do the same with Nano and Lite for plain SQL.

To write HeliosDB Full stored procedures in Go (compiled to WebAssembly), see
the separate module in [`wasm/`](wasm/).

## Install

```bash
go get github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go@latest
```

The module lives in a subdirectory of the
[HeliosDB-SDKs](https://github.com/HeliosDatabase/HeliosDB-SDKs) repository and
is versioned with tags of the form `sdks/go/vX.Y.Z`. Request a specific
release with `@vX.Y.Z` (for example `@v0.1.0`).

## Usage

The package name is `heliosdb`; an explicit import alias keeps that clear:

```go
package main

import (
	"context"
	"fmt"
	"log"

	heliosdb "github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go"
)

func main() {
	client, err := heliosdb.NewClient("http://localhost:8080",
		heliosdb.WithAPIKey("your-api-key"),
		heliosdb.WithBranch("main"),
	)
	if err != nil {
		log.Fatal(err)
	}

	ctx := context.Background()
	res, err := client.Query(ctx, "SELECT * FROM users WHERE id = $1", 1)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(res.Rows)

	hits, err := client.VectorSearch(ctx, "documents", "hello world",
		heliosdb.WithTopK(10),
		heliosdb.WithMinScore(0.7),
	)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(hits)
}
```

## Development

```bash
cd sdks/go
go build ./...
go test ./...
```

## License

Apache-2.0 — see [LICENSE](../../LICENSE).
