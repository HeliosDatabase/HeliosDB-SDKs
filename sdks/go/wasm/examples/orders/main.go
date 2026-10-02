// Command orders is an example HeliosDB WASM procedure module.
//
// Build:
//
//	tinygo build -target=wasm-unknown -gc=conservative -opt=z -no-debug -o orders.wasm .
//
// Exports:
//
//	apply_discount(price_cents i64, percent i32) -> i64    pure computation
//	record_order(customer_id i64, amount_cents i64) -> i32  0 on success, -1 on error
package main

import (
	"errors"

	"github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdb"
)

//export apply_discount
func applyDiscount(priceCents int64, percent int32) int64 {
	if percent <= 0 {
		return priceCents
	}
	if percent >= 100 {
		return 0
	}
	return priceCents - priceCents*int64(percent)/100
}

//export record_order
func recordOrder(customerID, amountCents int64) int32 {
	if amountCents <= 0 {
		heliosdb.LogWarn("record_order: amount must be positive")
		return -1
	}
	err := heliosdb.WithTx(func(heliosdb.Tx) error {
		n, err := heliosdb.Exec("INSERT INTO orders (customer_id, amount_cents) VALUES (" +
			heliosdb.Int64(customerID) + ", " + heliosdb.Int64(amountCents) + ")")
		if err != nil {
			return err
		}
		if n != 1 {
			return errors.New("expected 1 row, got " + heliosdb.Int64(n))
		}
		return nil
	})
	if err != nil {
		heliosdb.LogError("record_order: " + err.Error())
		return -1
	}
	if err := heliosdb.StorageWrite("orders:last_customer", []byte(heliosdb.Int64(customerID))); err != nil {
		heliosdb.LogWarn("record_order: last customer not stored: " + err.Error())
	}
	heliosdb.LogInfo("record_order: customer " + heliosdb.Int64(customerID) + " order recorded")
	return 0
}

// main is required by the Go toolchain; the host never calls it.
func main() {}
