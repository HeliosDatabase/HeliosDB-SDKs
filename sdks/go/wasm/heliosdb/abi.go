package heliosdb

// HostModule is the WASM import module that provides the HeliosDB host
// functions.
const HostModule = "heliosdb"

// LogLevel is the level argument of the host "log" function.
type LogLevel int32

const (
	LevelTrace LogLevel = 0
	LevelDebug LogLevel = 1
	LevelInfo  LogLevel = 2
	LevelWarn  LogLevel = 3
	LevelError LogLevel = 4
)

// HashAlgorithm is the algorithm argument of the host "hash" function.
type HashAlgorithm int32

const (
	SHA256 HashAlgorithm = 0
	SHA512 HashAlgorithm = 1
	BLAKE3 HashAlgorithm = 2
)

// Size returns the digest length in bytes.
func (a HashAlgorithm) Size() int {
	switch a {
	case SHA512:
		return 64
	case SHA256, BLAKE3:
		return 32
	}
	return 0
}

const (
	// MaxKVValueSize is the largest value KVSet accepts and KVGet reads back.
	// The host copies a stored value into guest memory without a length
	// limit, so the SDK caps values on write to keep reads in bounds.
	MaxKVValueSize = 64 << 10

	// MaxRandomBytes is the per-call limit of the host random_bytes function.
	MaxRandomBytes = 1 << 20
)

// Host is the raw host-function ABI of the "heliosdb" import module. Return
// values follow the host: a negative value signals an error (including a
// missing capability). Inside WebAssembly the package uses the real imports;
// elsewhere install an implementation with SetHost.
type Host interface {
	// exec_sql(sql_ptr i32, sql_len i32) -> i32 result id
	ExecSQL(sql string) int32
	// exec_prepared(stmt_id i64) -> i32 result id
	ExecPrepared(stmtID int64) int32
	// begin_transaction() -> i64 transaction id
	BeginTransaction() int64
	// commit_transaction(tx_id i64) -> i32
	CommitTransaction(txID int64) int32
	// rollback_transaction(tx_id i64) -> i32
	RollbackTransaction(txID int64) int32
	// kv_get(key_ptr i32, key_len i32, value_ptr i32) -> i32 bytes written
	// (0 = not found). The host writes the whole value at value_ptr.
	KVGet(key []byte, dst []byte) int32
	// kv_set(key_ptr i32, key_len i32, value_ptr i32, value_len i32) -> i32
	KVSet(key, value []byte) int32
	// kv_delete(key_ptr i32, key_len i32) -> i32
	KVDelete(key []byte) int32
	// log(level i32, msg_ptr i32, msg_len i32) -> i32
	Log(level LogLevel, msg string) int32
	// hash(algorithm i32, data_ptr i32, data_len i32, output_ptr i32) -> i32 digest length
	Hash(alg HashAlgorithm, data []byte, dst []byte) int32
	// random_bytes(output_ptr i32, count i32) -> i32
	RandomBytes(dst []byte) int32
	// result_row_count(result_id i32) -> i32
	ResultRowCount(resultID int32) int32
	// result_free(result_id i32)
	ResultFree(resultID int32)
}
