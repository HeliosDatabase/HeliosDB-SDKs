// Command orders is an example HeliosDB WASM stored procedure module.
//
// Build:
//
//	tinygo build -target=wasip1 -buildmode=c-shared -opt=z -no-debug -o orders.wasm .
//
// Exports:
//
//	apply_discount(price_cents i64, percent i32) -> i64   pure computation
//	record_order(customer_id i64, amount_cents i64) -> i32 0 on success, -1 on error
//	orders_recorded() -> i64                               counter kept in the KV store
package main

import (
	"strconv"

	"github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdb"
)

const counterKey = "orders:recorded"

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
		return heliosdb.Exec("INSERT INTO orders (customer_id, amount_cents) VALUES (" +
			heliosdb.Int64(customerID) + ", " + heliosdb.Int64(amountCents) + ")")
	})
	if err != nil {
		heliosdb.LogError("record_order: " + err.Error())
		return -1
	}
	n := ordersRecorded() + 1
	if err := heliosdb.KVSet(counterKey, []byte(strconv.FormatInt(n, 10))); err != nil {
		heliosdb.LogWarn("record_order: counter not updated: " + err.Error())
	}
	heliosdb.LogInfo("record_order: customer " + heliosdb.Int64(customerID) + " order recorded")
	return 0
}

//export orders_recorded
func ordersRecorded() int64 {
	v, found, err := heliosdb.KVGet(counterKey)
	if err != nil || !found {
		return 0
	}
	n, err := strconv.ParseInt(string(v), 10, 64)
	if err != nil {
		return 0
	}
	return n
}

// main is required by the Go toolchain; the server never calls it.
func main() {}
