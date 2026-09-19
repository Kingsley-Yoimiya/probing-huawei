#include <cstdint>

extern "C" int mspti_skeleton_start(const char*, int, int, uint64_t, uint64_t) noexcept;
extern "C" int mspti_skeleton_finalize() noexcept;

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const int start_rc = mspti_skeleton_start(argv[1], 0, 0, 50000, 1000000);
    if (start_rc != 0) return 10 + start_rc;
    const int finalize_rc = mspti_skeleton_finalize();
    return finalize_rc == 0 ? 0 : 100 + finalize_rc;
}
