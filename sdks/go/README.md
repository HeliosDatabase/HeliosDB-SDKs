# HeliosDB Go client

Go client for the HeliosDB REST API (`/v1/...`): SQL queries, branches,
vector search, agent memory and time-travel queries. It works with every
HeliosDB edition that exposes the REST API (Nano, Lite, Full).

For plain SQL over the PostgreSQL wire protocol you can also use any standard
Go PostgreSQL driver, such as [pgx](https://github.com/jackc/pgx).

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
