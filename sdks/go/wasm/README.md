# HeliosDB Go WASM procedure SDK

Write HeliosDB Full stored procedures in Go, compile them to WebAssembly with
[TinyGo](https://tinygo.org), and test them natively with `go test`.

```bash
go get github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm@latest
```

The module lives in `sdks/go/wasm` of
[HeliosDB-SDKs](https://github.com/HeliosDatabase/HeliosDB-SDKs) and is
versioned with tags of the form `sdks/go/wasm/vX.Y.Z`. It has no
dependencies.

| Package | Purpose |
|---------|---------|
| `github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdb` | Host functions: SQL, transactions, key-value store, logging, hashing, random bytes |
| `github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdbtest` | In-memory fake host for unit tests |

## Write a procedure

A procedure is an exported function whose parameters and results are WASM
scalars (`int32`, `int64`, `float32`, `float64`). Export it with TinyGo's
`//export` directive:

```go
package main

import "github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdb"

//export apply_discount
func applyDiscount(priceCents int64, percent int32) int64 {
	return priceCents - priceCents*int64(percent)/100
}

//export record_order
func recordOrder(customerID, amountCents int64) int32 {
	err := heliosdb.WithTx(func(heliosdb.Tx) error {
		return heliosdb.Exec("INSERT INTO orders (customer_id, amount_cents) VALUES (" +
			heliosdb.Int64(customerID) + ", " + heliosdb.Int64(amountCents) + ")")
	})
	if err != nil {
		heliosdb.LogError("record_order: " + err.Error())
		return -1
	}
	return 0
}

func main() {}
```

A complete example with tests is in [`examples/orders`](examples/orders).

## Build

```bash
tinygo build -target=wasip1 -buildmode=c-shared -opt=z -no-debug -o orders.wasm .
```

The `wasm-unknown` target (`tinygo build -target=wasm-unknown ...`) also works
and produces a smaller module without WASI imports, but it uses a leaking
garbage collector, so prefer `wasip1` for procedures that allocate.

Rules that follow from how the server runs procedures:

- **Use TinyGo and `//export`.** The server instantiates the module and calls
  the exported function directly. It does not run `_start` or `_initialize`.
  Modules built with the standard Go toolchain (`GOOS=wasip1`,
  `//go:wasmexport`) and TinyGo's `//go:wasmexport` trap with "runtime not
  initialized" when called that way.
- **Keep package-level state static.** Values TinyGo can compute at compile
  time (constants, literals, maps of literals) are fine. Avoid `init()`
  functions with side effects.
- **Scalars in, scalars out.** Return a status code or a number; write
  richer results to a table with `Exec`.

## Host functions

The `heliosdb` package wraps the `heliosdb` WASM import module. Imports are
only linked into the module for the functions a procedure actually uses.

| Go API | Host import | Notes |
|--------|-------------|-------|
| `ExecSQL(sql) (Result, error)`, `Exec(sql) error`, `QueryRowCount(sql)` | `exec_sql(ptr, len i32) i32` | The host returns a result-set handle. There are no bind parameters: build literals with `Quote`, `QuoteIdent` and `Int64`. |
| `Result.RowCount()`, `Result.Free()` | `result_row_count(id i32) i32`, `result_free(id i32)` | Row values are not readable through the ABI, only the row count. |
| `ExecPrepared(stmtID)` | `exec_prepared(id i64) i32` | Statement prepared on the server. |
| `Begin()`, `Tx.Commit()`, `Tx.Rollback()`, `WithTx(fn)` | `begin_transaction() i64`, `commit_transaction(id i64) i32`, `rollback_transaction(id i64) i32` | |
| `KVGet`, `KVSet`, `KVDelete` | `kv_get(kp, kl, vp i32) i32`, `kv_set(kp, kl, vp, vl i32) i32`, `kv_delete(kp, kl i32) i32` | Values are 1 to 64 KiB (`MaxKVValueSize`). |
| `Log`, `LogInfo`, `LogWarn`, `LogError`, ... | `log(level, ptr, len i32) i32` | Levels 0 (trace) to 4 (error). |
| `Hash(alg, data)` | `hash(alg, dp, dl, op i32) i32` | `SHA256`, `SHA512`, `BLAKE3`. |
| `RandomBytes(n)` | `random_bytes(op, n i32) i32` | Server CSPRNG, at most 1 MiB per call. |

Every host call returns a negative value on failure, including when the
procedure was not granted the capability it needs (database read/write,
transactions, logging, crypto, random). The SDK turns that into a
`*heliosdb.HostError`.

## Test natively

Outside WebAssembly the package uses a `Host` installed with `SetHost`. The
`heliosdbtest` package provides one:

```go
func TestRecordOrder(t *testing.T) {
	fake := heliosdbtest.Install(t)
	if rc := recordOrder(7, 1999); rc != 0 {
		t.Fatalf("rc = %d", rc)
	}
	if len(fake.SQL) != 1 {
		t.Fatalf("sql = %v", fake.SQL)
	}
}
```

```bash
go test ./...
```

## ABI conformance

[`conformance`](conformance) is a separate module that builds the example
with TinyGo, checks every import against the host ABI (WASI preview 1 plus
the `heliosdb` functions above, with exact signatures), and runs the
procedures in [wazero](https://wazero.io) without calling `_start` or
`_initialize`, the same way the server calls them.

```bash
cd conformance && go test ./...   # needs tinygo on PATH (or TINYGO=...)
```

## Register the procedure

Upload the `.wasm` module and register it as described in the
[WASM procedures guide](https://heliosdb.com/docs/full/guides/features/wasm_procedures_user_guide/).

## License

Apache-2.0
