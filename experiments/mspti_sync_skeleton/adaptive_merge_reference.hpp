// Portable merge reference mirroring python/probing/profiling/npu_sync/adaptive.py
#pragma once

#include "adaptive_logic.hpp"

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace mspti_skeleton {

struct RawKernel {
    uint32_t device_id = 0;
    uint32_t stream_id = 0;
    uint64_t start_ns = 0;
    uint64_t end_ns = 0;
};

struct BoundaryEvent {
    std::string kind;
    int step = -1;
    int device_id = -1;
    int stream_id = -1;
    uint64_t start_ns = 0;
    uint64_t end_ns = 0;
    std::string op;
    std::string comm_name;
    std::string flags;
};

struct OpenKseg {
    uint32_t device_id = 0;
    uint32_t stream_id = 0;
    uint64_t start_ns = 0;
    uint64_t end_ns = 0;
    uint32_t count = 1;

    void Absorb(uint64_t start, uint64_t end) {
        end_ns = std::max(end_ns, end);
        count += 1;
    }
};

struct KsegRow {
    uint32_t device_id = 0;
    uint32_t stream_id = 0;
    uint32_t count = 0;
};

inline uint32_t GapUs(uint64_t prev_end_ns, uint64_t next_start_ns) {
    if (prev_end_ns == 0) {
        return 0;
    }
    if (next_start_ns <= prev_end_ns) {
        return 0;
    }
    return static_cast<uint32_t>((next_start_ns - prev_end_ns) / 1000ULL);
}

inline std::tuple<std::vector<KsegRow>, AdaptiveTelemetry> MergeKernelStream(
    const std::vector<RawKernel>& kernels,
    const AdaptiveConfig& config,
    const std::vector<BoundaryEvent>& boundaries = {}) {
    AdaptiveTelemetry telemetry(config);
    std::map<std::pair<uint32_t, uint32_t>, OpenKseg> open_by_stream;
    std::map<std::pair<uint32_t, uint32_t>, uint64_t> last_end_by_stream;
    std::vector<KsegRow> rows;
    uint64_t raw_index = 0;
    size_t boundary_index = 0;

    auto stream_key = [](uint32_t device_id, uint32_t stream_id) {
        return std::make_pair(device_id, stream_id);
    };

    auto flush_stream = [&](const std::pair<uint32_t, uint32_t>& key) {
        auto it = open_by_stream.find(key);
        if (it == open_by_stream.end()) {
            return;
        }
        auto& open_seg = it->second;
        auto& state = telemetry.StreamState(open_seg.device_id, open_seg.stream_id);
        state.RecordKseg();
        rows.push_back(
            KsegRow{open_seg.device_id, open_seg.stream_id, open_seg.count});
        open_by_stream.erase(it);
    };

    auto flush_all = [&]() {
        std::vector<std::pair<uint32_t, uint32_t>> keys;
        keys.reserve(open_by_stream.size());
        for (const auto& entry : open_by_stream) {
            keys.push_back(entry.first);
        }
        for (const auto& key : keys) {
            flush_stream(key);
        }
    };

    auto emit_boundary = [&](const BoundaryEvent& event) {
        flush_all();
        (void)event;
    };

    for (const auto& kernel : kernels) {
        while (boundary_index < boundaries.size() &&
               boundaries[boundary_index].start_ns <= kernel.start_ns) {
            emit_boundary(boundaries[boundary_index]);
            boundary_index += 1;
        }

        const auto key = stream_key(kernel.device_id, kernel.stream_id);
        auto& state = telemetry.StreamState(kernel.device_id, kernel.stream_id);
        const uint64_t prev_end = last_end_by_stream.count(key) ? last_end_by_stream[key] : 0;
        const uint32_t gap_us = GapUs(prev_end, kernel.start_ns);
        if (gap_us > 0) {
            state.ObservePositiveGap(gap_us);
        }

        const double threshold = state.EffectiveThresholdUs();
        auto open_it = open_by_stream.find(key);
        const bool must_break =
            open_it != open_by_stream.end() && static_cast<double>(gap_us) > threshold;
        if (must_break) {
            flush_stream(key);
            open_it = open_by_stream.end();
        }

        if (open_it == open_by_stream.end()) {
            OpenKseg seg;
            seg.device_id = kernel.device_id;
            seg.stream_id = kernel.stream_id;
            seg.start_ns = kernel.start_ns;
            seg.end_ns = kernel.end_ns;
            open_by_stream[key] = seg;
        } else {
            open_it->second.Absorb(kernel.start_ns, kernel.end_ns);
        }

        const uint32_t updates_before = state.threshold_updates();
        state.OnRawKernel();
        if (state.threshold_updates() > updates_before) {
            telemetry.NoteThresholdUpdate(raw_index, state);
        }
        last_end_by_stream[key] = kernel.end_ns;
        raw_index += 1;
    }

    while (boundary_index < boundaries.size()) {
        emit_boundary(boundaries[boundary_index]);
        boundary_index += 1;
    }
    flush_all();
    return {rows, telemetry};
}

}  // namespace mspti_skeleton
