cmake_minimum_required(VERSION 3.16)
project(mspti_sync_skeleton LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

set(CANN_ROOT "/usr/local/Ascend/cann-8.5.0" CACHE PATH "CANN 根目录")

add_library(mspti_sync_skeleton SHARED collector.cpp)
target_include_directories(mspti_sync_skeleton PRIVATE "${CANN_ROOT}/include")
target_link_directories(mspti_sync_skeleton PRIVATE "${CANN_ROOT}/lib64")
target_link_libraries(mspti_sync_skeleton PRIVATE mspti pthread)
target_compile_options(mspti_sync_skeleton PRIVATE -Wall -Wextra -Wpedantic -O2)
set_target_properties(
  mspti_sync_skeleton
  PROPERTIES
    BUILD_RPATH "${CANN_ROOT}/lib64"
    INSTALL_RPATH "${CANN_ROOT}/lib64"
)

add_library(mspti_sync_interpose SHARED sync_interpose.cpp)
target_include_directories(mspti_sync_interpose PRIVATE "${CANN_ROOT}/include")
target_link_libraries(mspti_sync_interpose PRIVATE dl)
target_compile_options(mspti_sync_interpose PRIVATE -Wall -Wextra -Wpedantic -O2)

include(CTest)
if(BUILD_TESTING)
  add_executable(mspti_buffer_audit_test test_buffer_audit.cpp)
  target_compile_options(mspti_buffer_audit_test PRIVATE -Wall -Wextra -Wpedantic -O2)
  add_test(NAME mspti_buffer_audit_test COMMAND mspti_buffer_audit_test)
endif()
