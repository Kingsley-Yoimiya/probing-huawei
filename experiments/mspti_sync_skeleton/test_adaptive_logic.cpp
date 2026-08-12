// Standalone adaptive_v1 logic tests (no CANN). Build:
// g++ -std=c++17 -O1 -g -I. test_adaptive_logic.cpp -o /tmp/test_adaptive_logic && /tmp/test_adaptive_logic
#include "adaptive_logic.hpp"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <vector>

using mspti_skeleton::AdaptiveConfig;
using mspti_skeleton::AdaptiveTelemetry;
using mspti_skeleton::BoundedGapHistogram;
using mspti_skeleton::GranularityMode;
using mspti_skeleton::StreamAdaptiveState;

static int failures = 0;

#define EXPECT_TRUE(expr)                                                     \
    do {                                                                      \
        if (!(expr)) {                                                        \
            std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #expr); \
            failures += 1;                                                    \
        }                                                                     \
    } while (0)

#define EXPECT_EQ(a, b)                                                       \
    do {                                                                      \
        const auto _a = (a);                                                  \
        const auto _b = (b);                                                  \
        if (!(_a == _b)) {                                                    \
            std::fprintf(stderr, "FAIL %s:%d: %s == %s (%g vs %g)\n", __FILE__, \
                       __LINE__, #a, #b, static_cast<double>(_a),             \
                       static_cast<double>(_b));                               \
            failures += 1;                                                    \
        }                                                                     \
    } while (0)

#define EXPECT_NEAR(a, b, eps)                                                \
    do {                                                                      \
        const double _a = static_cast<double>(a);                             \
        const double _b = static_cast<double>(b);                             \
        if (std::fabs(_a - _b) > (eps)) {                                     \
            std::fprintf(stderr, "FAIL %s:%d: |%s-%s| > %s\n", __FILE__, __LINE__, \
                       #a, #b, #eps);                                         \
            failures += 1;                                                    \
        }                                                                     \
    } while (0)

static AdaptiveConfig AdaptiveV1Config() {
    AdaptiveConfig cfg;
    cfg.mode = GranularityMode::kAdaptiveV1;
    cfg.static_gap_us = 50;
    cfg.target_kseg_ratio = 0.08;
    cfg.adapt_min_samples = 32;
    cfg.adapt_every = 64;
    cfg.gap_min_us = 10;
    cfg.gap_max_us = 200;
    return cfg;
}

static void TestHistogramQuantile() {
    BoundedGapHistogram hist(10, 200);
    const std::vector<uint32_t> values = {12, 15, 18, 20, 25, 30, 40, 100, 150, 180};
    for (uint32_t value : values) {
        hist.Add(value);
    }
    EXPECT_EQ(hist.Quantile(0.92), 150.0);
    EXPECT_EQ(hist.Quantile(0.5), 25.0);
}

static void TestRateLimit() {
    AdaptiveConfig cfg = AdaptiveV1Config();
    cfg.adapt_min_samples = 1;
    cfg.adapt_every = 1;
    StreamAdaptiveState state(0, 1, cfg);
    state.SetThresholdUsForTest(100.0);
    state.ObservePositiveGap(400);
    state.MaybeUpdateThreshold();
    EXPECT_EQ(state.threshold_us(), 200.0);
    state.ObservePositiveGap(5);
    state.MaybeUpdateThreshold();
    EXPECT_EQ(state.threshold_us(), 100.0);
}

static void TestBootstrapUsesStaticGap() {
    AdaptiveConfig cfg = AdaptiveV1Config();
    cfg.adapt_min_samples = 5;
    StreamAdaptiveState state(0, 1, cfg);
    for (uint32_t gap : {12, 14, 16, 18}) {
        state.ObservePositiveGap(gap);
        state.OnRawKernel();
    }
    EXPECT_EQ(state.EffectiveThresholdUs(), 50.0);
}

static void TestThresholdUpdatesAfterEnoughSamples() {
    AdaptiveConfig cfg = AdaptiveV1Config();
    cfg.adapt_min_samples = 4;
    cfg.adapt_every = 2;
    StreamAdaptiveState state(0, 1, cfg);
    for (uint32_t gap : {12, 14, 16, 18, 20, 22}) {
        state.ObservePositiveGap(gap);
        state.OnRawKernel();
    }
    EXPECT_TRUE(state.threshold_updates() > 0);
}

static void TestTelemetryRollup() {
    AdaptiveTelemetry telemetry(AdaptiveV1Config());
    auto& left = telemetry.StreamState(0, 1);
    auto& right = telemetry.StreamState(0, 2);
    left.OnRawKernel();
    right.OnRawKernel();
    left.RecordKseg();
    EXPECT_EQ(telemetry.TotalRawKernels(), 2U);
    EXPECT_EQ(telemetry.TotalKseg(), 1U);
}

int main() {
    TestHistogramQuantile();
    TestRateLimit();
    TestBootstrapUsesStaticGap();
    TestThresholdUpdatesAfterEnoughSamples();
    TestTelemetryRollup();
    if (failures == 0) {
        std::printf("ADAPTIVE_LOGIC_OK\n");
        return 0;
    }
    std::fprintf(stderr, "ADAPTIVE_LOGIC_FAIL count=%d\n", failures);
    return 1;
}
