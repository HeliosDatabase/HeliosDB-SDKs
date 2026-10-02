// Package conformance checks that procedures built with the HeliosDB Go WASM
// SDK match the HeliosDB Full procedure ABI: every import is WASI preview 1
// or a known "heliosdb" host function with the exact signature, the module
// exports its memory and procedures, and exported procedures run without
// _start or _initialize being called (the server calls them directly).
//
// It is a separate module so the SDK itself has no dependencies. Run it with
// TinyGo on PATH (or TINYGO=/path/to/tinygo):
//
//	cd sdks/go/wasm/conformance && go test ./...
package conformance
