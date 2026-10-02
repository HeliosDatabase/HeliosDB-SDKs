TINYGO ?= tinygo

.PHONY: test example conformance

test:
	go test ./...

example:
	cd examples/orders && $(TINYGO) build -target=wasm-unknown -gc=conservative -opt=z -no-debug -o orders.wasm .

conformance:
	cd conformance && TINYGO=$(TINYGO) go test ./...
