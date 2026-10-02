package heliosdb_test

import (
	"errors"
	"testing"

	"github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdb"
	"github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdbtest"
)

func TestNoHost(t *testing.T) {
	prev := heliosdb.SetHost(nil)
	defer heliosdb.SetHost(prev)
	if _, err := heliosdb.Exec("SELECT 1"); !errors.Is(err, heliosdb.ErrNoHost) {
		t.Fatalf("want ErrNoHost, got %v", err)
	}
}

func TestExecRowsAffected(t *testing.T) {
	fake := heliosdbtest.Install(t)
	fake.OnSQL("update", 3)
	n, err := heliosdb.Exec("UPDATE t SET a = 1")
	if err != nil || n != 3 {
		t.Fatalf("Exec = %d, %v", n, err)
	}
	if n, err := heliosdb.Exec("CREATE TABLE x (a INT)"); n != 0 || err != nil {
		t.Fatalf("Exec = %d, %v", n, err)
	}
	if len(fake.SQL) != 2 {
		t.Fatalf("sql = %v", fake.SQL)
	}
}

func TestExecErrors(t *testing.T) {
	fake := heliosdbtest.Install(t)
	fake.FailSQL("DROP")
	_, err := heliosdb.Exec("DROP TABLE x")
	var he *heliosdb.HostError
	if !errors.As(err, &he) || he.Op != "heliosdb_execute" || he.Code != -2 {
		t.Fatalf("want HostError heliosdb_execute -2, got %v", err)
	}
	if _, err := heliosdb.Exec("SELECT \xff"); !errors.As(err, &he) || he.Code != -1 {
		t.Fatalf("invalid UTF-8: %v", err)
	}
}

func TestWithTx(t *testing.T) {
	fake := heliosdbtest.Install(t)
	if err := heliosdb.WithTx(func(tx heliosdb.Tx) error {
		_, err := heliosdb.Exec("INSERT INTO a VALUES (1)")
		return err
	}); err != nil {
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
	if fake.OpenTx() != 0 {
		t.Fatal("open transactions")
	}
}

func TestTxErrors(t *testing.T) {
	fake := heliosdbtest.Install(t)
	tx, err := heliosdb.Begin()
	if err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	var he *heliosdb.HostError
	if err := tx.Commit(); !errors.As(err, &he) || he.Code != 2 {
		t.Fatalf("double commit: %v", err)
	}
	fake.Denied["heliosdb_begin_tx"] = true
	if _, err := heliosdb.Begin(); !errors.As(err, &he) || he.Op != "heliosdb_begin_tx" {
		t.Fatalf("denied begin: %v", err)
	}
}

func TestStorageWriteAndLog(t *testing.T) {
	fake := heliosdbtest.Install(t)
	if err := heliosdb.StorageWrite("k", []byte("v1")); err != nil {
		t.Fatal(err)
	}
	if string(fake.KV["k"]) != "v1" {
		t.Fatalf("kv %q", fake.KV)
	}
	var he *heliosdb.HostError
	if err := heliosdb.StorageWrite("\xff", nil); !errors.As(err, &he) || he.Code != 1 {
		t.Fatalf("invalid key: %v", err)
	}
	heliosdb.LogInfo("hello")
	heliosdb.LogError("bad")
	if len(fake.Logs) != 2 || fake.Logs[0].Level != heliosdb.LevelInfo || fake.Logs[1].Message != "bad" {
		t.Fatalf("logs %v", fake.Logs)
	}
	if err := heliosdb.Log(heliosdb.LogLevel(9), "x"); !errors.As(err, &he) || he.Code != 2 {
		t.Fatalf("bad level: %v", err)
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
