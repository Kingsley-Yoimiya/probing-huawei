// Cross-language adaptive parity driver (reads shared JSON fixture, prints summary JSON).
// Build: g++ -std=c++17 -O1 -g -I. test_adaptive_parity.cpp -o /tmp/test_adaptive_parity
#include "adaptive_merge_reference.hpp"

#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

std::string ReadFile(const std::string& path) {
    std::ifstream in(path);
    if (!in) {
        std::fprintf(stderr, "cannot open %s\n", path.c_str());
        std::exit(2);
    }
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

size_t SkipWs(const std::string& text, size_t pos) {
    while (pos < text.size() && std::isspace(static_cast<unsigned char>(text[pos]))) {
        pos += 1;
    }
    return pos;
}

bool Match(const std::string& text, size_t& pos, char ch) {
    pos = SkipWs(text, pos);
    if (pos >= text.size() || text[pos] != ch) {
        return false;
    }
    pos += 1;
    return true;
}

std::string ParseString(const std::string& text, size_t& pos) {
    pos = SkipWs(text, pos);
    if (pos >= text.size() || text[pos] != '"') {
        return "";
    }
    pos += 1;
    std::string out;
    while (pos < text.size() && text[pos] != '"') {
        out.push_back(text[pos]);
        pos += 1;
    }
    if (pos < text.size()) {
        pos += 1;
    }
    return out;
}

double ParseNumber(const std::string& text, size_t& pos) {
    pos = SkipWs(text, pos);
    size_t start = pos;
    while (pos < text.size() &&
           (std::isdigit(static_cast<unsigned char>(text[pos])) || text[pos] == '.' ||
            text[pos] == '-' || text[pos] == 'e' || text[pos] == 'E' || text[pos] == '+')) {
        pos += 1;
    }
    return std::stod(text.substr(start, pos - start));
}

void SkipValue(const std::string& text, size_t& pos) {
    pos = SkipWs(text, pos);
    if (pos >= text.size()) {
        return;
    }
    if (text[pos] == '"') {
        ParseString(text, pos);
        return;
    }
    if (text[pos] == '{') {
        pos += 1;
        while (pos < text.size()) {
            pos = SkipWs(text, pos);
            if (text[pos] == '}') {
                pos += 1;
                return;
            }
            ParseString(text, pos);
            Match(text, pos, ':');
            SkipValue(text, pos);
            pos = SkipWs(text, pos);
            if (text[pos] == ',') {
                pos += 1;
            }
        }
        return;
    }
    if (text[pos] == '[') {
        pos += 1;
        while (pos < text.size()) {
            pos = SkipWs(text, pos);
            if (text[pos] == ']') {
                pos += 1;
                return;
            }
            SkipValue(text, pos);
            pos = SkipWs(text, pos);
            if (text[pos] == ',') {
                pos += 1;
            }
        }
        return;
    }
    while (pos < text.size() && text[pos] != ',' && text[pos] != '}' && text[pos] != ']') {
        pos += 1;
    }
}

std::optional<std::string> FindObject(const std::string& text, const std::string& key) {
    const std::string needle = "\"" + key + "\"";
    const size_t found = text.find(needle);
    if (found == std::string::npos) {
        return std::nullopt;
    }
    size_t pos = found + needle.size();
    if (!Match(text, pos, ':')) {
        return std::nullopt;
    }
    pos = SkipWs(text, pos);
    if (pos >= text.size() || text[pos] != '{') {
        return std::nullopt;
    }
    size_t start = pos;
    int depth = 0;
    while (pos < text.size()) {
        if (text[pos] == '{') {
            depth += 1;
        } else if (text[pos] == '}') {
            depth -= 1;
            if (depth == 0) {
                pos += 1;
                return text.substr(start, pos - start);
            }
        }
        pos += 1;
    }
    return std::nullopt;
}

std::optional<std::string> FindArray(const std::string& text, const std::string& key) {
    const std::string needle = "\"" + key + "\"";
    const size_t found = text.find(needle);
    if (found == std::string::npos) {
        return std::nullopt;
    }
    size_t pos = found + needle.size();
    if (!Match(text, pos, ':')) {
        return std::nullopt;
    }
    pos = SkipWs(text, pos);
    if (pos >= text.size() || text[pos] != '[') {
        return std::nullopt;
    }
    size_t start = pos;
    int depth = 0;
    while (pos < text.size()) {
        if (text[pos] == '[') {
            depth += 1;
        } else if (text[pos] == ']') {
            depth -= 1;
            if (depth == 0) {
                pos += 1;
                return text.substr(start, pos - start);
            }
        }
        pos += 1;
    }
    return std::nullopt;
}

mspti_skeleton::AdaptiveConfig ParseParams(const std::string& obj) {
    mspti_skeleton::AdaptiveConfig cfg;
    size_t pos = 0;
    if (!Match(obj, pos, '{')) {
        return cfg;
    }
    while (pos < obj.size()) {
        pos = SkipWs(obj, pos);
        if (obj[pos] == '}') {
            break;
        }
        const std::string key = ParseString(obj, pos);
        Match(obj, pos, ':');
        if (key == "mode") {
            const std::string mode = ParseString(obj, pos);
            if (mode == "adaptive_v1") {
                cfg.mode = mspti_skeleton::GranularityMode::kAdaptiveV1;
            }
        } else if (key == "static_gap_us") {
            cfg.static_gap_us = static_cast<uint32_t>(ParseNumber(obj, pos));
        } else if (key == "target_kseg_ratio") {
            cfg.target_kseg_ratio = ParseNumber(obj, pos);
        } else if (key == "adapt_min_samples") {
            cfg.adapt_min_samples = static_cast<uint32_t>(ParseNumber(obj, pos));
        } else if (key == "adapt_every") {
            cfg.adapt_every = static_cast<uint32_t>(ParseNumber(obj, pos));
        } else if (key == "gap_min_us") {
            cfg.gap_min_us = static_cast<uint32_t>(ParseNumber(obj, pos));
        } else if (key == "gap_max_us") {
            cfg.gap_max_us = static_cast<uint32_t>(ParseNumber(obj, pos));
        } else {
            SkipValue(obj, pos);
        }
        pos = SkipWs(obj, pos);
        if (obj[pos] == ',') {
            pos += 1;
        }
    }
    return cfg;
}

template <typename T>
T ParseObjectFields(const std::string& obj);

template <>
mspti_skeleton::RawKernel ParseObjectFields<mspti_skeleton::RawKernel>(const std::string& obj) {
    mspti_skeleton::RawKernel out;
    size_t pos = 0;
    if (!Match(obj, pos, '{')) {
        return out;
    }
    while (pos < obj.size()) {
        pos = SkipWs(obj, pos);
        if (obj[pos] == '}') {
            break;
        }
        const std::string key = ParseString(obj, pos);
        Match(obj, pos, ':');
        if (key == "device_id") {
            out.device_id = static_cast<uint32_t>(ParseNumber(obj, pos));
        } else if (key == "stream_id") {
            out.stream_id = static_cast<uint32_t>(ParseNumber(obj, pos));
        } else if (key == "start_ns") {
            out.start_ns = static_cast<uint64_t>(ParseNumber(obj, pos));
        } else if (key == "end_ns") {
            out.end_ns = static_cast<uint64_t>(ParseNumber(obj, pos));
        } else {
            SkipValue(obj, pos);
        }
        pos = SkipWs(obj, pos);
        if (obj[pos] == ',') {
            pos += 1;
        }
    }
    return out;
}

template <>
mspti_skeleton::BoundaryEvent ParseObjectFields<mspti_skeleton::BoundaryEvent>(
    const std::string& obj) {
    mspti_skeleton::BoundaryEvent out;
    size_t pos = 0;
    if (!Match(obj, pos, '{')) {
        return out;
    }
    while (pos < obj.size()) {
        pos = SkipWs(obj, pos);
        if (obj[pos] == '}') {
            break;
        }
        const std::string key = ParseString(obj, pos);
        Match(obj, pos, ':');
        if (key == "device_id") {
            out.device_id = static_cast<int>(ParseNumber(obj, pos));
        } else if (key == "stream_id") {
            out.stream_id = static_cast<int>(ParseNumber(obj, pos));
        } else if (key == "start_ns") {
            out.start_ns = static_cast<uint64_t>(ParseNumber(obj, pos));
        } else if (key == "end_ns") {
            out.end_ns = static_cast<uint64_t>(ParseNumber(obj, pos));
        } else if (key == "kind") {
            out.kind = ParseString(obj, pos);
        } else if (key == "step") {
            out.step = static_cast<int>(ParseNumber(obj, pos));
        } else if (key == "op") {
            out.op = ParseString(obj, pos);
        } else if (key == "comm_name") {
            out.comm_name = ParseString(obj, pos);
        } else if (key == "flags") {
            out.flags = ParseString(obj, pos);
        } else {
            SkipValue(obj, pos);
        }
        pos = SkipWs(obj, pos);
        if (obj[pos] == ',') {
            pos += 1;
        }
    }
    return out;
}

std::vector<mspti_skeleton::RawKernel> ParseKernels(const std::string& arr) {
    std::vector<mspti_skeleton::RawKernel> out;
    size_t pos = 0;
    if (!Match(arr, pos, '[')) {
        return out;
    }
    while (pos < arr.size()) {
        pos = SkipWs(arr, pos);
        if (arr[pos] == ']') {
            break;
        }
        if (arr[pos] != '{') {
            SkipValue(arr, pos);
            continue;
        }
        size_t start = pos;
        int depth = 0;
        while (pos < arr.size()) {
            if (arr[pos] == '{') {
                depth += 1;
            } else if (arr[pos] == '}') {
                depth -= 1;
                if (depth == 0) {
                    pos += 1;
                    break;
                }
            }
            pos += 1;
        }
        out.push_back(ParseObjectFields<mspti_skeleton::RawKernel>(arr.substr(start, pos - start)));
        pos = SkipWs(arr, pos);
        if (arr[pos] == ',') {
            pos += 1;
        }
    }
    return out;
}

std::vector<mspti_skeleton::BoundaryEvent> ParseBoundaries(const std::string& arr) {
    std::vector<mspti_skeleton::BoundaryEvent> out;
    size_t pos = 0;
    if (!Match(arr, pos, '[')) {
        return out;
    }
    while (pos < arr.size()) {
        pos = SkipWs(arr, pos);
        if (arr[pos] == ']') {
            break;
        }
        if (arr[pos] != '{') {
            SkipValue(arr, pos);
            continue;
        }
        size_t start = pos;
        int depth = 0;
        while (pos < arr.size()) {
            if (arr[pos] == '{') {
                depth += 1;
            } else if (arr[pos] == '}') {
                depth -= 1;
                if (depth == 0) {
                    pos += 1;
                    break;
                }
            }
            pos += 1;
        }
        out.push_back(
            ParseObjectFields<mspti_skeleton::BoundaryEvent>(arr.substr(start, pos - start)));
        pos = SkipWs(arr, pos);
        if (arr[pos] == ',') {
            pos += 1;
        }
    }
    return out;
}

void PrintJson(const std::vector<mspti_skeleton::KsegRow>& rows,
               const mspti_skeleton::AdaptiveTelemetry& telemetry) {
    std::cout << "{\n";
    std::cout << "  \"total_raw_kernels\": " << telemetry.TotalRawKernels() << ",\n";
    std::cout << "  \"total_kseg\": " << telemetry.TotalKseg() << ",\n";
    std::cout << "  \"threshold_updates\": " << telemetry.ThresholdUpdates() << ",\n";
    std::cout << "  \"kseg_rows\": [";
    for (size_t i = 0; i < rows.size(); ++i) {
        if (i) {
            std::cout << ", ";
        }
        std::cout << "{\"device_id\":" << rows[i].device_id << ",\"stream_id\":"
                  << rows[i].stream_id << ",\"count\":" << rows[i].count << "}";
    }
    std::cout << "],\n";
    std::cout << "  \"streams\": [";
    bool first = true;
    for (const auto& entry : telemetry.streams()) {
        const auto& state = entry.second;
        if (!first) {
            std::cout << ", ";
        }
        first = false;
        std::cout << "{\"device_id\":" << state.device_id() << ",\"stream_id\":"
                  << state.stream_id() << ",\"raw_kernels\":" << state.raw_kernels()
                  << ",\"kseg_count\":" << state.kseg_count()
                  << ",\"threshold_updates\":" << state.threshold_updates()
                  << ",\"positive_gaps\":" << state.positive_gaps()
                  << ",\"final_threshold_us\":" << state.EffectiveThresholdUs() << "}";
    }
    std::cout << "],\n";
    std::cout << "  \"threshold_history\": [";
    const auto& history = telemetry.threshold_history();
    for (size_t i = 0; i < history.size(); ++i) {
        if (i) {
            std::cout << ", ";
        }
        std::cout << "{\"raw_index\":" << history[i].raw_index << ",\"device_id\":"
                  << history[i].device_id << ",\"stream_id\":" << history[i].stream_id
                  << ",\"threshold_us\":" << history[i].threshold_us << "}";
    }
    std::cout << "]\n}\n";
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: %s <fixture.json>\n", argv[0]);
        return 2;
    }
    const std::string text = ReadFile(argv[1]);
    const auto params_obj = FindObject(text, "params");
    const auto kernels_arr = FindArray(text, "kernels");
    const auto boundaries_arr = FindArray(text, "boundaries");
    if (!params_obj || !kernels_arr) {
        std::fprintf(stderr, "fixture missing params/kernels\n");
        return 2;
    }
    const auto config = ParseParams(*params_obj);
    const auto kernels = ParseKernels(*kernels_arr);
    std::vector<mspti_skeleton::BoundaryEvent> boundaries;
    if (boundaries_arr) {
        boundaries = ParseBoundaries(*boundaries_arr);
    }
    const auto merged = mspti_skeleton::MergeKernelStream(kernels, config, boundaries);
    PrintJson(std::get<0>(merged), std::get<1>(merged));
    return 0;
}
