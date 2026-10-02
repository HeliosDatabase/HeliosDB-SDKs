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
	"crypto/rand"
	"crypto/sha256"
	"crypto/sha512"
	"strings"
	"sync"

	"github.com/HeliosDatabase/HeliosDB-SDKs/sdks/go/wasm/heliosdb"
)

// LogEntry is one call to the host log function.
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
	rows   int
	fail   bool
}

// Host is an in-memory heliosdb.Host. The zero value is not usable; call New.
type Host struct {
	mu sync.Mutex

	// SQL lists every statement passed to exec_sql, in order.
	SQL []string
	// Logs lists every log call.
	Logs []LogEntry
	// Tx lists transaction calls.
	Tx []TxEvent
	// KV is the key-value store.
	KV map[string][]byte
	// Denied makes every call of the named host function fail with -1
	// (e.g. "exec_sql"), simulating a missing capability.
	Denied map[string]bool

	rules   []sqlRule
	results map[int32]int
	nextRes int32
	nextTx  int64
	freed   map[int32]bool
}

// New returns an empty fake host.
func New() *Host {
	return &Host{
		KV:      map[string][]byte{},
		Denied:  map[string]bool{},
		results: map[int32]int{},
		freed:   map[int32]bool{},
		nextRes: 1,
		nextTx:  1,
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

// OnSQL makes statements starting with prefix (case-insensitive) return a
// result set of rows rows. The first matching rule wins; unmatched
// statements return an empty result set.
func (h *Host) OnSQL(prefix string, rows int) *Host {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.rules = append(h.rules, sqlRule{prefix: strings.ToUpper(prefix), rows: rows})
	return h
}

// FailSQL makes statements starting with prefix fail.
func (h *Host) FailSQL(prefix string) *Host {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.rules = append(h.rules, sqlRule{prefix: strings.ToUpper(prefix), fail: true})
	return h
}

// Freed reports whether result set id was freed.
func (h *Host) Freed(id int32) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.freed[id]
}

// OpenResults returns the number of result sets not yet freed.
func (h *Host) OpenResults() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.results)
}

func (h *Host) ExecSQL(sql string) int32 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.Denied["exec_sql"] {
		return -1
	}
	h.SQL = append(h.SQL, sql)
	rows := 0
	up := strings.ToUpper(strings.TrimSpace(sql))
	for _, r := range h.rules {
		if strings.HasPrefix(up, r.prefix) {
			if r.fail {
				return -1
			}
			rows = r.rows
			break
		}
	}
	id := h.nextRes
	h.nextRes++
	h.results[id] = rows
	return id
}

func (h *Host) ExecPrepared(stmtID int64) int32 {
	return h.ExecSQL("/* prepared */ EXECUTE " + heliosdb.Int64(stmtID))
}

func (h *Host) BeginTransaction() int64 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.Denied["begin_transaction"] {
		return -1
	}
	id := h.nextTx
	h.nextTx++
	h.Tx = append(h.Tx, TxEvent{Op: "begin", ID: id})
	return id
}

func (h *Host) CommitTransaction(txID int64) int32 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.Denied["commit_transaction"] {
		return -1
	}
	h.Tx = append(h.Tx, TxEvent{Op: "commit", ID: txID})
	return 0
}

func (h *Host) RollbackTransaction(txID int64) int32 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.Denied["rollback_transaction"] {
		return -1
	}
	h.Tx = append(h.Tx, TxEvent{Op: "rollback", ID: txID})
	return 0
}

func (h *Host) KVGet(key, dst []byte) int32 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.Denied["kv_get"] {
		return -1
	}
	v, ok := h.KV[string(key)]
	if !ok {
		return 0
	}
	if len(v) > len(dst) {
		// The real host would write past the buffer; fail loudly instead.
		return -1
	}
	return int32(copy(dst, v))
}

func (h *Host) KVSet(key, value []byte) int32 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.Denied["kv_set"] {
		return -1
	}
	h.KV[string(key)] = append([]byte(nil), value...)
	return 0
}

func (h *Host) KVDelete(key []byte) int32 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.Denied["kv_delete"] {
		return -1
	}
	delete(h.KV, string(key))
	return 0
}

func (h *Host) Log(level heliosdb.LogLevel, msg string) int32 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.Denied["log"] || level < heliosdb.LevelTrace || level > heliosdb.LevelError {
		return -1
	}
	h.Logs = append(h.Logs, LogEntry{Level: level, Message: msg})
	return 0
}

func (h *Host) Hash(alg heliosdb.HashAlgorithm, data, dst []byte) int32 {
	if h.Denied["hash"] {
		return -1
	}
	var sum []byte
	switch alg {
	case heliosdb.SHA256:
		s := sha256.Sum256(data)
		sum = s[:]
	case heliosdb.SHA512:
		s := sha512.Sum512(data)
		sum = s[:]
	default:
		// BLAKE3 is not in the Go standard library; tests that need it
		// should provide their own Host.
		return -1
	}
	return int32(copy(dst, sum))
}

func (h *Host) RandomBytes(dst []byte) int32 {
	if h.Denied["random_bytes"] || len(dst) > heliosdb.MaxRandomBytes {
		return -1
	}
	if _, err := rand.Read(dst); err != nil {
		return -1
	}
	return 0
}

func (h *Host) ResultRowCount(resultID int32) int32 {
	h.mu.Lock()
	defer h.mu.Unlock()
	n, ok := h.results[resultID]
	if !ok {
		return -1
	}
	return int32(n)
}

func (h *Host) ResultFree(resultID int32) {
	h.mu.Lock()
	defer h.mu.Unlock()
	delete(h.results, resultID)
	h.freed[resultID] = true
}

var _ heliosdb.Host = (*Host)(nil)
