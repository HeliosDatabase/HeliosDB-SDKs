//go:build wasm || tinygo.wasm

package heliosdb

import "unsafe"

//go:wasmimport env heliosdb_execute
func hostExecute(stmtPtr, stmtLen int32) int64

//go:wasmimport env heliosdb_begin_tx
func hostBeginTx() int64

//go:wasmimport env heliosdb_commit_tx
func hostCommitTx(txID int64) int32

//go:wasmimport env heliosdb_rollback_tx
func hostRollbackTx(txID int64) int32

//go:wasmimport env heliosdb_storage_write
func hostStorageWrite(keyPtr, keyLen, valuePtr, valueLen int32) int32

//go:wasmimport env heliosdb_log
func hostLog(level, msgPtr, msgLen int32) int32

func bytesPtr(b []byte) int32 {
	if len(b) == 0 {
		return 0
	}
	return int32(uintptr(unsafe.Pointer(&b[0])))
}

// wasmHost forwards every call to the "env" import module.
type wasmHost struct{}

func (wasmHost) Execute(sql string) int64 {
	b := []byte(sql)
	return hostExecute(bytesPtr(b), int32(len(b)))
}

func (wasmHost) BeginTx() int64              { return hostBeginTx() }
func (wasmHost) CommitTx(txID int64) int32   { return hostCommitTx(txID) }
func (wasmHost) RollbackTx(txID int64) int32 { return hostRollbackTx(txID) }

func (wasmHost) StorageWrite(key string, value []byte) int32 {
	k := []byte(key)
	return hostStorageWrite(bytesPtr(k), int32(len(k)), bytesPtr(value), int32(len(value)))
}

func (wasmHost) Log(level LogLevel, msg string) int32 {
	b := []byte(msg)
	return hostLog(int32(level), bytesPtr(b), int32(len(b)))
}

var host Host = wasmHost{}
