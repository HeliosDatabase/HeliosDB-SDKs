package heliosdb

// HostModule is the WASM import module that provides the HeliosDB host
// functions. It is the "env" module registered by HeliosDB Full's WASM host
// (heliosdb-wasm/src/host.rs, HostFunctions::register), the ABI that Full's
// own Rust procedure runtime (heliosdb-wasm/crates/runtime) also imports.
const HostModule = "env"

// LogLevel is the level argument of heliosdb_log.
type LogLevel int32

const (
	LevelTrace LogLevel = 0
	LevelDebug LogLevel = 1
	LevelInfo  LogLevel = 2
	LevelWarn  LogLevel = 3
	LevelError LogLevel = 4
)

// Host is the subset of the HeliosDB Full WASM host ABI that this SDK wraps.
// Each method maps to one import of the "env" module, with the host's exact
// signature and return codes. Inside WebAssembly the package uses the real
// imports; elsewhere install an implementation with SetHost.
//
// Not wrapped: heliosdb_query, heliosdb_storage_read and heliosdb_fetch_rows.
// The host writes their results at an address it chooses itself (a bump
// allocator starting at 64 KiB of guest memory), not into a buffer supplied
// by the guest. TinyGo places a module's data segment at exactly that
// address, so those calls would overwrite the procedure's own globals.
type Host interface {
	// heliosdb_execute(stmt_ptr i32, stmt_len i32) -> i64
	// Rows affected (>= 0); -1 invalid UTF-8, -2 execution failed,
	// -3 memory access error.
	Execute(sql string) int64
	// heliosdb_begin_tx() -> i64
	// Transaction id (> 0); -1 failed to start, -2 too many transactions.
	BeginTx() int64
	// heliosdb_commit_tx(tx_id i64) -> i32
	// 0 success; 1 unknown id, 2 already finished, 3 commit failed.
	CommitTx(txID int64) int32
	// heliosdb_rollback_tx(tx_id i64) -> i32
	// 0 success; 1 unknown id, 2 already finished.
	RollbackTx(txID int64) int32
	// heliosdb_storage_write(key_ptr i32, key_len i32, value_ptr i32, value_len i32) -> i32
	// 0 success; 1 invalid UTF-8 key, 2 write failed, 3 memory access error.
	StorageWrite(key string, value []byte) int32
	// heliosdb_log(level i32, msg_ptr i32, msg_len i32) -> i32
	// 0 success; 1 invalid UTF-8, 2 invalid level, 3 memory access error.
	Log(level LogLevel, msg string) int32
}
