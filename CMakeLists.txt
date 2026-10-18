cmake_minimum_required(VERSION 3.24)
project(VROSTBridge VERSION 1.0.0.13 LANGUAGES C CXX)
set(VROSTBRIDGE_VERSION "1.0.0.13")
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
include(CTest)
if(WIN32)
  # 1.0.0.7: Windows builds (the native Windows client and the contract tests): Unicode APIs, a lean
  # <windows.h> without min/max macros, UTF-8 sources and the static C runtime. With clang-cl the
  # source and build folders are mapped out of __FILE__ (no build-machine paths in the programs).
  add_compile_definitions(UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _CRT_SECURE_NO_WARNINGS _WIN32_WINNT=0x0A00)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
  if(MSVC)
    add_compile_options(/utf-8)
  endif()
  if(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
    add_compile_options("/clang:-ffile-prefix-map=${CMAKE_CURRENT_SOURCE_DIR}/=" "/clang:-ffile-prefix-map=${CMAKE_CURRENT_BINARY_DIR}/=")
  endif()
  # Version information and the manifest (UTF-8 code page, Windows 10/11) of each Windows program.
  function(vrost_windows_resources target description)
    string(REPLACE "." "," VROST_RC_VERSION_COMMAS "${VROSTBRIDGE_VERSION}")
    set(VROST_RC_NAME "${target}")
    set(VROST_RC_DESCRIPTION "${description}")
    set(VROST_RC_MANIFEST "${CMAKE_CURRENT_SOURCE_DIR}/client/platform/windows/vrostbridge.manifest")
    configure_file(client/platform/windows/version.rc.in "${CMAKE_CURRENT_BINARY_DIR}/${target}.rc" @ONLY)
    target_sources(${target} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/${target}.rc")
    if(MSVC)
      target_link_options(${target} PRIVATE /MANIFEST:NO) # the resource above is the manifest
    endif()
  endfunction()
endif()
add_executable(core_tests tests/core_tests.cpp)
target_include_directories(core_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME core_contracts COMMAND core_tests)

# Interface raster (RC4.2): TrueType text (stb_truetype), shapes and the in-headset
# status card. Also built into libvrosttext.so for the Game Mode screen (ctypes).
add_library(vrost_ui STATIC client/ui/raster.cpp client/ui/status_card.cpp)
target_include_directories(vrost_ui PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(vrost_ui PROPERTIES POSITION_INDEPENDENT_CODE ON CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
if(NOT MSVC)
  target_compile_options(vrost_ui PRIVATE -Wall -Wextra -Wno-missing-field-initializers)
endif()
add_executable(status_card_tests tests/status_card_tests.cpp)
target_link_libraries(status_card_tests PRIVATE vrost_ui)
add_test(NAME status_card_contracts COMMAND status_card_tests "${CMAKE_CURRENT_SOURCE_DIR}/launcher/fonts"
  "${CMAKE_CURRENT_SOURCE_DIR}/client/assets/hud/logo-192.rgba" "${CMAKE_CURRENT_BINARY_DIR}")
add_executable(reaim_tests tests/reaim_tests.cpp)
target_include_directories(reaim_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME reaim_contracts COMMAND reaim_tests)
add_executable(headset_edid_tests tests/headset_edid_tests.cpp)
target_include_directories(headset_edid_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME headset_edid_contracts COMMAND headset_edid_tests)
add_executable(shown_size_tests tests/shown_size_tests.cpp)
target_include_directories(shown_size_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME shown_size_contracts COMMAND shown_size_tests)
# 1.0.0.11: the pixels the fused pass computes, from the lenses' visible area.
add_executable(visible_area_tests tests/visible_area_tests.cpp)
target_include_directories(visible_area_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME visible_area_contracts COMMAND visible_area_tests)
add_executable(fovea_view_tests tests/fovea_view_tests.cpp)
target_include_directories(fovea_view_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/client/transport)
add_test(NAME fovea_view_contracts COMMAND fovea_view_tests)
find_package(Threads REQUIRED)
add_executable(tracking_schedule_tests tests/tracking_schedule_tests.cpp)
target_include_directories(tracking_schedule_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(tracking_schedule_tests PRIVATE Threads::Threads)
add_test(NAME tracking_schedule_contracts COMMAND tracking_schedule_tests)
# 1.0.0.6: the controller prediction check.
add_executable(prediction_check_tests tests/prediction_check_tests.cpp)
target_include_directories(prediction_check_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME prediction_check_contracts COMMAND prediction_check_tests)

option(VROST_BUILD_LINUX_CLIENT "Build native Linux streaming client" ON)
if(UNIX AND NOT APPLE AND VROST_BUILD_LINUX_CLIENT)
  find_package(Threads REQUIRED)
  find_package(Vulkan REQUIRED)
  find_package(PkgConfig REQUIRED)
  pkg_check_modules(LEASE REQUIRED IMPORTED_TARGET wayland-server libdrm)
  add_executable(VROSTBridgeLease client/platform/linux/lease.cpp client/platform/linux/lease_protocol/drm-lease-v1-protocol.c)
  target_include_directories(VROSTBridgeLease PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
  target_link_libraries(VROSTBridgeLease PRIVATE PkgConfig::LEASE)
  set_target_properties(VROSTBridgeLease PROPERTIES INSTALL_RPATH "$ORIGIN/lib")
  install(TARGETS VROSTBridgeLease RUNTIME DESTINATION .)
  if(BUILD_TESTING)
    pkg_check_modules(WAYLAND_CLIENT REQUIRED IMPORTED_TARGET wayland-client)
    add_executable(lease_protocol_tests tests/lease_protocol_tests.cpp client/platform/linux/lease_protocol/drm-lease-v1-protocol.c)
    target_include_directories(lease_protocol_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_link_libraries(lease_protocol_tests PRIVATE PkgConfig::LEASE PkgConfig::WAYLAND_CLIENT Threads::Threads)
    add_test(NAME lease_protocol COMMAND lease_protocol_tests)
  endif()
  pkg_check_modules(AV REQUIRED IMPORTED_TARGET libavcodec libavutil libswscale)
  set(VROST_TRANSPORT_LIBRARY "${CMAKE_CURRENT_SOURCE_DIR}/client/transport/target/release/libvrostbridge_transport.so" CACHE FILEPATH "Built ALVR bridge library")
  if(NOT EXISTS "${VROST_TRANSPORT_LIBRARY}")
    message(FATAL_ERROR "Build client/transport with Cargo first, or set VROST_BUILD_LINUX_CLIENT=OFF for core tests")
  endif()
  # Rust cdylibs do not supply a SONAME. Link by library name so the installed
  # executable never records a build-machine path in its DT_NEEDED entries.
  add_library(VROSTBridgeTransport SHARED IMPORTED)
  set_target_properties(VROSTBridgeTransport PROPERTIES
    IMPORTED_LOCATION "${VROST_TRANSPORT_LIBRARY}" IMPORTED_NO_SONAME TRUE)
  add_executable(VROSTBridgeClient client/platform/linux/main.cpp client/platform/linux/vulkan.cpp client/platform/linux/decoder.cpp client/device/openvr_device.cpp)
  target_include_directories(VROSTBridgeClient PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} third_party/openvr)
  target_compile_options(VROSTBridgeClient PRIVATE -Wall -Wextra -Wno-missing-field-initializers)
  target_link_libraries(VROSTBridgeClient PRIVATE vrost_ui Vulkan::Vulkan PkgConfig::AV Threads::Threads ${CMAKE_DL_LIBS} VROSTBridgeTransport)
  add_library(vrosttext SHARED client/ui/vrosttext.cpp)
  target_link_libraries(vrosttext PRIVATE vrost_ui)
  target_compile_options(vrosttext PRIVATE -Wall -Wextra -Wno-missing-field-initializers -fvisibility=hidden)
  set_target_properties(vrosttext PROPERTIES CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
  install(TARGETS vrosttext LIBRARY DESTINATION .)
  # 1.0.0.2: the Vulkan layer VROSTBridge's calibration launcher (launcher/baballonia.py) enables for Baballonia's
  # calibration when it starts as a SteamVR overlay, so SteamVR finds the extension commands it calls on that
  # program's device. It needs only libc; the Vulkan loader loads it into that one program from vulkan/.
  add_library(VkLayer_vrostbridge_openvr MODULE client/platform/linux/openvr_texture_layer.c)
  target_include_directories(VkLayer_vrostbridge_openvr PRIVATE ${Vulkan_INCLUDE_DIRS})
  target_compile_options(VkLayer_vrostbridge_openvr PRIVATE -Wall -Wextra -Wno-missing-field-initializers)
  target_link_libraries(VkLayer_vrostbridge_openvr PRIVATE Threads::Threads)
  set_target_properties(VkLayer_vrostbridge_openvr PROPERTIES C_VISIBILITY_PRESET hidden
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/vulkan")
  configure_file(client/platform/linux/VkLayer_vrostbridge_openvr.json
    "${CMAKE_CURRENT_BINARY_DIR}/vulkan/VkLayer_vrostbridge_openvr.json" COPYONLY)
  install(TARGETS VkLayer_vrostbridge_openvr LIBRARY DESTINATION vulkan)
  install(FILES client/platform/linux/VkLayer_vrostbridge_openvr.json DESTINATION vulkan)
  if(BUILD_TESTING)
    # Loaded as the launcher loads it, on any Vulkan device (lavapipe in CI). Skips (77) without one.
    add_executable(vulkan_layer_tests tests/vulkan_layer_tests.cpp)
    target_link_libraries(vulkan_layer_tests PRIVATE Vulkan::Vulkan)
    add_dependencies(vulkan_layer_tests VkLayer_vrostbridge_openvr)
    add_test(NAME openvr_texture_layer COMMAND vulkan_layer_tests)
    set_tests_properties(openvr_texture_layer PROPERTIES SKIP_RETURN_CODE 77 ENVIRONMENT
      "VK_ADD_LAYER_PATH=${CMAKE_CURRENT_BINARY_DIR}/vulkan;VK_INSTANCE_LAYERS=VK_LAYER_VROSTBRIDGE_openvr_textures")
  endif()
  set_target_properties(VROSTBridgeClient PROPERTIES BUILD_RPATH "$ORIGIN" INSTALL_RPATH "$ORIGIN;$ORIGIN/lib")
  find_program(GLSLANG glslangValidator REQUIRED)
  add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/convert.spv" COMMAND ${GLSLANG} -V "${CMAKE_CURRENT_SOURCE_DIR}/client/platform/linux/convert.comp" -o "${CMAKE_CURRENT_BINARY_DIR}/convert.spv" DEPENDS client/platform/linux/convert.comp VERBATIM)
  add_custom_target(conversion_shader ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/convert.spv")
  add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/reaim.spv" COMMAND ${GLSLANG} -V "${CMAKE_CURRENT_SOURCE_DIR}/client/platform/linux/reaim.comp" -o "${CMAKE_CURRENT_BINARY_DIR}/reaim.spv" DEPENDS client/platform/linux/reaim.comp VERBATIM)
  add_custom_target(reaim_shader ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/reaim.spv")
  # RC4.3: conversion, foveation inverse, re-aim and sharpening in one pass per displayed frame (1.0.0.11: one
  # shader for every GPU, its constants in a uniform buffer).
  add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/fused.spv" COMMAND ${GLSLANG} -V "${CMAKE_CURRENT_SOURCE_DIR}/client/platform/linux/fused.comp" -o "${CMAKE_CURRENT_BINARY_DIR}/fused.spv" DEPENDS client/platform/linux/fused.comp VERBATIM)
  add_custom_target(fused_shader ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/fused.spv")
  if(BUILD_TESTING)
    # 1.0.0.7: the Windows client's frame upload (NV12, P010, yuv420p, yuv420p10le through a staging
    # buffer into the plane images) and the fused pass reading it, against a CPU model. Skips (77).
    add_executable(planar_upload_tests tests/planar_upload_tests.cpp)
    target_include_directories(planar_upload_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_link_libraries(planar_upload_tests PRIVATE Vulkan::Vulkan)
    add_dependencies(planar_upload_tests fused_shader conversion_shader)
    add_test(NAME planar_upload_contracts COMMAND planar_upload_tests "${CMAKE_CURRENT_BINARY_DIR}/fused.spv"
      "${CMAKE_CURRENT_BINARY_DIR}/convert.spv")
    set_tests_properties(planar_upload_contracts PROPERTIES SKIP_RETURN_CODE 77)
    # 1.0.0.7: CPU decoding's frames (as the Windows client decodes) laid out for that upload.
    add_executable(software_decode_tests tests/software_decode_tests.cpp)
    target_include_directories(software_decode_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_link_libraries(software_decode_tests PRIVATE PkgConfig::AV Vulkan::Vulkan)
    add_test(NAME software_decode_contracts COMMAND software_decode_tests
      "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/software_decode_8bit.h264"
      "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/software_decode_8bit_full_range.h264"
      "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/software_decode_10bit.h264")
    # Runs the real conversion shader on any Vulkan device (lavapipe in CI): colour,
    # 10-bit, chroma siting, dithering and the foveated-encoding inverse. Skips (77)
    # when no Vulkan device exists.
    add_executable(convert_shader_tests tests/convert_shader_tests.cpp)
    target_include_directories(convert_shader_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_link_libraries(convert_shader_tests PRIVATE Vulkan::Vulkan)
    add_dependencies(convert_shader_tests conversion_shader)
    add_test(NAME conversion_shader_contracts COMMAND convert_shader_tests "${CMAKE_CURRENT_BINARY_DIR}/convert.spv")
    set_tests_properties(conversion_shader_contracts PROPERTIES SKIP_RETURN_CODE 77)
    # The frame re-aim shader against the CPU model in core/reaim.hpp (RC4.2).
    add_executable(reaim_shader_tests tests/reaim_shader_tests.cpp)
    target_include_directories(reaim_shader_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_link_libraries(reaim_shader_tests PRIVATE Vulkan::Vulkan)
    add_dependencies(reaim_shader_tests reaim_shader)
    add_test(NAME reaim_shader_contracts COMMAND reaim_shader_tests "${CMAKE_CURRENT_BINARY_DIR}/reaim.spv")
    set_tests_properties(reaim_shader_contracts PROPERTIES SKIP_RETURN_CODE 77)
    # The fused frame pass against its CPU model and the RC4.2 two passes (RC4.3).
    add_executable(fused_shader_tests tests/fused_shader_tests.cpp)
    target_include_directories(fused_shader_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_link_libraries(fused_shader_tests PRIVATE Vulkan::Vulkan)
    add_dependencies(fused_shader_tests fused_shader)
    add_test(NAME fused_shader_contracts COMMAND fused_shader_tests "${CMAKE_CURRENT_BINARY_DIR}/fused.spv")
    set_tests_properties(fused_shader_contracts PROPERTIES SKIP_RETURN_CODE 77)
    # 1.0.0.11: the client's own frame path (VulkanVideo: the frame pass's buffers, the lens area, the clears, the
    # shown size, GPU time stamps) with SteamVR stood in for (tests/openvr_stand_in.hpp). Skips (77) without Vulkan.
    add_executable(video_pass_tests tests/video_pass_tests.cpp client/platform/linux/vulkan.cpp)
    target_include_directories(video_pass_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} third_party/openvr)
    target_compile_options(video_pass_tests PRIVATE -Wall -Wextra -Wno-missing-field-initializers)
    target_link_libraries(video_pass_tests PRIVATE Vulkan::Vulkan PkgConfig::AV Threads::Threads)
    add_dependencies(video_pass_tests fused_shader reaim_shader conversion_shader)
    add_test(NAME video_pass_contracts COMMAND video_pass_tests "${CMAKE_CURRENT_BINARY_DIR}")
    set_tests_properties(video_pass_contracts PROPERTIES SKIP_RETURN_CODE 77)
  endif()
  install(TARGETS VROSTBridgeClient RUNTIME DESTINATION .)
  install(FILES "${VROST_TRANSPORT_LIBRARY}" third_party/openvr/libopenvr_api.so DESTINATION .)
  install(DIRECTORY client/assets DESTINATION .)
  install(FILES "${CMAKE_CURRENT_BINARY_DIR}/convert.spv" "${CMAKE_CURRENT_BINARY_DIR}/reaim.spv"
          "${CMAKE_CURRENT_BINARY_DIR}/fused.spv" DESTINATION assets)
  install(DIRECTORY launcher/fonts/ DESTINATION assets/fonts FILES_MATCHING PATTERN "*.ttf" PATTERN "*-OFL.txt")
  install(DIRECTORY launcher DESTINATION . PATTERN "__pycache__" EXCLUDE)
  install(DIRECTORY dist/tools/ DESTINATION tools OPTIONAL USE_SOURCE_PERMISSIONS)
  install(DIRECTORY host/ DESTINATION host
    FILES_MATCHING PATTERN "*.py" PATTERN "*.json" PATTERN "VENDORED.md"
    PATTERN "__pycache__" EXCLUDE)
  install(FILES licenses/websockets-BSD.txt DESTINATION licenses)
  install(FILES scripts/Start-Client.sh DESTINATION . PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE GROUP_READ GROUP_EXECUTE WORLD_READ WORLD_EXECUTE)
endif()
# 1.0.0.7: the native Windows streaming client, the same program as the Linux one (its sources under
# client/platform/linux with Windows sections, and client/platform/windows): SteamVR as a Vulkan scene
# app on this PC, D3D11VA (or CPU) decoding uploaded into the fused frame pass. Cross-built on Linux
# with scripts/cross/windows-clang-cl.cmake by scripts/Build-Client-Windows.py, which prepares these inputs.
option(VROST_BUILD_WINDOWS_CLIENT "Build native Windows streaming client" ON)
if(WIN32 AND VROST_BUILD_WINDOWS_CLIENT)
  set(VROST_FFMPEG_DIR "" CACHE PATH "FFmpeg 6.1 for Windows: include/ and bin/ (avcodec, avutil and swscale DLLs)")
  set(VROST_IMPORT_LIBRARY_DIR "" CACHE PATH "avcodec.lib, avutil.lib, swscale.lib and vulkan-1.lib (scripts/Build-Client-Windows.py)")
  set(VROST_TRANSPORT_LIBRARY "" CACHE FILEPATH "vrostbridge_transport.dll (client/transport for x86_64-pc-windows-msvc)")
  set(VROST_TRANSPORT_IMPORT_LIBRARY "${VROST_TRANSPORT_LIBRARY}.lib" CACHE FILEPATH "Its import library")
  set(VROST_VULKAN_HEADERS "" CACHE PATH "Vulkan 1.3 headers: the folder that holds vulkan/ and vk_video/")
  file(GLOB VROST_FFMPEG_DLLS "${VROST_FFMPEG_DIR}/bin/avcodec-*.dll" "${VROST_FFMPEG_DIR}/bin/avutil-*.dll"
       "${VROST_FFMPEG_DIR}/bin/swscale-*.dll")
  list(LENGTH VROST_FFMPEG_DLLS VROST_FFMPEG_DLL_COUNT)
  foreach(required "${VROST_FFMPEG_DIR}/include/libavcodec/avcodec.h" "${VROST_IMPORT_LIBRARY_DIR}/avcodec.lib"
          "${VROST_IMPORT_LIBRARY_DIR}/avutil.lib" "${VROST_IMPORT_LIBRARY_DIR}/swscale.lib"
          "${VROST_IMPORT_LIBRARY_DIR}/vulkan-1.lib" "${VROST_TRANSPORT_LIBRARY}" "${VROST_TRANSPORT_IMPORT_LIBRARY}"
          "${VROST_VULKAN_HEADERS}/vulkan/vulkan.h")
    if(NOT EXISTS "${required}")
      message(FATAL_ERROR "Windows client input missing: ${required}. Build with scripts/Build-Client-Windows.py, "
                          "or set VROST_BUILD_WINDOWS_CLIENT=OFF for the contract tests")
    endif()
  endforeach()
  if(NOT VROST_FFMPEG_DLL_COUNT EQUAL 3)
    message(FATAL_ERROR "Expected the avcodec, avutil and swscale DLLs in ${VROST_FFMPEG_DIR}/bin")
  endif()
  # Only the Vulkan headers themselves reach the include path, never a whole system include folder.
  file(COPY "${VROST_VULKAN_HEADERS}/vulkan" "${VROST_VULKAN_HEADERS}/vk_video"
       DESTINATION "${CMAKE_CURRENT_BINARY_DIR}/vulkan-headers")
  add_library(vrost_vulkan INTERFACE)
  target_include_directories(vrost_vulkan INTERFACE "${CMAKE_CURRENT_BINARY_DIR}/vulkan-headers")
  target_link_libraries(vrost_vulkan INTERFACE "${VROST_IMPORT_LIBRARY_DIR}/vulkan-1.lib")
  add_library(vrost_ffmpeg INTERFACE)
  target_include_directories(vrost_ffmpeg INTERFACE "${VROST_FFMPEG_DIR}/include")
  target_link_libraries(vrost_ffmpeg INTERFACE "${VROST_IMPORT_LIBRARY_DIR}/avcodec.lib"
                        "${VROST_IMPORT_LIBRARY_DIR}/avutil.lib" "${VROST_IMPORT_LIBRARY_DIR}/swscale.lib")
  add_library(VROSTBridgeTransport SHARED IMPORTED)
  set_target_properties(VROSTBridgeTransport PROPERTIES
    IMPORTED_LOCATION "${VROST_TRANSPORT_LIBRARY}" IMPORTED_IMPLIB "${VROST_TRANSPORT_IMPORT_LIBRARY}")
  add_executable(VROSTBridgeClient client/platform/linux/main.cpp client/platform/linux/vulkan.cpp
    client/platform/linux/decoder.cpp client/device/openvr_device.cpp client/platform/windows/d3d11_video.cpp
    client/platform/windows/vulkan_upload.cpp)
  target_include_directories(VROSTBridgeClient PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} third_party/openvr)
  target_link_libraries(VROSTBridgeClient PRIVATE vrost_ui vrost_vulkan vrost_ffmpeg VROSTBridgeTransport
    d3d11 dxgi dxguid winmm avrt ws2_32 delayimp)
  # vulkan-1.dll comes with the GPU driver: loaded when first used, so a PC without it gets the client's
  # own error message instead of Windows refusing to start the program.
  target_link_options(VROSTBridgeClient PRIVATE /DELAYLOAD:vulkan-1.dll)
  vrost_windows_resources(VROSTBridgeClient "VROSTBridge native Windows client")
  if(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
    target_compile_options(VROSTBridgeClient PRIVATE /W4 -Wno-missing-field-initializers -Wno-unused-parameter)
  endif()
  find_program(GLSLANG glslangValidator REQUIRED)
  foreach(shader convert reaim fused)
    add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/${shader}.spv"
      COMMAND ${GLSLANG} -V "${CMAKE_CURRENT_SOURCE_DIR}/client/platform/linux/${shader}.comp" -o "${CMAKE_CURRENT_BINARY_DIR}/${shader}.spv"
      DEPENDS client/platform/linux/${shader}.comp VERBATIM)
  endforeach()
  add_custom_target(windows_shaders ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/convert.spv"
    "${CMAKE_CURRENT_BINARY_DIR}/reaim.spv" "${CMAKE_CURRENT_BINARY_DIR}/fused.spv")
  if(BUILD_TESTING)
    # The shader contracts and the frame upload on any Vulkan device (under Wine: winevulkan with
    # lavapipe). Skip (77) without one.
    foreach(shader_test convert_shader_tests reaim_shader_tests fused_shader_tests planar_upload_tests)
      add_executable(${shader_test} tests/${shader_test}.cpp)
      target_include_directories(${shader_test} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
      target_link_libraries(${shader_test} PRIVATE vrost_vulkan)
      add_dependencies(${shader_test} windows_shaders)
    endforeach()
    add_test(NAME conversion_shader_contracts COMMAND convert_shader_tests "${CMAKE_CURRENT_BINARY_DIR}/convert.spv")
    add_test(NAME reaim_shader_contracts COMMAND reaim_shader_tests "${CMAKE_CURRENT_BINARY_DIR}/reaim.spv")
    add_test(NAME fused_shader_contracts COMMAND fused_shader_tests "${CMAKE_CURRENT_BINARY_DIR}/fused.spv")
    add_test(NAME planar_upload_contracts COMMAND planar_upload_tests "${CMAKE_CURRENT_BINARY_DIR}/fused.spv"
      "${CMAKE_CURRENT_BINARY_DIR}/convert.spv")
    set_tests_properties(conversion_shader_contracts reaim_shader_contracts fused_shader_contracts
      planar_upload_contracts PROPERTIES SKIP_RETURN_CODE 77)
    # CPU decoding with the FFmpeg DLLs the client ships (copied beside the test).
    add_executable(software_decode_tests tests/software_decode_tests.cpp)
    target_include_directories(software_decode_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_link_libraries(software_decode_tests PRIVATE vrost_ffmpeg vrost_vulkan)
    add_custom_command(TARGET software_decode_tests POST_BUILD
      COMMAND ${CMAKE_COMMAND} -E copy_if_different ${VROST_FFMPEG_DLLS} $<TARGET_FILE_DIR:software_decode_tests>)
    add_test(NAME software_decode_contracts COMMAND software_decode_tests
      "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/software_decode_8bit.h264"
      "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/software_decode_8bit_full_range.h264"
      "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/software_decode_10bit.h264")
    # 1.0.0.11: the client's own frame path with SteamVR stood in for, CPU-decoded frames taken by its upload
    # (tests/video_pass_tests.cpp). Skips (77) without a Vulkan device.
    add_executable(video_pass_tests tests/video_pass_tests.cpp client/platform/linux/vulkan.cpp
      client/platform/windows/vulkan_upload.cpp client/platform/windows/d3d11_video.cpp)
    target_include_directories(video_pass_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} third_party/openvr)
    target_link_libraries(video_pass_tests PRIVATE vrost_vulkan vrost_ffmpeg d3d11 dxgi dxguid)
    add_dependencies(video_pass_tests windows_shaders)
    add_custom_command(TARGET video_pass_tests POST_BUILD
      COMMAND ${CMAKE_COMMAND} -E copy_if_different ${VROST_FFMPEG_DLLS} $<TARGET_FILE_DIR:video_pass_tests>)
    add_test(NAME video_pass_contracts COMMAND video_pass_tests "${CMAKE_CURRENT_BINARY_DIR}")
    set_tests_properties(video_pass_contracts PROPERTIES SKIP_RETURN_CODE 77)
  endif()
  install(TARGETS VROSTBridgeClient RUNTIME DESTINATION .)
  install(FILES "${VROST_TRANSPORT_LIBRARY}" ${VROST_FFMPEG_DLLS} third_party/openvr/openvr_api.dll DESTINATION .)
  install(DIRECTORY client/assets DESTINATION .)
  install(FILES "${CMAKE_CURRENT_BINARY_DIR}/convert.spv" "${CMAKE_CURRENT_BINARY_DIR}/reaim.spv"
          "${CMAKE_CURRENT_BINARY_DIR}/fused.spv" DESTINATION assets)
  install(DIRECTORY launcher/fonts/ DESTINATION assets/fonts FILES_MATCHING PATTERN "*.ttf" PATTERN "*-OFL.txt")
endif()
add_executable(stream_tests tests/stream_tests.cpp)
target_include_directories(stream_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME stream_contracts COMMAND stream_tests)
add_executable(video_handshake_tests tests/video_handshake_tests.cpp)
target_include_directories(video_handshake_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME video_handshake_contracts COMMAND video_handshake_tests)
find_package(Threads REQUIRED)
add_executable(presentation_tests tests/presentation_tests.cpp)
target_include_directories(presentation_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(presentation_tests PRIVATE Threads::Threads)
add_test(NAME presentation_contracts COMMAND presentation_tests)
add_executable(geometry_tests tests/geometry_tests.cpp)
target_include_directories(geometry_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} third_party/openvr)
target_link_libraries(geometry_tests PRIVATE Threads::Threads)
add_test(NAME live_geometry_contracts COMMAND geometry_tests)

add_executable(input_tests tests/input_tests.cpp)
target_include_directories(input_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME input_contracts COMMAND input_tests)

add_executable(skeletal_tests tests/skeletal_tests.cpp)
target_include_directories(skeletal_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME skeletal_contracts COMMAND skeletal_tests)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux" OR WIN32)
  add_executable(VROSTBridgeRuntimeProbe client/platform/linux/runtime_probe.cpp)
  target_include_directories(VROSTBridgeRuntimeProbe PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} third_party/openvr)
  target_link_libraries(VROSTBridgeRuntimeProbe PRIVATE ${CMAKE_DL_LIBS} Threads::Threads)
  if(WIN32)
    vrost_windows_resources(VROSTBridgeRuntimeProbe "VROSTBridge runtime probe")
  endif()
  install(TARGETS VROSTBridgeRuntimeProbe RUNTIME DESTINATION .)
  add_executable(runtime_settings_tests tests/runtime_settings_tests.cpp)
  target_include_directories(runtime_settings_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} third_party/openvr)
  target_link_libraries(runtime_settings_tests PRIVATE ${CMAKE_DL_LIBS})
  add_test(NAME runtime_settings_contracts COMMAND runtime_settings_tests)
endif()

if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/upstream/ALVR/alvr/server_openvr/cpp/alvr_server/VROSTBridgeHand.h")
  add_executable(native_hand_host_tests tests/native_hand_host_tests.cpp)
  target_include_directories(native_hand_host_tests PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/upstream/ALVR/alvr/server_openvr/cpp/alvr_server")
  add_test(NAME native_host_hand_contracts COMMAND native_hand_host_tests)
endif()

# 1.0.0.7: the companion's gaze samples on loopback UDP (both clients) and the Linux AF_UNIX socket.
add_executable(gaze_receiver_tests tests/gaze_receiver_tests.cpp)
target_include_directories(gaze_receiver_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME gaze_input_contracts COMMAND gaze_receiver_tests)
if(WIN32)
  # 1.0.0.7: the Windows client's command pipe, stdout lines, console events and scheduling.
  add_executable(windows_process_tests tests/windows_process_tests.cpp)
  target_include_directories(windows_process_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
  add_test(NAME windows_process_contracts COMMAND windows_process_tests)
endif()

add_executable(chaperone_geometry_tests tests/chaperone_geometry_tests.cpp)
target_include_directories(chaperone_geometry_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME chaperone_geometry_contracts COMMAND chaperone_geometry_tests)

add_executable(status_text_tests tests/status_text_tests.cpp)
target_include_directories(status_text_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME status_text_contracts COMMAND status_text_tests "${CMAKE_CURRENT_BINARY_DIR}/status_text_sample.ppm")

# Contract checks must execute in release builds too. Some test fixtures also
# perform setup in assert(), so NDEBUG would remove both checks and setup.
foreach(contract_target core_tests lease_protocol_tests stream_tests video_handshake_tests
    presentation_tests geometry_tests input_tests skeletal_tests runtime_settings_tests
    native_hand_host_tests chaperone_geometry_tests status_text_tests convert_shader_tests
    status_card_tests reaim_tests reaim_shader_tests tracking_schedule_tests planar_upload_tests
    software_decode_tests gaze_receiver_tests windows_process_tests)
  if(TARGET ${contract_target})
    if(MSVC)
      target_compile_options(${contract_target} PRIVATE /UNDEBUG)
    else()
      target_compile_options(${contract_target} PRIVATE -UNDEBUG)
    endif()
  endif()
endforeach()
