// Package conformance checks that procedures built with the HeliosDB Go WASM
// SDK match the HeliosDB Full WASM host ABI (heliosdb-wasm/src/host.rs): every
// import comes from the "env" module with the exact host signature, nothing
// imports WASI (the host does not link it), the module exports its memory and
// procedures, and exported procedures run without _start or _initialize
// being called.
//
// It is a separate module so the SDK itself has no dependencies. Run it with
// TinyGo on PATH (or TINYGO=/path/to/tinygo):
//
//	cd sdks/go/wasm/conformance && go test ./...
package conformance
