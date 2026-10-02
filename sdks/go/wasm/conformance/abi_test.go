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
	"unicode/utf8"

	"github.com/tetratelabs/wazero"
	"github.com/tetratelabs/wazero/api"
)

const (
	i32 = api.ValueTypeI32
	i64 = api.ValueTypeI64
	f64 = api.ValueTypeF64
)

type sig struct{ params, results []api.ValueType }

func ts(v ...api.ValueType) []api.ValueType { return v }

// hostABI is every function HeliosDB Full's WASM host registers in the "env"
// module (heliosdb-wasm/src/host.rs, HostFunctions::register), with the exact
// wasmtime signatures. The host's linker provides nothing else: no WASI.
var hostABI = map[string]sig{
	"log_i32":                {ts(i32), nil},
	"log_i64":                {ts(i64), nil},
	"log_f64":                {ts(f64), nil},
	"add_i32":                {ts(i32, i32), ts(i32)},
	"multiply_i64":           {ts(i64, i64), ts(i64)},
	"heliosdb_query":         {ts(i32, i32, i32, i32), ts(i32)},
	"heliosdb_execute":       {ts(i32, i32), ts(i64)},
	"heliosdb_log":           {ts(i32, i32, i32), ts(i32)},
	"heliosdb_storage_read":  {ts(i32, i32, i32, i32), ts(i32)},
	"heliosdb_storage_write": {ts(i32, i32, i32, i32), ts(i32)},
	"heliosdb_begin_tx":      {nil, ts(i64)},
	"heliosdb_commit_tx":     {ts(i64), ts(i32)},
	"heliosdb_rollback_tx":   {ts(i64), ts(i32)},
	"heliosdb_fetch_rows":    {ts(i64, i32, i32, i32), ts(i32)},
	"heliosdb_emit_event":    {ts(i32, i32, i32, i32), ts(i32)},
}

// unsafeImports write results at a host-chosen guest address (64 KiB and
// up), which is where TinyGo puts the module's data segment. The SDK must
// never import them.
var unsafeImports = []string{"heliosdb_query", "heliosdb_storage_read", "heliosdb_fetch_rows"}

