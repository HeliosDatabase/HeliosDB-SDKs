package heliosdb

import (
	"errors"
	"strconv"
	"strings"
)

// ErrNoHost is returned when no Host is available (outside WebAssembly and
// before SetHost was called).
var ErrNoHost = errors.New("heliosdb: no host (not running inside HeliosDB; call SetHost in tests)")

// HostError reports a failed host call with the host's return code.
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

// Exec runs a SQL statement (INSERT, UPDATE, DELETE, DDL) through
// heliosdb_execute and returns the number of rows affected. The ABI has no
// bind parameters: build literals with Quote, QuoteIdent and Int64.
func Exec(sql string) (int64, error) {
	h, err := getHost()
	if err != nil {
		return 0, err
	}
	n := h.Execute(sql)
	if n < 0 {
		return 0, &HostError{Op: "heliosdb_execute", Code: n}
	}
	return n, nil
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
	id := h.BeginTx()
	if id <= 0 {
		return Tx{}, &HostError{Op: "heliosdb_begin_tx", Code: id}
	}
	return Tx{id: id}, nil
}

// Commit commits the transaction.
func (t Tx) Commit() error {
	h, err := getHost()
	if err != nil {
		return err
	}
	if rc := h.CommitTx(t.id); rc != 0 {
		return &HostError{Op: "heliosdb_commit_tx", Code: int64(rc)}
	}
	return nil
}

// Rollback rolls the transaction back.
func (t Tx) Rollback() error {
	h, err := getHost()
	if err != nil {
		return err
	}
	if rc := h.RollbackTx(t.id); rc != 0 {
		return &HostError{Op: "heliosdb_rollback_tx", Code: int64(rc)}
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

// StorageWrite stores value under key in the host key-value store
// (heliosdb_storage_write). The key must be valid UTF-8. There is no
// matching read in this SDK; see the Host documentation.
func StorageWrite(key string, value []byte) error {
	h, err := getHost()
	if err != nil {
		return err
	}
	if rc := h.StorageWrite(key, value); rc != 0 {
		return &HostError{Op: "heliosdb_storage_write", Code: int64(rc)}
	}
	return nil
}

// Log writes a message to the server log at the given level.
func Log(level LogLevel, msg string) error {
	h, err := getHost()
	if err != nil {
		return err
	}
	if rc := h.Log(level, msg); rc != 0 {
		return &HostError{Op: "heliosdb_log", Code: int64(rc)}
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
