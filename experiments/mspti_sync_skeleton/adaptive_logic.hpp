// Deterministic adaptive KSEG gap thresholding for the CANN native collector.
// Contract mirrors python/probing/profiling/npu_sync/adaptive.py.
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace mspti_skeleton {

constexpr int kAdaptiveAbiVersion = 1;

enum class GranularityMode : int32_t {
    kStatic = 0,
    kAdaptiveV1 = 1,
};

inline double ClampDouble(double value, double low, double high) {
    return std::max(low, std::min(value, high));
}

struct AdaptiveConfig {
    GranularityMode mode = GranularityMode::kStatic;
    uint32_t static_gap_us = 50;
    double target_kseg_ratio = 0.08;
    uint32_t adapt_min_samples = 128;
    uint32_t adapt_every = 256;
    uint32_t gap_min_us = 10;
    uint32_t gap_max_us = 200;
};

class BoundedGapHistogram {
public:
    BoundedGapHistogram(uint32_t min_us, uint32_t max_us)
        : min_us_(min_us), max_us_(max_us), counts_(max_us >= min_us ? max_us - min_us + 1 : 1, 0) {}

    void Add(uint32_t gap_us) {
        if (gap_us == 0) {
            return;
        }
        total_ += 1;
        if (gap_us < min_us_) {
            counts_[0] += 1;
        } else if (gap_us > max_us_) {
            counts_.back() += 1;
        } else {
            counts_[gap_us - min_us_] += 1;
        }
    }

    uint64_t Total() const { return total_; }

    double Quantile(double q) const {
        if (total_ == 0) {
            return static_cast<double>(min_us_);
        }
        q = ClampDouble(q, 0.0, 1.0);
        uint64_t target = static_cast<uint64_t>(total_ * q);
        if (target < 1) {
            target = 1;
        }
        uint64_t seen = 0;
        for (size_t index = 0; index < counts_.size(); ++index) {
            seen += counts_[index];
            if (seen >= target) {
                return static_cast<double>(min_us_ + index);
            }
        }
        return static_cast<double>(max_us_);
    }

private:
    uint32_t min_us_;
    uint32_t max_us_;
    std::vector<uint64_t> counts_;
    uint64_t total_ = 0;
};

struct ThresholdHistoryEntry {
    uint64_t raw_index = 0;
    uint32_t device_id = 0;
    uint32_t stream_id = 0;
    double threshold_us = 0.0;
};

class StreamAdaptiveState {
public:
    StreamAdaptiveState(uint32_t device_id, uint32_t stream_id, const AdaptiveConfig& config)
        : device_id_(device_id),
          stream_id_(stream_id),
          config_(config),
          histogram_(config.gap_min_us, config.gap_max_us),
          threshold_us_(static_cast<double>(config.static_gap_us)) {}

    void ObservePositiveGap(uint32_t gap_us) {
        positive_gaps_ += 1;
        histogram_.Add(gap_us);
    }

    void OnRawKernel() {
        raw_kernels_ += 1;
        if (config_.mode != GranularityMode::kAdaptiveV1) {
            return;
        }
        raw_since_adapt_ += 1;
        if (raw_since_adapt_ < config_.adapt_every) {
            return;
        }
        raw_since_adapt_ = 0;
        MaybeUpdateThreshold();
    }

    double EffectiveThresholdUs() const {
        if (config_.mode == GranularityMode::kStatic) {
            return static_cast<double>(config_.static_gap_us);
        }
        if (positive_gaps_ < config_.adapt_min_samples) {
            return static_cast<double>(config_.static_gap_us);
        }
        return threshold_us_;
    }

    uint64_t EffectiveThresholdNs() const {
        const double us = EffectiveThresholdUs();
        return static_cast<uint64_t>(us * 1000.0);
    }

    void RecordKseg() { kseg_count_ += 1; }

    uint32_t device_id() const { return device_id_; }
    uint32_t stream_id() const { return stream_id_; }
    uint64_t raw_kernels() const { return raw_kernels_; }
    uint64_t kseg_count() const { return kseg_count_; }
    uint32_t threshold_updates() const { return threshold_updates_; }
    uint64_t positive_gaps() const { return positive_gaps_; }
    double threshold_us() const { return threshold_us_; }

    void SetThresholdUsForTest(double value) { threshold_us_ = value; }

    bool MaybeUpdateThreshold() {
        if (config_.mode != GranularityMode::kAdaptiveV1) {
            return false;
        }
        if (positive_gaps_ < config_.adapt_min_samples) {
            return false;
        }
        const double q = 1.0 - config_.target_kseg_ratio;
        double candidate = histogram_.Quantile(q);
        candidate = ClampDouble(candidate, static_cast<double>(config_.gap_min_us),
                                static_cast<double>(config_.gap_max_us));
        const double prev = threshold_us_;
        const double limited =
            ClampDouble(candidate, prev * 0.5, prev * 2.0);
        threshold_us_ = limited;
        threshold_updates_ += 1;
        return true;
    }

private:
    uint32_t device_id_;
    uint32_t stream_id_;
    AdaptiveConfig config_;
    BoundedGapHistogram histogram_;
    uint64_t positive_gaps_ = 0;
    uint32_t raw_since_adapt_ = 0;
    double threshold_us_;
    uint64_t raw_kernels_ = 0;
    uint64_t kseg_count_ = 0;
    uint32_t threshold_updates_ = 0;
};

class AdaptiveTelemetry {
public:
    explicit AdaptiveTelemetry(AdaptiveConfig config) : config_(std::move(config)) {
        initial_gap_us_ = static_cast<double>(config_.static_gap_us);
    }

    StreamAdaptiveState& StreamState(uint32_t device_id, uint32_t stream_id) {
        const uint64_t key = (static_cast<uint64_t>(device_id) << 32U) | stream_id;
        auto it = streams_.find(key);
        if (it == streams_.end()) {
            it = streams_
                     .emplace(key, StreamAdaptiveState(device_id, stream_id, config_))
                     .first;
            if (initial_gap_us_ == 0.0) {
                initial_gap_us_ = static_cast<double>(config_.static_gap_us);
            }
        }
        return it->second;
    }

    void NoteThresholdUpdate(uint64_t raw_index, const StreamAdaptiveState& stream) {
        threshold_history_.push_back(
            ThresholdHistoryEntry{raw_index, stream.device_id(), stream.stream_id(),
                                  stream.threshold_us()});
        RecomputeThresholdUpdates();
    }

    uint64_t TotalRawKernels() const {
        uint64_t total = 0;
        for (const auto& entry : streams_) {
            total += entry.second.raw_kernels();
        }
        return total;
    }

    uint64_t TotalKseg() const {
        uint64_t total = 0;
        for (const auto& entry : streams_) {
            total += entry.second.kseg_count();
        }
        return total;
    }

    double ActualRatio() const {
        const uint64_t raw = TotalRawKernels();
        return raw == 0 ? 0.0 : static_cast<double>(TotalKseg()) / static_cast<double>(raw);
    }

    double FinalGapUs() const {
        if (streams_.empty()) {
            return static_cast<double>(config_.static_gap_us);
        }
        double max_threshold = 0.0;
        for (const auto& entry : streams_) {
            max_threshold = std::max(max_threshold, entry.second.EffectiveThresholdUs());
        }
        return max_threshold;
    }

    uint32_t ThresholdUpdates() const { return threshold_updates_; }

    const AdaptiveConfig& config() const { return config_; }
    const std::unordered_map<uint64_t, StreamAdaptiveState>& streams() const { return streams_; }
    const std::vector<ThresholdHistoryEntry>& threshold_history() const {
        return threshold_history_;
    }

    double initial_gap_us() const { return initial_gap_us_; }

private:
    void RecomputeThresholdUpdates() {
        threshold_updates_ = 0;
        for (const auto& entry : streams_) {
            threshold_updates_ += entry.second.threshold_updates();
        }
    }

    AdaptiveConfig config_;
    std::unordered_map<uint64_t, StreamAdaptiveState> streams_;
    std::vector<ThresholdHistoryEntry> threshold_history_;
    double initial_gap_us_ = 0.0;
    uint32_t threshold_updates_ = 0;
};

}  // namespace mspti_skeleton
