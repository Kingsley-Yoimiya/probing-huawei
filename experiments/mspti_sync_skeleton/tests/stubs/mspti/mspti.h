#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef int32_t msptiResult;
typedef int32_t msptiCallbackDomain;
typedef int32_t msptiCallbackId;
typedef void* msptiSubscriberHandle;
typedef int32_t msptiCommunicationDataType;

enum {
    MSPTI_SUCCESS = 0,
    MSPTI_ERROR_MAX_LIMIT_REACHED = 1,
    MSPTI_CB_DOMAIN_RUNTIME = 2,
    MSPTI_CBID_RUNTIME_STREAM_SYNCHRONIZED = 3,
    MSPTI_API_ENTER = 4,
    MSPTI_ACTIVITY_KIND_KERNEL = 5,
    MSPTI_ACTIVITY_KIND_COMMUNICATION = 6,
    MSPTI_ACTIVITY_COMMUNICATION_INT8 = 10,
    MSPTI_ACTIVITY_COMMUNICATION_UINT8,
    MSPTI_ACTIVITY_COMMUNICATION_INT16,
    MSPTI_ACTIVITY_COMMUNICATION_UINT16,
    MSPTI_ACTIVITY_COMMUNICATION_FP16,
    MSPTI_ACTIVITY_COMMUNICATION_BFP16,
    MSPTI_ACTIVITY_COMMUNICATION_INT32,
    MSPTI_ACTIVITY_COMMUNICATION_UINT32,
    MSPTI_ACTIVITY_COMMUNICATION_FP32,
    MSPTI_ACTIVITY_COMMUNICATION_INT64,
    MSPTI_ACTIVITY_COMMUNICATION_UINT64,
    MSPTI_ACTIVITY_COMMUNICATION_FP64,
    MSPTI_ACTIVITY_COMMUNICATION_INT128,
};

typedef struct {
    uint32_t deviceId;
    uint32_t streamId;
} msptiDeviceStream;

typedef struct {
    uint32_t kind;
} msptiActivity;

typedef struct {
    uint32_t kind;
    msptiDeviceStream ds;
    uint64_t start;
    uint64_t end;
    uint64_t correlationId;
} msptiActivityKernel;

typedef struct {
    uint32_t kind;
    msptiDeviceStream ds;
    uint64_t start;
    uint64_t end;
    uint64_t correlationId;
    uint64_t count;
    msptiCommunicationDataType dataType;
    const char* name;
    const char* commName;
} msptiActivityCommunication;

typedef struct {
    uint64_t correlationId;
    int32_t callbackSite;
    const char* functionName;
} msptiCallbackData;

typedef void (*msptiBufferRequestedCallback)(uint8_t**, size_t*, size_t*);
typedef void (*msptiBufferCompletedCallback)(uint8_t*, size_t, size_t);
typedef void (*msptiCallback)(void*, msptiCallbackDomain, msptiCallbackId,
                              const msptiCallbackData*);

static msptiBufferRequestedCallback mspti_stub_requested = NULL;
static msptiBufferCompletedCallback mspti_stub_completed = NULL;

static inline msptiResult msptiActivityRegisterCallbacks(
    msptiBufferRequestedCallback requested, msptiBufferCompletedCallback completed) {
    mspti_stub_requested = requested;
    mspti_stub_completed = completed;
    return MSPTI_SUCCESS;
}
static inline msptiResult msptiSubscribe(msptiSubscriberHandle* handle,
                                         msptiCallback, void*) {
    if (handle != NULL) *handle = (void*)1;
    return MSPTI_SUCCESS;
}
static inline msptiResult msptiEnableCallback(int, msptiSubscriberHandle,
                                               msptiCallbackDomain,
                                               msptiCallbackId) {
    return MSPTI_SUCCESS;
}
static inline msptiResult msptiUnsubscribe(msptiSubscriberHandle) {
    return MSPTI_SUCCESS;
}
static inline msptiResult msptiActivityEnable(uint32_t) { return MSPTI_SUCCESS; }
static inline msptiResult msptiActivityDisable(uint32_t) { return MSPTI_SUCCESS; }
static inline msptiResult msptiActivityFlushAll(uint32_t) {
    if (mspti_stub_requested != NULL && mspti_stub_completed != NULL) {
        for (int i = 0; i < 2; ++i) {
            uint8_t* buffer = NULL;
            size_t size = 0;
            size_t max_records = 0;
            mspti_stub_requested(&buffer, &size, &max_records);
            mspti_stub_completed(buffer, size, 0);
        }
    }
    return MSPTI_SUCCESS;
}
static inline msptiResult msptiActivityGetNextRecord(uint8_t*, size_t,
                                                      msptiActivity**) {
    return MSPTI_ERROR_MAX_LIMIT_REACHED;
}

#ifdef __cplusplus
}
#endif
