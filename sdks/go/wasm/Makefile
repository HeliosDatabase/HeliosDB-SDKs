TINYGO ?= tinygo

.PHONY: test example conformance

test:
	go test ./...

example:
	cd examples/orders && $(TINYGO) build -target=wasip1 -buildmode=c-shared -opt=z -no-debug -o orders.wasm .

conformance:
	cd conformance && TINYGO=$(TINYGO) go test ./...
