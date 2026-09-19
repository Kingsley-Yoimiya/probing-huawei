// Stub libhccl surface used by test_hccl_issued_ledger.py.
#include <cstdint>

extern "C" {

using HcclResult = int32_t;
using HcclDataType = int32_t;
using HcclReduceOp = int32_t;
using HcclComm = void*;
using aclrtStream = void*;

HcclResult HcclAllReduce(void*, void*, uint64_t, HcclDataType, HcclReduceOp,
                         HcclComm, aclrtStream) {
    return 0;
}

HcclResult HcclAllGather(void*, void*, uint64_t, HcclDataType, HcclComm,
                         aclrtStream) {
    return 0;
}

HcclResult HcclReduceScatter(void*, void*, uint64_t, HcclDataType,
                             HcclReduceOp, HcclComm, aclrtStream) {
    return 0;
}

HcclResult HcclBroadcast(void*, uint64_t, HcclDataType, uint32_t, HcclComm,
                         aclrtStream) {
    return 0;
}

HcclResult HcclSend(void*, uint64_t, HcclDataType, uint32_t, HcclComm,
                    aclrtStream) {
    return 0;
}

HcclResult HcclRecv(void*, uint64_t, HcclDataType, uint32_t, HcclComm,
                    aclrtStream) {
    return 0;
}

}  // extern "C"
