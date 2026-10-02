//go:build wasm || tinygo.wasm

package heliosdb

import "unsafe"

//go:wasmimport heliosdb exec_sql
func hostExecSQL(sqlPtr, sqlLen int32) int32

//go:wasmimport heliosdb exec_prepared
func hostExecPrepared(stmtID int64) int32

//go:wasmimport heliosdb begin_transaction
func hostBeginTransaction() int64

//go:wasmimport heliosdb commit_transaction
func hostCommitTransaction(txID int64) int32

//go:wasmimport heliosdb rollback_transaction
func hostRollbackTransaction(txID int64) int32

//go:wasmimport heliosdb kv_get
func hostKVGet(keyPtr, keyLen, valuePtr int32) int32

//go:wasmimport heliosdb kv_set
func hostKVSet(keyPtr, keyLen, valuePtr, valueLen int32) int32

//go:wasmimport heliosdb kv_delete
func hostKVDelete(keyPtr, keyLen int32) int32

//go:wasmimport heliosdb log
func hostLog(level, msgPtr, msgLen int32) int32

//go:wasmimport heliosdb hash
func hostHash(algorithm, dataPtr, dataLen, outputPtr int32) int32

//go:wasmimport heliosdb random_bytes
func hostRandomBytes(outputPtr, count int32) int32

//go:wasmimport heliosdb result_row_count
func hostResultRowCount(resultID int32) int32

//go:wasmimport heliosdb result_free
func hostResultFree(resultID int32)

func bytesPtr(b []byte) int32 {
	if len(b) == 0 {
		return 0
	}
	return int32(uintptr(unsafe.Pointer(&b[0])))
}

// wasmHost forwards every call to the "heliosdb" import module.
type wasmHost struct{}

func (wasmHost) ExecSQL(sql string) int32 {
	b := []byte(sql)
	return hostExecSQL(bytesPtr(b), int32(len(b)))
}

func (wasmHost) ExecPrepared(stmtID int64) int32 { return hostExecPrepared(stmtID) }
func (wasmHost) BeginTransaction() int64         { return hostBeginTransaction() }
func (wasmHost) CommitTransaction(txID int64) int32 {
	return hostCommitTransaction(txID)
}
func (wasmHost) RollbackTransaction(txID int64) int32 {
	return hostRollbackTransaction(txID)
}

func (wasmHost) KVGet(key, dst []byte) int32 {
	return hostKVGet(bytesPtr(key), int32(len(key)), bytesPtr(dst))
}

func (wasmHost) KVSet(key, value []byte) int32 {
	return hostKVSet(bytesPtr(key), int32(len(key)), bytesPtr(value), int32(len(value)))
}

func (wasmHost) KVDelete(key []byte) int32 {
	return hostKVDelete(bytesPtr(key), int32(len(key)))
}

func (wasmHost) Log(level LogLevel, msg string) int32 {
	b := []byte(msg)
	return hostLog(int32(level), bytesPtr(b), int32(len(b)))
}

func (wasmHost) Hash(alg HashAlgorithm, data, dst []byte) int32 {
	return hostHash(int32(alg), bytesPtr(data), int32(len(data)), bytesPtr(dst))
}

func (wasmHost) RandomBytes(dst []byte) int32 {
	return hostRandomBytes(bytesPtr(dst), int32(len(dst)))
}

func (wasmHost) ResultRowCount(resultID int32) int32 { return hostResultRowCount(resultID) }
func (wasmHost) ResultFree(resultID int32)           { hostResultFree(resultID) }

var host Host = wasmHost{}
