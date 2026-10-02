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
	if rc := recordOrder(7, 1999); rc != 0 {
		t.Fatalf("rc = %d", rc)
	}
	if rc := recordOrder(8, 500); rc != 0 {
		t.Fatalf("rc = %d", rc)
	}
	if len(fake.SQL) != 2 || !strings.Contains(fake.SQL[0], "VALUES (7, 1999)") {
		t.Fatalf("sql = %v", fake.SQL)
	}
	if got := ordersRecorded(); got != 2 {
		t.Fatalf("orders_recorded = %d", got)
	}
	if fake.OpenResults() != 0 {
		t.Fatal("result sets leaked")
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
