// 本地单测：g++ -std=c++17 -O2 -fsanitize=address,undefined test_kseg_logic.cpp -o /tmp/test_kseg_logic && /tmp/test_kseg_logic
#include "kseg_logic.hpp"

#include <cassert>
#include <cstdio>
#include <cstdlib>

using mspti_skeleton::IntervalUnionActiveNs;
using mspti_skeleton::KsegAccumulator;

static void ExpectEq(uint64_t got, uint64_t want, const char* name) {
    if (got != want) {
        std::fprintf(stderr, "FAIL %s: got=%llu want=%llu\n", name,
                     static_cast<unsigned long long>(got),
                     static_cast<unsigned long long>(want));
        std::exit(1);
    }
}

static void CheckInvariants(const KsegAccumulator& seg, const char* name) {
    uint64_t active = 0, span = 0, gap = 0;
    seg.Finalize(&active, &span, &gap);
    if (active > span) {
        std::fprintf(stderr, "FAIL %s active>span\n", name);
        std::exit(1);
    }
    if (gap != (span >= active ? span - active : 0)) {
        std::fprintf(stderr, "FAIL %s gap mismatch\n", name);
        std::exit(1);
    }
}

int main() {
    {
        auto a = IntervalUnionActiveNs({{0, 100}, {20, 40}});
        ExpectEq(a, 100, "nested");
    }
    {
        auto a = IntervalUnionActiveNs({{0, 50}, {40, 90}});
        ExpectEq(a, 90, "overlap");
    }
    {
        auto a = IntervalUnionActiveNs({{40, 90}, {0, 50}, {100, 110}});
        ExpectEq(a, 100, "unordered");
    }
    {
        auto a = IntervalUnionActiveNs({{0, 50}, {50, 80}});
        ExpectEq(a, 80, "adjacent");
    }
    {
        auto a = IntervalUnionActiveNs({{0, 10}, {100, 110}});
        ExpectEq(a, 20, "large_gap_union");
    }

    // 正序 + 大 gap 不 merge
    {
        KsegAccumulator seg;
        assert(seg.TryMerge(0, 10, 1, 50));
        assert(!seg.TryMerge(100, 110, 1, 50));
        CheckInvariants(seg, "forward_large_gap");
    }
    // 倒序远距：完全在前方且 gap 大 → 不 merge（禁止 gap_after=0 强并）
    {
        KsegAccumulator seg;
        assert(seg.TryMerge(100, 110, 1, 50));
        assert(!seg.TryMerge(0, 10, 1, 50));  // gap=90
        CheckInvariants(seg, "late_before_far");
        ExpectEq(seg.start_ns, 100, "late_before_far_start");
        ExpectEq(seg.count, 1, "late_before_far_count");
    }
    // 倒序邻近：完全在前方但 gap 小 → merge
    {
        KsegAccumulator seg;
        assert(seg.TryMerge(100, 110, 1, 50));
        assert(seg.TryMerge(60, 90, 1, 50));  // gap=10
        CheckInvariants(seg, "late_before_near");
        ExpectEq(seg.start_ns, 60, "late_before_near_start");
        ExpectEq(seg.end_ns, 110, "late_before_near_end");
        ExpectEq(seg.count, 2, "late_before_near_count");
        uint64_t active = 0, span = 0, gap = 0;
        seg.Finalize(&active, &span, &gap);
        ExpectEq(span, 50, "late_before_near_span");
        ExpectEq(active, 40, "late_before_near_active");  // [60,90]+[100,110]
        ExpectEq(gap, 10, "late_before_near_gap");
    }
    // 重叠 / 嵌套
    {
        KsegAccumulator seg;
        assert(seg.TryMerge(0, 100, 1, 50));
        assert(seg.TryMerge(20, 40, 1, 50));
        assert(seg.TryMerge(90, 120, 1, 50));
        CheckInvariants(seg, "overlap_nested");
        uint64_t active = 0, span = 0, gap = 0;
        seg.Finalize(&active, &span, &gap);
        ExpectEq(span, 120, "overlap_nested_span");
        ExpectEq(active, 120, "overlap_nested_active");
        ExpectEq(gap, 0, "overlap_nested_gap");
    }
    // 乱序 watermark 风格：先远后近
    {
        KsegAccumulator seg;
        assert(seg.TryMerge(200, 210, 1, 30));
        assert(!seg.TryMerge(0, 10, 1, 30));
        assert(seg.TryMerge(180, 195, 1, 30));  // gap=5
        CheckInvariants(seg, "watermark_reorder");
    }
    // 多 stream：各自独立 accumulator（本测试模拟两路）
    {
        KsegAccumulator s0, s1;
        assert(s0.TryMerge(0, 10, 1, 50));
        assert(s1.TryMerge(1000, 1010, 1, 50));
        assert(!s0.TryMerge(100, 110, 1, 50));
        assert(s1.TryMerge(1020, 1030, 1, 50));  // gap=10
        CheckInvariants(s0, "multi_stream_0");
        CheckInvariants(s1, "multi_stream_1");
        ExpectEq(s0.count, 1, "multi_stream_0_count");
        ExpectEq(s1.count, 2, "multi_stream_1_count");
    }
    // 大 gap 正序再接邻近
    {
        KsegAccumulator seg;
        assert(seg.TryMerge(0, 10, 1, 20));
        assert(!seg.TryMerge(100, 110, 1, 20));
        // 新 segment
        KsegAccumulator seg2;
        assert(seg2.TryMerge(100, 110, 1, 20));
        assert(seg2.TryMerge(115, 125, 1, 20));
        CheckInvariants(seg, "large_gap_left");
        CheckInvariants(seg2, "large_gap_right");
    }
    // step 不同不 merge
    {
        KsegAccumulator seg;
        assert(seg.TryMerge(0, 10, 1, 50));
        assert(!seg.TryMerge(12, 20, 2, 50));
        CheckInvariants(seg, "step_barrier");
    }
    std::puts("OK test_kseg_logic");
    return 0;
}
