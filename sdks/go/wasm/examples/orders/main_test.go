package main

import (
	"strings"
	"testing"

	"github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdbtest"
)

func TestApplyDiscount(t *testing.T) {
	cases := []struct {
		price   int64
		percent int32
		want    int64
	}{{1000, 10, 900}, {1000, 0, 1000}, {1000, 150, 0}, {999, 50, 500}}
	for _, c := range cases {
		if got := applyDiscount(c.price, c.percent); got != c.want {
			t.Errorf("applyDiscount(%d, %d) = %d, want %d", c.price, c.percent, got, c.want)
		}
	}
}

func TestRecordOrder(t *testing.T) {
	fake := heliosdbtest.Install(t)
	fake.OnSQL("INSERT INTO orders", 1)
	if rc := recordOrder(7, 1999); rc != 0 {
		t.Fatalf("rc = %d", rc)
	}
	if rc := recordOrder(8, 500); rc != 0 {
		t.Fatalf("rc = %d", rc)
	}
	if len(fake.SQL) != 2 || !strings.Contains(fake.SQL[0], "VALUES (7, 1999)") {
		t.Fatalf("sql = %v", fake.SQL)
	}
	if string(fake.KV["orders:last_customer"]) != "8" {
		t.Fatalf("kv = %q", fake.KV)
	}
	if fake.OpenTx() != 0 {
		t.Fatal("transaction left open")
	}
}

func TestRecordOrderRollsBackOnFailure(t *testing.T) {
	fake := heliosdbtest.Install(t)
	fake.FailSQL("INSERT")
	if rc := recordOrder(7, 100); rc != -1 {
		t.Fatalf("rc = %d", rc)
	}
	if len(fake.Tx) != 2 || fake.Tx[1].Op != "rollback" {
		t.Fatalf("tx = %v", fake.Tx)
	}
	if recordOrder(1, 0) != -1 {
		t.Fatal("non-positive amount should fail")
	}
}

func TestRecordOrderRejectsUnexpectedRowCount(t *testing.T) {
	fake := heliosdbtest.Install(t)
	fake.OnSQL("INSERT", 0)
	if rc := recordOrder(7, 100); rc != -1 {
		t.Fatalf("rc = %d", rc)
	}
	if fake.Tx[len(fake.Tx)-1].Op != "rollback" {
		t.Fatalf("tx = %v", fake.Tx)
	}
}
