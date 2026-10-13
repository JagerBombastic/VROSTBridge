#pragma once
// Windows: loading a DLL from the client's own folder by its full path (OpenVR's openvr_api.dll), the
// counterpart of dlopen/dlsym/dlclose on Linux. Only the named file is loaded: never a copy found on
// the DLL search path.
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <filesystem>
#include <string>

namespace vrost {

inline std::string windowsErrorText(DWORD code) {
  wchar_t *text = nullptr;
  const DWORD length = FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
                                          FORMAT_MESSAGE_IGNORE_INSERTS,
                                      nullptr, code, 0, reinterpret_cast<wchar_t *>(&text), 0, nullptr);
  std::string result;
  if (length && text) {
    const int bytes = WideCharToMultiByte(CP_UTF8, 0, text, int(length), nullptr, 0, nullptr, nullptr);
    result.resize(size_t(bytes > 0 ? bytes : 0));
    if (bytes > 0) WideCharToMultiByte(CP_UTF8, 0, text, int(length), result.data(), bytes, nullptr, nullptr);
    while (!result.empty() && (result.back() == '\n' || result.back() == '\r' || result.back() == ' '))
      result.pop_back();
  }
  if (text) LocalFree(text);
  return result.empty() ? "Windows error " + std::to_string(code) : result + " (" + std::to_string(code) + ")";
}

// Null on failure; lastLibraryError() then says why.
inline void *loadLibrary(const std::filesystem::path &file) {
  // LOAD_WITH_ALTERED_SEARCH_PATH: the DLL's own dependencies resolve from its folder first.
  return reinterpret_cast<void *>(LoadLibraryExW(file.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH));
}
inline void *librarySymbol(void *library, const char *name) {
  return reinterpret_cast<void *>(GetProcAddress(static_cast<HMODULE>(library), name));
}
inline void closeLibrary(void *library) {
  if (library) FreeLibrary(static_cast<HMODULE>(library));
}
inline std::string lastLibraryError(const std::filesystem::path &file) {
  return "Cannot load " + file.filename().u8string() + ": " + windowsErrorText(GetLastError());
}

} // namespace vrost
