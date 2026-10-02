// Package heliosdb lets you write HeliosDB Full WASM procedures in Go and
// compile them to WebAssembly with TinyGo.
//
// The package imports the HeliosDB Full WASM host ABI: the "env" module
// with heliosdb_execute, heliosdb_begin_tx, heliosdb_commit_tx,
// heliosdb_rollback_tx, heliosdb_storage_write and heliosdb_log, as
// registered by heliosdb-wasm/src/host.rs in the HeliosDB Full source.
//
// A procedure is an exported function whose parameters and results are WASM
// scalars (int32, int64, float32, float64). The host instantiates the module
// and calls the exported function directly; it does not run _start or
// _initialize. Export procedures with TinyGo's //export directive and keep
// package-level state to values TinyGo can initialise at compile time.
//
//	package main
//
//	import "github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdb"
//
//	//export touch_user
//	func touchUser(id int64) int32 {
//		_, err := heliosdb.Exec("UPDATE users SET seen_at = NOW() WHERE id = " + heliosdb.Int64(id))
//		if err != nil {
//			heliosdb.LogError(err.Error())
//			return -1
//		}
//		return 0
//	}
//
//	func main() {}
//
// Build:
//
//	tinygo build -target=wasm-unknown -gc=conservative -opt=z -no-debug -o touch_user.wasm .
//
// Outside WebAssembly (for example under "go test") the package talks to a
// Host installed with SetHost; the heliosdbtest package provides an in-memory
// fake for unit tests.
package heliosdb
