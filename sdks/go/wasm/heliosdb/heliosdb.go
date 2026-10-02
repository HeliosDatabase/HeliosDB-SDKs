package heliosdb

import (
	"errors"
	"strconv"
	"strings"
)

// ErrNoHost is returned when no Host is available (outside WebAssembly and
// before SetHost was called).
var ErrNoHost = errors.New("heliosdb: no host (not running inside HeliosDB; call SetHost in tests)")

// ErrValueTooLarge is returned by KVSet for values over MaxKVValueSize.
var ErrValueTooLarge = errors.New("heliosdb: value larger than MaxKVValueSize")

// ErrEmptyValue is returned by KVSet for empty values; the host reports an
// empty value the same way as a missing key.
var ErrEmptyValue = errors.New("heliosdb: empty values are not supported (indistinguishable from a missing key)")

// HostError reports a failed host call. The host returns -1 for any failure,
// including a capability the procedure was not granted.
type HostError struct {
	Op   string
	Code int64
}

func (e *HostError) Error() string {
	return "heliosdb: " + e.Op + " failed (host returned " + strconv.FormatInt(e.Code, 10) + ")"
}

// SetHost replaces the Host used by the package and returns the previous
// one. Use it in native unit tests; procedures running inside HeliosDB use
// the real host imports and never need it.
func SetHost(h Host) Host {
	prev := host
	host = h
	return prev
}

func getHost() (Host, error) {
	if host == nil {
		return nil, ErrNoHost
	}
	return host, nil
}

// Result is a server-side result set returned by ExecSQL. Free it when done.
type Result struct {
	id int32
}

// ID returns the host result-set handle.
func (r Result) ID() int32 { return r.id }

// RowCount returns the number of rows in the result set.
func (r Result) RowCount() (int, error) {
	h, err := getHost()
	if err != nil {
		return 0, err
	}
	n := h.ResultRowCount(r.id)
	if n < 0 {
		return 0, &HostError{Op: "result_row_count", Code: int64(n)}
	}
	return int(n), nil
}

// Free releases the result set on the host.
func (r Result) Free() {
	if h, err := getHost(); err == nil {
		h.ResultFree(r.id)
	}
}

// ExecSQL runs a SQL statement and returns its result set. The ABI has no
// bind parameters: build literals with Quote, QuoteIdent and Int64.
func ExecSQL(sql string) (Result, error) {
	h, err := getHost()
	if err != nil {
		return Result{}, err
	}
	id := h.ExecSQL(sql)
	if id < 0 {
		return Result{}, &HostError{Op: "exec_sql", Code: int64(id)}
	}
	return Result{id: id}, nil
}

// Exec runs a SQL statement and discards its result set.
func Exec(sql string) error {
	r, err := ExecSQL(sql)
	if err != nil {
		return err
	}
	r.Free()
	return nil
}

// QueryRowCount runs a query and returns how many rows it produced.
func QueryRowCount(sql string) (int, error) {
	r, err := ExecSQL(sql)
	if err != nil {
		return 0, err
	}
	defer r.Free()
	return r.RowCount()
}

// ExecPrepared executes a statement prepared on the server by id.
func ExecPrepared(stmtID int64) (Result, error) {
	h, err := getHost()
	if err != nil {
		return Result{}, err
	}
	id := h.ExecPrepared(stmtID)
	if id < 0 {
		return Result{}, &HostError{Op: "exec_prepared", Code: int64(id)}
	}
	return Result{id: id}, nil
}

// Tx is a host transaction.
type Tx struct {
	id int64
}

// ID returns the host transaction id.
func (t Tx) ID() int64 { return t.id }

// Begin starts a transaction.
func Begin() (Tx, error) {
	h, err := getHost()
	if err != nil {
		return Tx{}, err
	}
	id := h.BeginTransaction()
	if id < 0 {
		return Tx{}, &HostError{Op: "begin_transaction", Code: id}
	}
	return Tx{id: id}, nil
}

// Commit commits the transaction.
func (t Tx) Commit() error {
	h, err := getHost()
	if err != nil {
		return err
	}
	if rc := h.CommitTransaction(t.id); rc < 0 {
		return &HostError{Op: "commit_transaction", Code: int64(rc)}
	}
	return nil
}

