// 纯逻辑：KSEG 区间并集与 merge 判定。可在无 CANN 环境下单测。
#pragma once

#include <algorithm>
#include <cstdint>
#include <utility>
#include <vector>

namespace mspti_skeleton {

inline uint64_t IntervalUnionActiveNs(
    std::vector<std::pair<uint64_t, uint64_t>> intervals) {
    if (intervals.empty()) {
        return 0;
    }
    std::sort(intervals.begin(), intervals.end());
    uint64_t active = 0;
    uint64_t cur_s = intervals[0].first;
    uint64_t cur_e = intervals[0].second;
    if (cur_e < cur_s) {
        cur_e = cur_s;
    }
    for (size_t i = 1; i < intervals.size(); ++i) {
        uint64_t s = intervals[i].first;
        uint64_t e = intervals[i].second;
        if (e < s) {
            e = s;
        }
        if (s <= cur_e) {
            cur_e = std::max(cur_e, e);
        } else {
            active += cur_e - cur_s;
            cur_s = s;
            cur_e = e;
        }
    }
    active += cur_e - cur_s;
    return active;
}

struct KsegAccumulator {
    bool open = false;
    uint64_t start_ns = 0;
    uint64_t end_ns = 0;
    uint64_t count = 0;
    int64_t step = -1;
    std::vector<std::pair<uint64_t, uint64_t>> intervals;

    // 迟到且完全位于当前 segment 前方的非重叠 kernel：用两侧真实 gap 判定，
    // 禁止把 gap_after 置 0 强行合并。
    bool TryMerge(uint64_t s, uint64_t e, int64_t event_step, uint64_t gap_ns) {
        if (e < s) {
            e = s;
        }
        if (!open) {
            open = true;
            start_ns = s;
            end_ns = e;
            count = 1;
            step = event_step;
            intervals.clear();
            intervals.emplace_back(s, e);
            return true;
        }
        if (event_step != step) {
            return false;
        }
        const bool completely_before = e <= start_ns;
        const bool completely_after = s >= end_ns;
        const bool overlaps = !completely_before && !completely_after;
        uint64_t gap = 0;
        if (completely_after) {
            gap = s - end_ns;
        } else if (completely_before) {
            gap = start_ns - e;
        } else {
            gap = 0;
        }
        if (!(overlaps || gap <= gap_ns)) {
            return false;
        }
        start_ns = std::min(start_ns, s);
        end_ns = std::max(end_ns, e);
        intervals.emplace_back(s, e);
        count += 1;
        return true;
    }

    void Finalize(uint64_t* active_ns, uint64_t* span_ns, uint64_t* gap_ns) const {
        const uint64_t span = end_ns >= start_ns ? end_ns - start_ns : 0;
        uint64_t active = IntervalUnionActiveNs(intervals);
        if (active > span) {
            active = span;
        }
        *active_ns = active;
        *span_ns = span;
        *gap_ns = span >= active ? span - active : 0;
    }
};

}  // namespace mspti_skeleton
