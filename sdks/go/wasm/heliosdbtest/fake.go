// Package heliosdbtest provides an in-memory implementation of the HeliosDB
// WASM host ABI for unit-testing procedures with "go test" (no WebAssembly
// runtime and no server needed).
//
//	func TestRecordOrder(t *testing.T) {
//		fake := heliosdbtest.Install(t)
//		fake.OnSQL("INSERT INTO orders", 1)
//		if rc := recordOrder(7, 1999); rc != 0 {
//			t.Fatalf("rc = %d", rc)
//		}
//		if len(fake.SQL) != 1 { ... }
//	}
package heliosdbtest

import (
	"strings"
	"sync"
	"unicode/utf8"

	"github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdb"
)

// LogEntry is one call to heliosdb_log.
type LogEntry struct {
	Level   heliosdb.LogLevel
	Message string
}

// TxEvent records a transaction call ("begin", "commit" or "rollback").
type TxEvent struct {
	Op string
	ID int64
}

type sqlRule struct {
	prefix string
	rows   int64
	fail   bool
}

// Host is an in-memory heliosdb.Host that follows the return codes of the
// HeliosDB Full host. The zero value is not usable; call New.
type Host struct {
	mu sync.Mutex

	// SQL lists every statement passed to heliosdb_execute, in order.
	SQL []string
	// Logs lists every log call.
	Logs []LogEntry
	// Tx lists transaction calls.
	Tx []TxEvent
	// KV holds values written with heliosdb_storage_write.
	KV map[string][]byte
	// Denied makes every call of the named host function fail
	// (e.g. "heliosdb_execute").
	Denied map[string]bool

	rules  []sqlRule
	nextTx int64
	open   map[int64]bool
}

// New returns an empty fake host.
func New() *Host {
	return &Host{
		KV:     map[string][]byte{},
		Denied: map[string]bool{},
		nextTx: 1,
		open:   map[int64]bool{},
	}
}

// TB is the subset of testing.TB used by Install.
type TB interface {
	Cleanup(func())
}

// Install creates a fake host, installs it with heliosdb.SetHost and
// restores the previous host when the test ends.
func Install(t TB) *Host {
	h := New()
	prev := heliosdb.SetHost(h)
	t.Cleanup(func() { heliosdb.SetHost(prev) })
	return h
}

// OnSQL makes statements starting with prefix (case-insensitive) report
// rows rows affected. The first matching rule wins; unmatched statements
// report 0 rows.
func (h *Host) OnSQL(prefix string, rows int64) *Host {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.rules = append(h.rules, sqlRule{prefix: strings.ToUpper(prefix), rows: rows})
	return h
}

// FailSQL makes statements starting with prefix fail with -2 (execution
// failed).
func (h *Host) FailSQL(prefix string) *Host {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.rules = append(h.rules, sqlRule{prefix: strings.ToUpper(prefix), fail: true})
	return h
}

// OpenTx returns the number of transactions neither committed nor rolled
// back.
func (h *Host) OpenTx() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.open)
}

func (h *Host) Execute(sql string) int64 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if !utf8.ValidString(sql) {
		return -1
	}
	if h.Denied["heliosdb_execute"] {
		return -2
	}
	h.SQL = append(h.SQL, sql)
	up := strings.ToUpper(strings.TrimSpace(sql))
	for _, r := range h.rules {
		if strings.HasPrefix(up, r.prefix) {
			if r.fail {
				return -2
			}
			return r.rows
		}
	}
	return 0
}

func (h *Host) BeginTx() int64 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.Denied["heliosdb_begin_tx"] {
		return -1
	}
	id := h.nextTx
	h.nextTx++
	h.open[id] = true
	h.Tx = append(h.Tx, TxEvent{Op: "begin", ID: id})
	return id
}

func (h *Host) finish(op string, txID int64) int32 {
	if txID <= 0 || txID >= h.nextTx {
		return 1
	}
	if !h.open[txID] {
		return 2
	}
	delete(h.open, txID)
	h.Tx = append(h.Tx, TxEvent{Op: op, ID: txID})
	return 0
}

func (h *Host) CommitTx(txID int64) int32 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.Denied["heliosdb_commit_tx"] {
		return 3
	}
	return h.finish("commit", txID)
}

func (h *Host) RollbackTx(txID int64) int32 {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.finish("rollback", txID)
}

func (h *Host) StorageWrite(key string, value []byte) int32 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if !utf8.ValidString(key) {
		return 1
	}
	if h.Denied["heliosdb_storage_write"] {
		return 2
	}
	h.KV[key] = append([]byte(nil), value...)
	return 0
}

func (h *Host) Log(level heliosdb.LogLevel, msg string) int32 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if level < heliosdb.LevelTrace || level > heliosdb.LevelError {
		return 2
	}
	if !utf8.ValidString(msg) {
		return 1
	}
	h.Logs = append(h.Logs, LogEntry{Level: level, Message: msg})
	return 0
}

var _ heliosdb.Host = (*Host)(nil)
