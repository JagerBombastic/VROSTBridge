# CMake toolchain: the native Windows x64 client (VROSTBridgeClient.exe and its tests) cross-built on
# Linux with LLVM's clang-cl, lld-link, llvm-lib, llvm-rc and llvm-mt against a Microsoft CRT and
# Windows SDK in xwin's layout: crt/include, crt/lib/x86_64, sdk/include/{ucrt,um,shared,winrt} and
# sdk/lib/{um,ucrt}/x86_64 (a licensed, case-correct copy; xwin adds the case links).
#
#   cmake -S . -B build-windows -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/windows-clang-cl.cmake \
#     -DVROST_LLVM_BIN=/opt/llvm/bin -DVROST_WINDOWS_SDK=/opt/xwin-sdk -DCMAKE_BUILD_TYPE=Release ...
#
# scripts/Build-Client-Windows.py passes these and the client's other inputs (FFmpeg, the transport
# DLL, Vulkan headers and import libraries). The environment variables of the same names also work.
# Builds are reproducible: no timestamps (/Brepro) and no build-machine paths in the binaries.
set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_VERSION 10.0)
set(CMAKE_SYSTEM_PROCESSOR AMD64)

if(NOT VROST_LLVM_BIN AND DEFINED ENV{VROST_LLVM_BIN})
  set(VROST_LLVM_BIN "$ENV{VROST_LLVM_BIN}")
endif()
if(NOT VROST_WINDOWS_SDK AND DEFINED ENV{VROST_WINDOWS_SDK})
  set(VROST_WINDOWS_SDK "$ENV{VROST_WINDOWS_SDK}")
endif()
set(VROST_LLVM_BIN "${VROST_LLVM_BIN}" CACHE PATH "Directory with clang-cl, lld-link, llvm-lib, llvm-rc and llvm-mt")
set(VROST_WINDOWS_SDK "${VROST_WINDOWS_SDK}" CACHE PATH "Microsoft CRT and Windows SDK in xwin layout (crt/, sdk/)")
if(NOT IS_DIRECTORY "${VROST_LLVM_BIN}" OR NOT IS_DIRECTORY "${VROST_WINDOWS_SDK}/crt/include" OR
   NOT IS_DIRECTORY "${VROST_WINDOWS_SDK}/sdk/include/um")
  message(FATAL_ERROR "Set VROST_LLVM_BIN (LLVM 19 or later: clang-cl, lld-link, llvm-lib, llvm-rc, llvm-mt) and "
                      "VROST_WINDOWS_SDK (CRT and Windows SDK in xwin layout)")
endif()
# try_compile projects read this file again: hand them the same two directories.
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES VROST_LLVM_BIN VROST_WINDOWS_SDK)

set(CMAKE_C_COMPILER "${VROST_LLVM_BIN}/clang-cl")
set(CMAKE_CXX_COMPILER "${VROST_LLVM_BIN}/clang-cl")
set(CMAKE_LINKER "${VROST_LLVM_BIN}/lld-link")
set(CMAKE_AR "${VROST_LLVM_BIN}/llvm-lib")
set(CMAKE_RC_COMPILER "${VROST_LLVM_BIN}/llvm-rc")
set(CMAKE_MT "${VROST_LLVM_BIN}/llvm-mt")
set(CMAKE_C_COMPILER_TARGET x86_64-pc-windows-msvc)
set(CMAKE_CXX_COMPILER_TARGET x86_64-pc-windows-msvc)

set(_vrost_sdk "${VROST_WINDOWS_SDK}")
set(_vrost_includes "/imsvc \"${_vrost_sdk}/crt/include\" /imsvc \"${_vrost_sdk}/sdk/include/ucrt\" /imsvc \"${_vrost_sdk}/sdk/include/um\" /imsvc \"${_vrost_sdk}/sdk/include/shared\" /imsvc \"${_vrost_sdk}/sdk/include/winrt\"")
# /Brepro: no timestamps in objects or images. CMakeLists.txt maps the source and build directories
# out of __FILE__ and debug information, and the client ships without PDBs.
set(_vrost_repro "/Brepro")
set(CMAKE_C_FLAGS_INIT "${_vrost_includes} ${_vrost_repro}")
set(CMAKE_CXX_FLAGS_INIT "${_vrost_includes} ${_vrost_repro} /EHsc")
set(CMAKE_RC_FLAGS_INIT "/I \"${_vrost_sdk}/sdk/include/um\" /I \"${_vrost_sdk}/sdk/include/shared\" /I \"${_vrost_sdk}/sdk/include/ucrt\" /I \"${_vrost_sdk}/crt/include\"")
set(_vrost_libs "/libpath:\"${_vrost_sdk}/crt/lib/x86_64\" /libpath:\"${_vrost_sdk}/sdk/lib/um/x86_64\" /libpath:\"${_vrost_sdk}/sdk/lib/ucrt/x86_64\" /Brepro")
set(CMAKE_EXE_LINKER_FLAGS_INIT "${_vrost_libs}")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${_vrost_libs}")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "${_vrost_libs}")

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
# xwin carries the release CRT only.
set(CMAKE_TRY_COMPILE_CONFIGURATION Release)
set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)
