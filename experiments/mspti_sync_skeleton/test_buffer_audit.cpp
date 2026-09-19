#include "buffer_audit.hpp"

#include <cassert>
#include <string>

using mspti_skeleton::AuditRawKind;
using mspti_skeleton::AuditContentStage;
using mspti_skeleton::AuditLifecyclePoint;
using mspti_skeleton::BufferAuditCounters;
using mspti_skeleton::BufferAuditPathForOutput;
using mspti_skeleton::CheckBufferAuditConservation;
using mspti_skeleton::MalformedTimestampSample;

int main() {
    BufferAuditCounters counters;
    int first_buffer = 0;
    int second_buffer = 0;
    const auto first = counters.BeginBufferRequest(
        &first_buffer, 1, 8192, 100, 1);
    const auto second = counters.BeginBufferRequest(
        &second_buffer, 1, 8192, 110, 2);
    counters.NoteBufferCompleteCallback();
    counters.NoteBufferCompleteCallback();
    assert(counters.NoteOwnedBufferComplete(first, 8192, 4096, 200) == 1);
    assert(counters.NoteOwnedBufferComplete(second, 8192, 2048, 220) == 2);
    counters.NoteLedgerTerminal(first, 7, 4);
    counters.NoteLedgerTerminal(second, 7, 2);

    for (int i = 0; i < 3; ++i) {
        const uint64_t hash = 100 + static_cast<uint64_t>(i);
        counters.NoteParsed(AuditRawKind::kKernel);
        counters.NoteWorkerConsumed(AuditRawKind::kKernel);
        counters.NoteProcessed(AuditRawKind::kKernel);
        for (uint32_t stage = 0;
             stage < static_cast<uint32_t>(AuditContentStage::kCount); ++stage) {
            counters.NoteContent(static_cast<AuditContentStage>(stage),
                                 AuditRawKind::kKernel, hash);
        }
    }
    counters.NoteEmittedKseg(3);
    counters.NoteParsed(AuditRawKind::kCommunication);
    counters.NoteWorkerConsumed(AuditRawKind::kCommunication);
    counters.NoteProcessed(AuditRawKind::kCommunication);
    for (uint32_t stage = 0;
         stage < static_cast<uint32_t>(AuditContentStage::kCount); ++stage) {
        counters.NoteContent(static_cast<AuditContentStage>(stage),
                             AuditRawKind::kCommunication, 200);
    }
    counters.NoteEmittedComm();
    counters.NoteEmittedTotal();
    counters.NoteCallbackWall(first, 10);
    counters.NoteCallbackWall(second, 30);
    counters.NoteLifecycleSnapshot(
        AuditLifecyclePoint::kBeforeDisable, 300, 0, 0, false);
    counters.NoteLifecycleSnapshot(
        AuditLifecyclePoint::kAfterFlush, 310, 0, 0, false);
    counters.NoteLifecycleSnapshot(
        AuditLifecyclePoint::kBeforeFree, 320, 0, 0, false);

    auto snapshot = counters.Snapshot();
    assert(snapshot.max_inflight == 2);
    assert(snapshot.callback_wall_total_ns == 40);
    assert(snapshot.callback_wall_max_ns == 30);
    assert(snapshot.ledger_entry_count == 2);
    assert(snapshot.ledger_completed_count == 2);
    assert(snapshot.buffer_ledger[0].valid_size == 4096);
    assert(snapshot.buffer_ledger[1].callback_wall_ns == 30);
    assert(snapshot.lifecycle_snapshots[2].captured);
    auto conservation = CheckBufferAuditConservation(
        snapshot, /*enqueued_kernel=*/3, /*enqueued_comm=*/1,
        /*inflight_final=*/0);
    assert(conservation.all_ok);

    counters.NoteParsed(AuditRawKind::kKernel);
    counters.NoteContent(AuditContentStage::kParsed, AuditRawKind::kKernel, 999);
    counters.NoteQueueRejected(AuditRawKind::kKernel);
    snapshot = counters.Snapshot();
    conservation = CheckBufferAuditConservation(
        snapshot, /*enqueued_kernel=*/3, /*enqueued_comm=*/1,
        /*inflight_final=*/0);
    assert(conservation.kernel_parse_queue_balanced);
    assert(!conservation.fingerprint_all_ok);
    assert(!conservation.all_ok);

    counters.NoteParsedUnknown();
    snapshot = counters.Snapshot();
    conservation = CheckBufferAuditConservation(snapshot, 3, 1, 0);
    assert(!conservation.unknown_kind_zero);
    assert(!conservation.all_ok);

    MalformedTimestampSample sample;
    sample.raw_kind = static_cast<uint32_t>(AuditRawKind::kCommunication);
    sample.start_ns = 20;
    sample.end_ns = 10;
    counters.NoteMalformedTimestamp(sample);
    snapshot = counters.Snapshot();
    assert(snapshot.malformed_timestamp_count == 1);
    assert(snapshot.malformed_sample_count == 1);
    assert(snapshot.malformed_samples[0].start_ns == 20);

    assert(BufferAuditPathForOutput("/tmp/rank_0007.skeleton.jsonl") ==
           "/tmp/rank_0007.buffer_audit.json");
    assert(BufferAuditPathForOutput("/tmp/custom.out") ==
           "/tmp/custom.out.buffer_audit.json");

    counters.Reset();
    snapshot = counters.Snapshot();
    assert(snapshot.buffer_request_count == 0);
    assert(snapshot.malformed_timestamp_count == 0);
    return 0;
}
