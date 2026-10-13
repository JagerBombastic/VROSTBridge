# CMake toolchain for cross-building Windows x64 dependencies (oneVPL) from Linux
# with clang-cl/lld-link and an xwin-splatted MSVC CRT + Windows SDK.
#   cmake -S libvpl -B build -G Ninja -DCMAKE_TOOLCHAIN_FILE=scripts/cross/clang-cl-msvc.cmake \
#     -DCMAKE_MODULE_PATH=scripts/cross/modules -DCMAKE_POLICY_DEFAULT_CMP0091=NEW \
#     -DCMAKE_BUILD_TYPE=Release -DUSE_MSVC_STATIC_RUNTIME=ON -DBUILD_SHARED_LIBS=OFF \
#     -DBUILD_TESTS=OFF -DBUILD_EXAMPLES=OFF -DINSTALL_EXAMPLES=OFF -DBUILD_TOOLS=OFF \
#     -DCMAKE_INSTALL_PREFIX=<ALVR>/deps/windows/libvpl/alvr_build
# Environment: LLVM_BIN (directory with clang-cl, lld-link, llvm-lib, llvm-rc, llvm-mt)
# and XWIN_SDK (xwin splat output with crt/ and sdk/).
set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR AMD64)
set(LLVM $ENV{LLVM_BIN})
set(XWIN $ENV{XWIN_SDK})
if(NOT LLVM OR NOT XWIN)
  message(FATAL_ERROR "Set LLVM_BIN and XWIN_SDK")
endif()
set(CMAKE_C_COMPILER ${LLVM}/clang-cl)
set(CMAKE_CXX_COMPILER ${LLVM}/clang-cl)
set(CMAKE_LINKER ${LLVM}/lld-link)
set(CMAKE_AR ${LLVM}/llvm-lib)
set(CMAKE_RC_COMPILER ${LLVM}/llvm-rc)
set(CMAKE_MT ${LLVM}/llvm-mt)
set(CMAKE_C_COMPILER_TARGET x86_64-pc-windows-msvc)
set(CMAKE_CXX_COMPILER_TARGET x86_64-pc-windows-msvc)
set(_inc "/imsvc ${XWIN}/crt/include /imsvc ${XWIN}/sdk/include/ucrt /imsvc ${XWIN}/sdk/include/um /imsvc ${XWIN}/sdk/include/shared /imsvc ${XWIN}/sdk/include/winrt")
set(CMAKE_C_FLAGS_INIT "${_inc}")
set(CMAKE_CXX_FLAGS_INIT "${_inc} /EHsc")
set(_lib "/libpath:${XWIN}/crt/lib/x86_64 /libpath:${XWIN}/sdk/lib/um/x86_64 /libpath:${XWIN}/sdk/lib/ucrt/x86_64")
set(CMAKE_EXE_LINKER_FLAGS_INIT "${_lib}")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${_lib}")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "${_lib}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
# xwin omits the debug CRT; probe the compiler in Release.
set(CMAKE_TRY_COMPILE_CONFIGURATION Release)