// Rollback rolls the transaction back.
func (t Tx) Rollback() error {
	h, err := getHost()
	if err != nil {
		return err
	}
	if rc := h.RollbackTransaction(t.id); rc < 0 {
		return &HostError{Op: "rollback_transaction", Code: int64(rc)}
	}
	return nil
}

// WithTx runs fn inside a transaction, committing on success and rolling
// back when fn returns an error.
func WithTx(fn func(Tx) error) error {
	tx, err := Begin()
	if err != nil {
		return err
	}
	if err := fn(tx); err != nil {
		_ = tx.Rollback()
		return err
	}
	return tx.Commit()
}

// KVGet reads a key from the procedure key-value store. found is false when
// the key does not exist.
func KVGet(key string) (value []byte, found bool, err error) {
	h, err := getHost()
	if err != nil {
		return nil, false, err
	}
	buf := make([]byte, MaxKVValueSize)
	n := h.KVGet([]byte(key), buf)
	if n < 0 {
		return nil, false, &HostError{Op: "kv_get", Code: int64(n)}
	}
	if n == 0 {
		return nil, false, nil
	}
	if int(n) > len(buf) {
		return nil, false, ErrValueTooLarge
	}
	out := make([]byte, n)
	copy(out, buf[:n])
	return out, true, nil
}

// KVSet stores a value (1..MaxKVValueSize bytes).
func KVSet(key string, value []byte) error {
	if len(value) == 0 {
		return ErrEmptyValue
	}
	if len(value) > MaxKVValueSize {
		return ErrValueTooLarge
	}
	h, err := getHost()
	if err != nil {
		return err
	}
	if rc := h.KVSet([]byte(key), value); rc < 0 {
		return &HostError{Op: "kv_set", Code: int64(rc)}
	}
	return nil
}

// KVDelete removes a key.
func KVDelete(key string) error {
	h, err := getHost()
	if err != nil {
		return err
	}
	if rc := h.KVDelete([]byte(key)); rc < 0 {
		return &HostError{Op: "kv_delete", Code: int64(rc)}
	}
	return nil
}

// Log writes a message to the server log at the given level.
func Log(level LogLevel, msg string) error {
	h, err := getHost()
	if err != nil {
		return err
	}
	if rc := h.Log(level, msg); rc < 0 {
		return &HostError{Op: "log", Code: int64(rc)}
	}
	return nil
}

// LogTrace, LogDebug, LogInfo, LogWarn and LogError are shorthands for Log.
// Errors are ignored; use Log to observe them.
func LogTrace(msg string) { _ = Log(LevelTrace, msg) }
func LogDebug(msg string) { _ = Log(LevelDebug, msg) }
func LogInfo(msg string)  { _ = Log(LevelInfo, msg) }
func LogWarn(msg string)  { _ = Log(LevelWarn, msg) }
func LogError(msg string) { _ = Log(LevelError, msg) }

// Hash computes a digest on the host.
func Hash(alg HashAlgorithm, data []byte) ([]byte, error) {
	size := alg.Size()
	if size == 0 {
		return nil, errors.New("heliosdb: unknown hash algorithm")
	}
	h, err := getHost()
	if err != nil {
		return nil, err
	}
	out := make([]byte, size)
	n := h.Hash(alg, data, out)
	if n < 0 {
		return nil, &HostError{Op: "hash", Code: int64(n)}
	}
	if int(n) != size {
		return nil, &HostError{Op: "hash", Code: int64(n)}
	}
	return out, nil
}

// RandomBytes returns n bytes from the server's CSPRNG (n <= MaxRandomBytes).
func RandomBytes(n int) ([]byte, error) {
	if n < 0 || n > MaxRandomBytes {
		return nil, errors.New("heliosdb: random byte count out of range")
	}
	h, err := getHost()
	if err != nil {
		return nil, err
	}
	out := make([]byte, n)
	if rc := h.RandomBytes(out); rc < 0 {
		return nil, &HostError{Op: "random_bytes", Code: int64(rc)}
	}
	return out, nil
}

// Quote returns s as a SQL string literal ('...' with embedded quotes
// doubled). Use it when building statements, since the ABI has no bind
// parameters.
func Quote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", "''") + "'"
}

// QuoteIdent returns s as a double-quoted SQL identifier.
func QuoteIdent(s string) string {
	return `"` + strings.ReplaceAll(s, `"`, `""`) + `"`
}

// Int64 formats an integer for use in SQL text.
func Int64(v int64) string { return strconv.FormatInt(v, 10) }
