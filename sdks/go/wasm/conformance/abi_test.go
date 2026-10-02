package conformance

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/tetratelabs/wazero"
	"github.com/tetratelabs/wazero/api"
	"github.com/tetratelabs/wazero/imports/wasi_snapshot_preview1"
)

const (
	i32 = api.ValueTypeI32
	i64 = api.ValueTypeI64
)

type sig struct{ params, results []api.ValueType }

// hostABI is the "heliosdb" import module as implemented by the HeliosDB
// Full procedure host.
var hostABI = map[string]sig{
	"exec_sql":             {[]api.ValueType{i32, i32}, []api.ValueType{i32}},
	"exec_prepared":        {[]api.ValueType{i64}, []api.ValueType{i32}},
	"begin_transaction":    {nil, []api.ValueType{i64}},
	"commit_transaction":   {[]api.ValueType{i64}, []api.ValueType{i32}},
	"rollback_transaction": {[]api.ValueType{i64}, []api.ValueType{i32}},
	"kv_get":               {[]api.ValueType{i32, i32, i32}, []api.ValueType{i32}},
	"kv_set":               {[]api.ValueType{i32, i32, i32, i32}, []api.ValueType{i32}},
	"kv_delete":            {[]api.ValueType{i32, i32}, []api.ValueType{i32}},
	"log":                  {[]api.ValueType{i32, i32, i32}, []api.ValueType{i32}},
	"hash":                 {[]api.ValueType{i32, i32, i32, i32}, []api.ValueType{i32}},
	"random_bytes":         {[]api.ValueType{i32, i32}, []api.ValueType{i32}},
	"result_row_count":     {[]api.ValueType{i32}, []api.ValueType{i32}},
	"result_free":          {[]api.ValueType{i32}, nil},
}

var exampleExports = map[string]sig{
	"apply_discount":  {[]api.ValueType{i64, i32}, []api.ValueType{i64}},
	"record_order":    {[]api.ValueType{i64, i64}, []api.ValueType{i32}},
	"orders_recorded": {nil, []api.ValueType{i64}},
}

func tinygo(t *testing.T) string {
	if p := os.Getenv("TINYGO"); p != "" {
		return p
	}
	p, err := exec.LookPath("tinygo")
	if err != nil {
		t.Skip("tinygo not found on PATH; set TINYGO to run the ABI conformance tests")
	}
	return p
}

func build(t *testing.T, args ...string) []byte {
	t.Helper()
	out := filepath.Join(t.TempDir(), "orders.wasm")
	cmd := exec.Command(tinygo(t), append(append([]string{"build"}, args...), "-opt=z", "-no-debug", "-o", out, ".")...)
	cmd.Dir = filepath.Join("..", "examples", "orders")
	if b, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("tinygo build %v: %v\n%s", args, err, b)
	}
	b, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func sameTypes(a, b []api.ValueType) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// fakeHost records what the guest asked the host to do.
type fakeHost struct {
	sql      []string
	logs     []string
	tx       []string
	kv       map[string][]byte
	results  map[uint32]int
	nextRes  uint32
	nextTx   int64
	denySQL  bool
	hashSeen int
}

func newFakeHost() *fakeHost {
	return &fakeHost{kv: map[string][]byte{}, results: map[uint32]int{}, nextRes: 1, nextTx: 1}
}

func read(m api.Module, ptr, n uint32) []byte {
	b, ok := m.Memory().Read(ptr, n)
	if !ok {
		panic(fmt.Sprintf("guest pointer out of range: %d+%d", ptr, n))
	}
	return append([]byte(nil), b...)
}

func (h *fakeHost) instantiate(ctx context.Context, r wazero.Runtime) error {
	b := r.NewHostModuleBuilder("heliosdb")
	fn := func(name string, f interface{}) { b.NewFunctionBuilder().WithFunc(f).Export(name) }
	fn("exec_sql", func(ctx context.Context, m api.Module, p, n uint32) int32 {
		if h.denySQL {
			return -1
		}
		h.sql = append(h.sql, string(read(m, p, n)))
		id := h.nextRes
		h.nextRes++
		h.results[id] = 1
		return int32(id)
	})
	fn("exec_prepared", func(int64) int32 { return -1 })
	fn("begin_transaction", func() int64 {
		id := h.nextTx
		h.nextTx++
		h.tx = append(h.tx, "begin:"+strconv.FormatInt(id, 10))
		return id
	})
	fn("commit_transaction", func(id int64) int32 { h.tx = append(h.tx, "commit:"+strconv.FormatInt(id, 10)); return 0 })
	fn("rollback_transaction", func(id int64) int32 { h.tx = append(h.tx, "rollback:"+strconv.FormatInt(id, 10)); return 0 })
	fn("kv_get", func(ctx context.Context, m api.Module, kp, kn, vp uint32) int32 {
		v, ok := h.kv[string(read(m, kp, kn))]
		if !ok {
			return 0
		}
		if !m.Memory().Write(vp, v) {
			return -1
		}
		return int32(len(v))
	})
	fn("kv_set", func(ctx context.Context, m api.Module, kp, kn, vp, vn uint32) int32 {
		h.kv[string(read(m, kp, kn))] = read(m, vp, vn)
		return 0
	})
	fn("kv_delete", func(ctx context.Context, m api.Module, kp, kn uint32) int32 {
		delete(h.kv, string(read(m, kp, kn)))
		return 0
	})
	fn("log", func(ctx context.Context, m api.Module, level int32, p, n uint32) int32 {
		if level < 0 || level > 4 {
			return -1
		}
		h.logs = append(h.logs, strconv.Itoa(int(level))+":"+string(read(m, p, n)))
		return 0
	})
	fn("hash", func(ctx context.Context, m api.Module, alg int32, dp, dn, op uint32) int32 {
		h.hashSeen++
		return -1
	})
	fn("random_bytes", func(ctx context.Context, m api.Module, op uint32, n int32) int32 { return 0 })
	fn("result_row_count", func(id uint32) int32 {
		n, ok := h.results[id]
		if !ok {
			return -1
		}
		return int32(n)
	})
	fn("result_free", func(id uint32) { delete(h.results, id) })
	_, err := b.Instantiate(ctx)
	return err
}

