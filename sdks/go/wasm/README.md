# HeliosDB Go WASM procedure SDK

Write HeliosDB Full WASM procedures in Go, compile them to WebAssembly with
[TinyGo](https://tinygo.org), and test them natively with `go test`.

```bash
go get github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm@latest
```

The module lives in `sdks/go/wasm` of
[HeliosDB-SDKs](https://github.com/HeliosDatabase/HeliosDB-SDKs) and is
versioned with tags of the form `sdks/go/wasm/vX.Y.Z`. It has no
dependencies. v0.1.0 is retracted: it targeted a host module that HeliosDB
Full does not link, so its modules cannot be instantiated.

| Package | Purpose |
|---------|---------|
| `github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdb` | Host functions: SQL statements, transactions, key-value writes, logging |
| `github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdbtest` | In-memory fake host for unit tests |

## Status in HeliosDB Full

The SDK builds modules for the WASM host in HeliosDB Full's `heliosdb-wasm`
crate (`HostFunctions` in `heliosdb-wasm/src/host.rs`). That host registers
its functions in the `env` import module, and Full's own Rust procedure
runtime (`heliosdb-wasm/crates/runtime`) uses the same module.

The `heliosdb-full` server does not run WASM procedures yet. "WASM
functions" are on the server's list of subsystems that are not wired, so
SQL that would create or call one is refused with SQLSTATE `0A000`. Modules
you build now can be unit-tested with `heliosdbtest` and checked with the
conformance suite below. They will load once the server enables the WASM
host.

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
		_, err := heliosdb.Exec("INSERT INTO orders (customer_id, amount_cents) VALUES (" +
			heliosdb.Int64(customerID) + ", " + heliosdb.Int64(amountCents) + ")")
		return err
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
tinygo build -target=wasm-unknown -gc=conservative -opt=z -no-debug -o orders.wasm .
```

The host has these requirements:

- **No WASI.** The host linker provides only the `env` functions, so a module
  that imports `wasi_snapshot_preview1` (any `-target=wasip1` build, or the
  standard Go toolchain's `GOOS=wasip1`) fails to instantiate. Use
  `-target=wasm-unknown`. Add `-gc=conservative`, because that target
  otherwise uses a garbage collector that never frees memory.
- **Use TinyGo and `//export`.** The host instantiates the module and calls
  the exported function directly. It does not run `_start` or `_initialize`.
- **Keep package-level state static.** Values TinyGo can compute at compile
  time (constants, literals, maps of literals) are fine. Avoid `init()`
  functions with side effects.
- **Scalars in, scalars out.** Return a status code or a number, and write
  richer results to a table with `Exec`.

## Host functions

The `heliosdb` package wraps these `env` imports. A function is only
imported into the module if the procedure uses it.

| Go API | Host import | Notes |
|--------|-------------|-------|
| `Exec(sql) (rowsAffected int64, err error)` | `heliosdb_execute(ptr, len i32) i64` | Negative return codes: -1 invalid UTF-8, -2 execution failed, -3 memory error. There are no bind parameters, so build literals with `Quote`, `QuoteIdent` and `Int64`. |
| `Begin()`, `Tx.Commit()`, `Tx.Rollback()`, `WithTx(fn)` | `heliosdb_begin_tx() i64`, `heliosdb_commit_tx(id i64) i32`, `heliosdb_rollback_tx(id i64) i32` | |
| `StorageWrite(key, value)` | `heliosdb_storage_write(kp, kl, vp, vl i32) i32` | The key must be valid UTF-8. |
| `Log`, `LogInfo`, `LogWarn`, `LogError`, ... | `heliosdb_log(level, ptr, len i32) i32` | Levels range from 0 (trace) to 4 (error). |

A failed host call becomes a `*heliosdb.HostError` that carries the host's
return code.

**Not wrapped:** `heliosdb_query`, `heliosdb_storage_read` and
`heliosdb_fetch_rows`. The host writes their results to a guest address it
picks itself, using a bump allocator that starts at 64 KiB, rather than into
a buffer the procedure supplies. TinyGo places a module's data segment at
exactly 64 KiB, so these calls would overwrite the procedure's own globals.
Read data inside SQL instead, for example with `INSERT ... SELECT` or
`UPDATE ... FROM`.

## Test natively

Outside WebAssembly the package uses a `Host` installed with `SetHost`. The
`heliosdbtest` package provides one with the host's return codes:

```go
func TestRecordOrder(t *testing.T) {
	fake := heliosdbtest.Install(t)
	fake.OnSQL("INSERT INTO orders", 1)
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

[`conformance`](conformance) is a separate module. It builds the example with
TinyGo and checks every import against the `env` functions the host
registers, with exact signatures. It confirms there are no WASI imports, runs
the procedures in [wazero](https://wazero.io) without calling `_start` or
`_initialize`, and checks that a `wasip1` build is rejected.

```bash
cd conformance && go test ./...   # needs tinygo on PATH (or TINYGO=...)
```

## License

Apache-2.0
