package heliosdb_test

import (
	"bytes"
	"crypto/sha256"
	"errors"
	"testing"

	"github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdb"
	"github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdbtest"
)

func TestNoHost(t *testing.T) {
	prev := heliosdb.SetHost(nil)
	defer heliosdb.SetHost(prev)
	if _, err := heliosdb.ExecSQL("SELECT 1"); !errors.Is(err, heliosdb.ErrNoHost) {
		t.Fatalf("want ErrNoHost, got %v", err)
	}
}

func TestExecSQLAndRowCount(t *testing.T) {
	fake := heliosdbtest.Install(t)
	fake.OnSQL("select", 3)
	r, err := heliosdb.ExecSQL("SELECT * FROM t")
	if err != nil {
		t.Fatal(err)
	}
	n, err := r.RowCount()
	if err != nil || n != 3 {
		t.Fatalf("rowcount = %d, %v", n, err)
	}
	r.Free()
	if !fake.Freed(r.ID()) || fake.OpenResults() != 0 {
		t.Fatal("result not freed")
	}
	if n, err := heliosdb.QueryRowCount("select 1"); n != 3 || err != nil {
		t.Fatalf("QueryRowCount = %d, %v", n, err)
	}
	if fake.OpenResults() != 0 {
		t.Fatal("QueryRowCount leaked a result set")
	}
}

func TestExecErrorAndDeniedCapability(t *testing.T) {
	fake := heliosdbtest.Install(t)
	fake.FailSQL("DROP")
	err := heliosdb.Exec("DROP TABLE x")
	var he *heliosdb.HostError
	if !errors.As(err, &he) || he.Op != "exec_sql" || he.Code != -1 {
		t.Fatalf("want HostError exec_sql, got %v", err)
	}
	fake.Denied["exec_sql"] = true
	if err := heliosdb.Exec("SELECT 1"); err == nil {
		t.Fatal("denied capability should fail")
	}
}

func TestWithTx(t *testing.T) {
	fake := heliosdbtest.Install(t)
	if err := heliosdb.WithTx(func(tx heliosdb.Tx) error { return heliosdb.Exec("INSERT INTO a VALUES (1)") }); err != nil {
		t.Fatal(err)
	}
	boom := errors.New("boom")
	if err := heliosdb.WithTx(func(heliosdb.Tx) error { return boom }); err != boom {
		t.Fatalf("got %v", err)
	}
	want := []heliosdbtest.TxEvent{{Op: "begin", ID: 1}, {Op: "commit", ID: 1}, {Op: "begin", ID: 2}, {Op: "rollback", ID: 2}}
	if len(fake.Tx) != len(want) {
		t.Fatalf("tx events %v", fake.Tx)
	}
	for i := range want {
		if fake.Tx[i] != want[i] {
			t.Fatalf("tx events %v", fake.Tx)
		}
	}
}

func TestKV(t *testing.T) {
	heliosdbtest.Install(t)
	if _, found, err := heliosdb.KVGet("k"); found || err != nil {
		t.Fatalf("missing key: found=%v err=%v", found, err)
	}
	if err := heliosdb.KVSet("k", []byte("v1")); err != nil {
		t.Fatal(err)
	}
	v, found, err := heliosdb.KVGet("k")
	if err != nil || !found || string(v) != "v1" {
		t.Fatalf("got %q %v %v", v, found, err)
	}
	if err := heliosdb.KVSet("k", nil); err != heliosdb.ErrEmptyValue {
		t.Fatalf("empty value: %v", err)
	}
	if err := heliosdb.KVSet("k", make([]byte, heliosdb.MaxKVValueSize+1)); err != heliosdb.ErrValueTooLarge {
		t.Fatalf("large value: %v", err)
	}
	if err := heliosdb.KVSet("big", make([]byte, heliosdb.MaxKVValueSize)); err != nil {
		t.Fatal(err)
	}
	if v, _, _ := heliosdb.KVGet("big"); len(v) != heliosdb.MaxKVValueSize {
		t.Fatalf("max-size value len %d", len(v))
	}
	if err := heliosdb.KVDelete("k"); err != nil {
		t.Fatal(err)
	}
	if _, found, _ := heliosdb.KVGet("k"); found {
		t.Fatal("deleted key still present")
	}
}

func TestLogHashRandom(t *testing.T) {
	fake := heliosdbtest.Install(t)
	heliosdb.LogInfo("hello")
	heliosdb.LogError("bad")
	if len(fake.Logs) != 2 || fake.Logs[0].Level != heliosdb.LevelInfo || fake.Logs[1].Message != "bad" {
		t.Fatalf("logs %v", fake.Logs)
	}
	sum, err := heliosdb.Hash(heliosdb.SHA256, []byte("abc"))
	want := sha256.Sum256([]byte("abc"))
	if err != nil || !bytes.Equal(sum, want[:]) {
		t.Fatalf("hash %x %v", sum, err)
	}
	b, err := heliosdb.RandomBytes(16)
	if err != nil || len(b) != 16 {
		t.Fatalf("random %v %v", b, err)
	}
	if _, err := heliosdb.RandomBytes(heliosdb.MaxRandomBytes + 1); err == nil {
		t.Fatal("expected range error")
	}
}

func TestQuoting(t *testing.T) {
	if got := heliosdb.Quote("O'Brien"); got != "'O''Brien'" {
		t.Fatal(got)
	}
	if got := heliosdb.QuoteIdent(`we"ird`); got != `"we""ird"` {
		t.Fatal(got)
	}
	if got := heliosdb.Int64(-42); got != "-42" {
		t.Fatal(got)
	}
}