var exampleExports = map[string]sig{
	"apply_discount": {ts(i64, i32), ts(i64)},
	"record_order":   {ts(i64, i64), ts(i32)},
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

// fakeHost implements the env functions the SDK uses, with host.rs return
// codes, reading arguments from guest memory like the real host.
type fakeHost struct {
	sql      []string
	logs     []string
	tx       []string
	kv       map[string]string
	open     map[int64]bool
	nextTx   int64
	failExec bool
}

func newFakeHost() *fakeHost {
	return &fakeHost{kv: map[string]string{}, open: map[int64]bool{}, nextTx: 1}
}

func read(m api.Module, ptr, n uint32) ([]byte, bool) {
	b, ok := m.Memory().Read(ptr, n)
	if !ok {
		return nil, false
	}
	return append([]byte(nil), b...), true
}

func (h *fakeHost) instantiate(ctx context.Context, r wazero.Runtime) error {
	b := r.NewHostModuleBuilder("env")
	fn := func(name string, f interface{}) { b.NewFunctionBuilder().WithFunc(f).Export(name) }
	fn("heliosdb_execute", func(ctx context.Context, m api.Module, p, n int32) int64 {
		if p < 0 || n < 0 {
			return -3
		}
		s, ok := read(m, uint32(p), uint32(n))
		if !ok {
			return -3
		}
		if !utf8.Valid(s) {
			return -1
		}
		if h.failExec {
			return -2
		}
		h.sql = append(h.sql, string(s))
		return 1
	})
	fn("heliosdb_begin_tx", func() int64 {
		id := h.nextTx
		h.nextTx++
		h.open[id] = true
		h.tx = append(h.tx, "begin:"+strconv.FormatInt(id, 10))
		return id
	})
	finish := func(op string, id int64) int32 {
		if !h.open[id] {
			return 1
		}
		delete(h.open, id)
		h.tx = append(h.tx, op+":"+strconv.FormatInt(id, 10))
		return 0
	}
	fn("heliosdb_commit_tx", func(id int64) int32 { return finish("commit", id) })
	fn("heliosdb_rollback_tx", func(id int64) int32 { return finish("rollback", id) })
	fn("heliosdb_storage_write", func(ctx context.Context, m api.Module, kp, kn, vp, vn int32) int32 {
		k, ok1 := read(m, uint32(kp), uint32(kn))
		v, ok2 := read(m, uint32(vp), uint32(vn))
		if !ok1 || !ok2 {
			return 3
		}
		if !utf8.Valid(k) {
			return 1
		}
		h.kv[string(k)] = string(v)
		return 0
	})
	fn("heliosdb_log", func(ctx context.Context, m api.Module, level, p, n int32) int32 {
		if level < 0 || level > 4 {
			return 2
		}
		s, ok := read(m, uint32(p), uint32(n))
		if !ok {
			return 3
		}
		if !utf8.Valid(s) {
			return 1
		}
		h.logs = append(h.logs, strconv.Itoa(int(level))+":"+string(s))
		return 0
	})
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
		if mod != "env" {
			t.Errorf("import %s.%s: the HeliosDB host links only the \"env\" module (no WASI)", mod, name)
			continue
		}
		want, ok := hostABI[name]
		if !ok {
			t.Errorf("import env.%s is not registered by the HeliosDB host", name)
			continue
		}
		if !sameTypes(f.ParamTypes(), want.params) || !sameTypes(f.ResultTypes(), want.results) {
			t.Errorf("import env.%s: signature %v -> %v, host has %v -> %v",
				name, f.ParamTypes(), f.ResultTypes(), want.params, want.results)
		}
		for _, u := range unsafeImports {
			if name == u {
				t.Errorf("import env.%s writes to a host-chosen guest address and must not be used", name)
			}
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
	// Like heliosdb-wasm's WasmInstance: a linker with only the env host
	// functions, instantiate, then call exports directly (no _start or
	// _initialize).
	h := newFakeHost()
	if err := h.instantiate(ctx, r); err != nil {
		t.Fatal(err)
	}
	cm := checkABI(t, ctx, r, bin)
	m, err := r.InstantiateModule(ctx, cm, wazero.NewModuleConfig().WithStartFunctions())
	if err != nil {
		t.Fatal(err)
	}

	if got := int64(call(t, ctx, m, "apply_discount", api.EncodeI64(1000), api.EncodeI32(10))); got != 900 {
		t.Errorf("apply_discount = %d", got)
	}
	// Call many times so the conservative GC has to run inside the guest.
	for i := int64(1); i <= 300; i++ {
		if rc := api.DecodeI32(call(t, ctx, m, "record_order", api.EncodeI64(i), api.EncodeI64(100+i))); rc != 0 {
			t.Fatalf("record_order(%d) = %d (logs %v)", i, rc, h.logs)
		}
	}
	if len(h.sql) != 300 || h.sql[0] != "INSERT INTO orders (customer_id, amount_cents) VALUES (1, 101)" {
		t.Errorf("sql[0] = %q (n=%d)", h.sql[0], len(h.sql))
	}
	if !strings.HasPrefix(strings.Join(h.tx, ","), "begin:1,commit:1,begin:2,commit:2") || len(h.open) != 0 {
		t.Errorf("tx = %v open = %v", h.tx[:4], h.open)
	}
	if h.kv["orders:last_customer"] != "300" {
		t.Errorf("kv = %v", h.kv)
	}
	if len(h.logs) == 0 || !strings.HasPrefix(h.logs[0], "2:record_order: customer 1 ") {
		t.Errorf("logs = %v", h.logs[:1])
	}

	h.failExec = true
	if rc := api.DecodeI32(call(t, ctx, m, "record_order", api.EncodeI64(9), api.EncodeI64(1))); rc != -1 {
		t.Errorf("record_order with failing execute = %d, want -1", rc)
	}
	if last := h.tx[len(h.tx)-1]; last != "rollback:301" {
		t.Errorf("expected rollback:301, got %s", last)
	}
	if last := h.logs[len(h.logs)-1]; !strings.Contains(last, "heliosdb_execute failed (host returned -2)") {
		t.Errorf("error log = %q", last)
	}
}

// TestExampleWASMUnknown is the supported build: env imports only.
func TestExampleWASMUnknown(t *testing.T) {
	run(t, build(t, "-target=wasm-unknown", "-gc=conservative"))
}

// TestWASIModuleIsRejected documents why wasip1 builds are not supported:
// they import wasi_snapshot_preview1, which the HeliosDB host does not link.
func TestWASIModuleIsRejected(t *testing.T) {
	ctx := context.Background()
	r := wazero.NewRuntime(ctx)
	defer r.Close(ctx)
	if err := newFakeHost().instantiate(ctx, r); err != nil {
		t.Fatal(err)
	}
	bin := build(t, "-target=wasip1", "-buildmode=c-shared")
	_, err := r.Instantiate(ctx, bin)
	if err == nil || !strings.Contains(fmt.Sprint(err), "wasi_snapshot_preview1") {
		t.Fatalf("wasip1 module instantiated without WASI: %v", err)
	}
}
