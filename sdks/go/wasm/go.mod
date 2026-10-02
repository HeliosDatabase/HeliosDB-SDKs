module github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm

go 1.21

// v0.1.0 imported a "heliosdb" host module that only a HeliosDB Full test
// links; modules built with it fail to instantiate. Use v0.2.0 or later.
retract v0.1.0
