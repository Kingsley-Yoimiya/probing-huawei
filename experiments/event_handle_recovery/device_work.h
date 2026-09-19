#pragma once

#include <acl/acl_rt.h>
#include <cstdint>

#ifdef __cplusplus
extern "C" {
#endif

void acl_event_work_prepare(void);
void acl_event_work_arm(void);
void acl_event_work_disarm(void);
void acl_event_work_cleanup(void);

// Returns launch rc when inject attempted; 0 if skipped.
int acl_event_work_try_launch(aclrtStream stream, uint64_t* host_enter_ns, uint64_t* host_exit_ns);

bool acl_event_work_should_inject(int active_record_success_ord, uint32_t record_issuing_tid);
bool acl_event_work_should_inject_after_wait(int active_wait_success_ord, uint32_t wait_issuing_tid);
bool acl_event_work_inject_site_is_after_wait(void);
void acl_event_work_set_record_issuing_tid(uint32_t tid);
void acl_event_work_on_inject_claim(uint64_t preload_cs, uint64_t raw_event, uint64_t raw_stream);
void acl_event_work_on_wait_inject_claim(uint64_t preload_cs, uint64_t raw_event, uint64_t raw_stream);
void acl_event_work_on_inject_record_rc(int32_t rc);
void acl_event_work_on_inject_wait_rc(int32_t rc);
void acl_event_work_record_sidecar_snapshot(uint64_t preload_cs);
void acl_event_work_write_audit(void);

// Chain-out smoke: sync launch + scratch readback (not on inject hot path).
uint32_t acl_event_work_chainout_checksum(uint32_t iters, int32_t* out_launch_rc);

// V4.4 GM proof: nonce-driven init, full scratch D2H after single launch.
int32_t acl_event_work_chainout_proof(uint32_t iters, uint32_t runtime_nonce,
                                      uint32_t* out_scratch, uint32_t out_elems,
                                      int32_t* out_launch_rc);

#ifdef __cplusplus
}
#endif
