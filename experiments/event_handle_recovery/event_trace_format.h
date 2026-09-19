#pragma once

#include <cstdint>

// ACL Event trace binary ABI (version 2).
// V1 (version=1) is rejected by the V2 analyzer. Hot-path writers must match layout.

#define ACL_EVENT_TRACE_MAGIC 0x41435445u  // 'ACTE'
#define ACL_EVENT_TRACE_VERSION 2u
#define ACL_EVENT_TRACE_DEFAULT_CAPACITY 65536u
#define ACL_EVENT_TRACE_RESOLVER_AUDIT_CAP 256u

enum AclEventTraceOp : uint8_t {
    kAclEventTraceOpInvalid = 0,
    kAclEventTraceOpCreate = 1,
    kAclEventTraceOpCreateWithFlag = 2,
    kAclEventTraceOpCreateExWithFlag = 3,
    kAclEventTraceOpRecord = 4,
    kAclEventTraceOpWait = 5,
    kAclEventTraceOpReset = 6,
    kAclEventTraceOpDestroy = 7,
    kAclEventTraceOpStreamWaitWithTimeout = 8,
    kAclEventTraceOpRtCreate = 9,
    kAclEventTraceOpRtCreateWithFlag = 10,
    kAclEventTraceOpRtCreateExWithFlag = 11,
};

enum AclEventTraceSource : uint8_t {
    kAclEventTraceSourceAcl = 1,
    kAclEventTraceSourceRt = 2,
};

enum AclEventTraceResolverPath : uint8_t {
    kAclEventTraceResolverPlt = 1,
    kAclEventTraceResolverDlsym = 2,
    kAclEventTraceResolverDlvsym = 3,
};

enum AclEventTraceFlag : uint32_t {
    kAclEventTraceFlagNone = 0,
    kAclEventTraceFlagFatal = 1u << 0,
    kAclEventTraceFlagReentrancy = 1u << 1,
    kAclEventTraceFlagRingFull = 1u << 2,
    kAclEventTraceFlagRtFallback = 1u << 3,
};

enum AclEventTraceResolverApi : uint8_t {
    kAclEventTraceResolverApiDlsym = 1,
    kAclEventTraceResolverApiDlvsym = 2,
};

#pragma pack(push, 1)

struct AclEventTraceRecord {
    uint32_t format_version;
    uint8_t op;
    uint8_t committed;
    uint16_t reserved0;
    uint64_t call_sequence;
    uint64_t slot_sequence;
    uint32_t pid;
    uint32_t tid;
    int32_t rank;
    int32_t local_rank;
    uint64_t enter_realtime_ns;
    uint64_t exit_realtime_ns;
    uint64_t enter_monotonic_ns;
    uint64_t exit_monotonic_ns;
    uint64_t raw_event;
    uint64_t raw_stream;
    uint32_t create_flag;
    int32_t acl_ret;
    uint32_t flags;
    uint8_t source;
    uint8_t resolver_path;
    uint8_t nested_under_acl;
    uint8_t reserved1;
    uint64_t parent_acl_call_sequence;
};

struct AclEventTraceHeader {
    uint32_t magic;
    uint32_t version;
    uint64_t capacity;
    uint64_t committed_count;
    uint64_t dropped;
    uint64_t fatal;
    uint64_t late_calls;
    uint32_t pid;
    int32_t rank;
    uint32_t header_crc32;
    uint32_t reserved0;
    uint64_t resolver_queries;
    uint64_t resolver_wrappers_returned;
    uint64_t resolver_target_conflict;
    uint64_t resolver_audit_overflow;
    uint64_t acl_create_wrapper_calls;
    uint64_t rt_create_wrapper_calls;
    uint64_t logical_create_count;
};

struct AclEventTraceTrailer {
    uint64_t committed_count;
    uint64_t record_checksum;
    uint32_t trailer_crc32;
    uint32_t reserved0;
};

struct AclEventTraceResolverAuditEntry {
    uint8_t api;
    uint8_t reserved0;
    uint16_t name_len;
    uint64_t handle;
    uint64_t real_addr;
    uint64_t wrapper_addr;
    char name[32];
};

#pragma pack(pop)

static_assert(sizeof(AclEventTraceRecord) == 112, "AclEventTraceRecord size mismatch");
static_assert(sizeof(AclEventTraceHeader) == 120, "AclEventTraceHeader size mismatch");
static_assert(sizeof(AclEventTraceTrailer) == 24, "AclEventTraceTrailer size mismatch");
static_assert(sizeof(AclEventTraceResolverAuditEntry) == 60,
              "AclEventTraceResolverAuditEntry size mismatch");
