//go:build !wasm && !tinygo.wasm

package heliosdb

// Outside WebAssembly there is no host until a test installs one.
var host Host
