#pragma once
// Windows process plumbing for the native client and its runtime probe (1.0.0.7). The Linux client
// does the same with /proc/self/exe, signals, poll() on its stdin pipe and the kernel's scheduler:
//  - the program's folder (its DLLs, assets and openvr_api.dll live beside it);
//  - stdout and stderr in binary mode: lines end in "\n" exactly as on Linux;
//  - console events (Ctrl+C, Ctrl+Break, closing the console, logoff, shutdown) stop the frame loop
//    as SIGINT/SIGTERM do on Linux. The companion cannot send a signal (Popen.terminate() is
//    TerminateProcess), so it sends "quit" on the command pipe instead (client/platform/linux/main.cpp);
//  - the companion's command pipe on stdin: read without blocking; a broken pipe or end of file
//    means the companion is gone;
//  - scheduling basics: 1 ms timer resolution, above-normal priority, the MMCSS "Games" task on the
//    frame loop's thread, no power throttling (EcoQoS) of a process without a visible window, and
//    a high-resolution timer for the tracking loop's sleeps.
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <avrt.h>
#include <timeapi.h>
#include <fcntl.h>
#include <io.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <thread>
#pragma comment(lib, "winmm.lib")
#pragma comment(lib, "avrt.lib")

namespace vrost::windows {

inline std::filesystem::path executableDirectory() {
  std::wstring buffer(512, L'\0');
  for (;;) {
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), DWORD(buffer.size()));
    if (length == 0) throw std::runtime_error("Cannot find the program's folder");
    if (length < buffer.size()) {
      buffer.resize(length);
      break;
    }
    if (buffer.size() >= 32768) throw std::runtime_error("The program's folder path is too long");
    buffer.resize(buffer.size() * 2);
  }
  return std::filesystem::path(buffer).parent_path();
}

inline void binaryStdio() {
  std::fflush(stdout);
  std::fflush(stderr);
  // A program started without standard handles has no descriptor (-2): _setmode would end it.
  for (FILE *stream : {stdout, stderr}) {
    const int descriptor = _fileno(stream);
    if (descriptor >= 0) _setmode(descriptor, _O_BINARY);
  }
}

// Console events stop the frame loop; event() is 0 or the CTRL_*_EVENT value plus one.
class ConsoleStop {
public:
  static std::atomic<int> &event() {
    static std::atomic<int> value{0};
    return value;
  }
  static bool install() {
    finished();
    return SetConsoleCtrlHandler(handler, TRUE) != 0;
  }
  // main has finished its cleanup: a close, logoff or shutdown handler may now let Windows end the process.
  static void done() { SetEvent(finished()); }
  static const char *name(int value) {
    switch (value - 1) {
    case CTRL_C_EVENT: return "ctrl-c";
    case CTRL_BREAK_EVENT: return "ctrl-break";
    case CTRL_CLOSE_EVENT: return "close";
    case CTRL_LOGOFF_EVENT: return "logoff";
    case CTRL_SHUTDOWN_EVENT: return "shutdown";
    default: return "unknown";
    }
  }

private:
  static HANDLE finished() {
    static const HANDLE value = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    return value;
  }
  static BOOL WINAPI handler(DWORD type) {
    switch (type) {
    case CTRL_C_EVENT:
    case CTRL_BREAK_EVENT:
      event() = int(type) + 1;
      return TRUE;
    case CTRL_CLOSE_EVENT:
    case CTRL_LOGOFF_EVENT:
    case CTRL_SHUTDOWN_EVENT:
      // Windows ends the process when this handler returns (after 5 s for a closed console): give
      // the frame loop the time to leave SteamVR and the host connection cleanly.
      event() = int(type) + 1;
      if (finished()) WaitForSingleObject(finished(), 4500);
      return TRUE;
    default:
      return FALSE;
    }
  }
};

// Sets ConsoleStop::done() when main returns (declare it first in main).
struct ConsoleStopScope {
  ~ConsoleStopScope() { ConsoleStop::done(); }
};

