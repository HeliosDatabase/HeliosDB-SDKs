// Package heliosdb lets you write HeliosDB Full stored procedures in Go and
// compile them to WebAssembly with TinyGo.
//
// A procedure is an exported function whose parameters and results are WASM
// scalars (int32, int64, float32, float64). The server instantiates the
// module with WASI preview 1 and the "heliosdb" host module, and calls the
// exported function directly: it does not run _start or _initialize. Export
// procedures with TinyGo's //export directive and keep package-level state to
// values TinyGo can initialise at compile time.
//
//	package main
//
//	import "github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdb"
//
//	//export touch_user
//	func touchUser(id int64) int32 {
//		err := heliosdb.Exec("UPDATE users SET seen_at = NOW() WHERE id = " + heliosdb.Int64(id))
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
//	tinygo build -target=wasip1 -buildmode=c-shared -opt=z -no-debug -o touch_user.wasm .
//
// Outside WebAssembly (for example under "go test") the package talks to a
// Host installed with SetHost; the heliosdbtest package provides an in-memory
// fake for unit tests.
package heliosdb