func checkABI(t *testing.T, ctx context.Context, r wazero.Runtime, bin []byte) wazero.CompiledModule {
	t.Helper()
	cm, err := r.CompileModule(ctx, bin)
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range cm.ImportedFunctions() {
		mod, name, _ := f.Import()
		switch mod {
		case wasi_snapshot_preview1.ModuleName:
		case "heliosdb":
			want, ok := hostABI[name]
			if !ok {
				t.Errorf("import heliosdb.%s is not part of the host ABI", name)
				continue
			}
			if !sameTypes(f.ParamTypes(), want.params) || !sameTypes(f.ResultTypes(), want.results) {
				t.Errorf("import heliosdb.%s: signature %v -> %v, host has %v -> %v",
					name, f.ParamTypes(), f.ResultTypes(), want.params, want.results)
			}
		default:
			t.Errorf("unexpected import module %q (%s)", mod, name)
		}
	}
	if _, ok := cm.ExportedMemories()["memory"]; !ok {
		t.Error(`module must export its linear memory as "memory"`)
	}
	exports := cm.ExportedFunctions()
	for name, want := range exampleExports {
		f, ok := exports[name]
		if !ok {
			t.Errorf("missing export %s", name)
			continue
		}
		if !sameTypes(f.ParamTypes(), want.params) || !sameTypes(f.ResultTypes(), want.results) {
			t.Errorf("export %s: %v -> %v, want %v -> %v", name, f.ParamTypes(), f.ResultTypes(), want.params, want.results)
		}
	}
	return cm
}

func call(t *testing.T, ctx context.Context, m api.Module, name string, args ...uint64) uint64 {
	t.Helper()
	res, err := m.ExportedFunction(name).Call(ctx, args...)
	if err != nil {
		t.Fatalf("%s: %v", name, err)
	}
	return res[0]
}

func run(t *testing.T, bin []byte) {
	ctx := context.Background()
	r := wazero.NewRuntime(ctx)
	defer r.Close(ctx)
	wasi_snapshot_preview1.MustInstantiate(ctx, r)
	h := newFakeHost()
	if err := h.instantiate(ctx, r); err != nil {
		t.Fatal(err)
	}
	cm := checkABI(t, ctx, r, bin)
	// Like the HeliosDB procedure runtime: instantiate and call exports
	// directly, without running _start or _initialize.
	m, err := r.InstantiateModule(ctx, cm, wazero.NewModuleConfig().WithStartFunctions())
	if err != nil {
		t.Fatal(err)
	}

	if got := int64(call(t, ctx, m, "apply_discount", api.EncodeI64(1000), api.EncodeI32(10))); got != 900 {
		t.Errorf("apply_discount = %d", got)
	}
	for _, args := range [][2]int64{{7, 1999}, {8, 500}} {
		if rc := api.DecodeI32(call(t, ctx, m, "record_order", api.EncodeI64(args[0]), api.EncodeI64(args[1]))); rc != 0 {
			t.Fatalf("record_order%v = %d (logs %v)", args, rc, h.logs)
		}
	}
	if got := int64(call(t, ctx, m, "orders_recorded")); got != 2 {
		t.Errorf("orders_recorded = %d", got)
	}
	if len(h.sql) != 2 || h.sql[0] != "INSERT INTO orders (customer_id, amount_cents) VALUES (7, 1999)" {
		t.Errorf("sql = %q", h.sql)
	}
	if strings.Join(h.tx, ",") != "begin:1,commit:1,begin:2,commit:2" {
		t.Errorf("tx = %v", h.tx)
	}
	if len(h.results) != 0 {
		t.Errorf("result sets not freed: %v", h.results)
	}
	if len(h.logs) == 0 || !strings.HasPrefix(h.logs[0], "2:record_order: customer 7") {
		t.Errorf("logs = %v", h.logs)
	}

	h.denySQL = true
	if rc := api.DecodeI32(call(t, ctx, m, "record_order", api.EncodeI64(9), api.EncodeI64(1))); rc != -1 {
		t.Errorf("record_order with denied exec_sql = %d, want -1", rc)
	}
	if h.tx[len(h.tx)-1] != "rollback:3" {
		t.Errorf("expected rollback, tx = %v", h.tx)
	}
}

func TestExampleWASIReactor(t *testing.T) {
	run(t, build(t, "-target=wasip1", "-buildmode=c-shared"))
}

func TestExampleWASMUnknown(t *testing.T) {
	run(t, build(t, "-target=wasm-unknown"))
}