// The companion's command pipe (stdin of a program it starts with subprocess.Popen(stdin=PIPE)).
class CommandPipe {
  HANDLE input_ = GetStdHandle(STD_INPUT_HANDLE);
  bool pipe_ = input_ != nullptr && input_ != INVALID_HANDLE_VALUE && GetFileType(input_) == FILE_TYPE_PIPE;

public:
  enum class Read { Nothing, Data, Closed };
  // A pipe on stdin is the companion's; a console, a file or NUL is not, and is not read.
  bool companion() const { return pipe_; }
  // The line the client logs when the pipe closes (the Linux client's words).
  static const char *closedLine() { return "STATUS The VROSTBridge companion closed its command pipe; stopping"; }
  // Appends what has arrived (up to 128 bytes, as a read() on Linux) to `text`, never waiting.
  Read read(std::string &text) {
    if (!pipe_) return Read::Nothing;
    DWORD available = 0;
    // Fails once the companion's end is closed (ERROR_BROKEN_PIPE): the companion is gone, as a
    // hang-up or error on the Linux pipe.
    if (!PeekNamedPipe(input_, nullptr, 0, nullptr, &available, nullptr)) return Read::Closed;
    if (!available) return Read::Nothing;
    char buffer[128];
    DWORD got = 0;
    if (!ReadFile(input_, buffer, std::min<DWORD>(available, DWORD(sizeof(buffer))), &got, nullptr) || !got)
      return Read::Closed;
    text.append(buffer, got);
    return Read::Data;
  }
};

// vulkan-1.dll (the Vulkan loader, installed with the GPU driver) is delay-loaded by the client: check
// for it before the first Vulkan call, so a PC without it gets this message instead of a crash.
inline void requireVulkanLoader() {
  if (!LoadLibraryW(L"vulkan-1.dll"))
    throw std::runtime_error("Vulkan is not available on this PC (vulkan-1.dll was not found): install or update "
                             "the graphics driver");
}

// Sleeps until a monotonicNs() time with a high-resolution waitable timer (Windows 10 1803 and later;
// about 0.5 ms late at most instead of Sleep's whole milliseconds), falling back to Sleep.
inline void sleepUntil(uint64_t wakeNs) {
  struct Timer {
    HANDLE handle = nullptr;
    Timer() {
      handle = CreateWaitableTimerExW(nullptr, nullptr, 0x00000002 /* CREATE_WAITABLE_TIMER_HIGH_RESOLUTION */,
                                      TIMER_ALL_ACCESS);
      if (!handle) handle = CreateWaitableTimerExW(nullptr, nullptr, 0, TIMER_ALL_ACCESS);
    }
    ~Timer() {
      if (handle) CloseHandle(handle);
    }
  };
  thread_local Timer waiter;
  const HANDLE timer = waiter.handle;
  const uint64_t now = uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                    std::chrono::steady_clock::now().time_since_epoch())
                                    .count());
  if (wakeNs <= now) return;
  const uint64_t waitNs = std::min<uint64_t>(wakeNs - now, 1000000000ULL);
  LARGE_INTEGER due{};
  due.QuadPart = -LONGLONG((waitNs + 99) / 100); // relative, in 100 ns units
  if (timer && SetWaitableTimerEx(timer, &due, 0, nullptr, nullptr, nullptr, 0)) {
    WaitForSingleObject(timer, INFINITE);
    return;
  }
  std::this_thread::sleep_for(std::chrono::nanoseconds(waitNs));
}

// The scheduling basics for the process and the calling (frame loop) thread, undone on destruction.
class SchedulingBasics {
  bool timer_ = false;
  HANDLE task_ = nullptr;

public:
  // Returns the STATUS line saying what was applied.
  std::string apply() {
    timer_ = timeBeginPeriod(1) == TIMERR_NOERROR;
    const bool priority = SetPriorityClass(GetCurrentProcess(), ABOVE_NORMAL_PRIORITY_CLASS) != 0;
    DWORD taskIndex = 0;
    task_ = AvSetMmThreadCharacteristicsW(L"Games", &taskIndex);
    // A program started without a visible window may be throttled to efficiency (EcoQoS) and have
    // its timer requests ignored (Windows 11): opt out of both, or (Windows 10) of the first.
    PROCESS_POWER_THROTTLING_STATE throttling{};
    throttling.Version = PROCESS_POWER_THROTTLING_CURRENT_VERSION;
    throttling.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED | PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION;
    throttling.StateMask = 0;
    bool unthrottled = SetProcessInformation(GetCurrentProcess(), ProcessPowerThrottling, &throttling,
                                             sizeof(throttling)) != 0;
    if (!unthrottled) {
      throttling.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED;
      unthrottled = SetProcessInformation(GetCurrentProcess(), ProcessPowerThrottling, &throttling,
                                          sizeof(throttling)) != 0;
    }
    return std::string("STATUS Windows scheduling: timer resolution ") + (timer_ ? "1 ms" : "unchanged") +
           ", process priority " + (priority ? "above normal" : "unchanged") + ", frame loop MMCSS task " +
           (task_ ? "Games" : "unavailable") + ", power throttling " + (unthrottled ? "off" : "unchanged");
  }
  ~SchedulingBasics() {
    if (task_) AvRevertMmThreadCharacteristics(task_);
    if (timer_) timeEndPeriod(1);
  }
};

} // namespace vrost::windows
