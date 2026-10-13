// The Windows client's process plumbing (client/platform/windows/process.hpp and core/stream.hpp,
// 1.0.0.7) as the companion uses it; runs on Windows and under Wine:
//  1. a child started with pipes on stdin and stdout (as subprocess.Popen(stdin=PIPE, stdout=PIPE)
//     starts the client) sees its stdin as the companion's pipe, reads nothing while the pipe is empty
//     (never waiting), reads the commands written (CRLF lines too), and sees the pipe closed when the
//     parent closes its end; its diagnostic lines reach the parent whole, ending in "\n" only, a line
//     longer than one 4096-byte write included;
//  2. NUL on stdin is not the companion's pipe;
//  3. the program folder, the console event names, the scheduling line and the high-resolution sleep.
#include "core/stream.hpp"
#include "client/platform/windows/process.hpp"
#include <cstdio>
#include <string>
#include <vector>

namespace {
int failures = 0;
#define CHECK(cond, ...)                                                                           \
  do {                                                                                             \
    if (!(cond)) {                                                                                 \
      ++failures;                                                                                  \
      std::printf("FAIL line %d: %s ", __LINE__, #cond);                                           \
      std::printf(__VA_ARGS__);                                                                    \
      std::printf("\n");                                                                           \
    }                                                                                              \
  } while (0)

const std::string longLine = "CHILD long=" + std::string(6000, 'x') + "end";

// The child: what the client does with its stdin and stdout.
int child(bool expectPipe) {
  vrost::windows::binaryStdio();
  vrost::windows::CommandPipe pipe;
  vrost::diagnosticLine(std::string("CHILD companion=") + (pipe.companion() ? "1" : "0"));
  std::string text;
  const auto first = pipe.read(text);
  vrost::diagnosticLine(std::string("CHILD first=") + (first == vrost::windows::CommandPipe::Read::Nothing ? "nothing" : "other"));
  vrost::diagnosticLine("CHILD waiting");
  vrost::diagnosticFlush();
  if (!expectPipe) return pipe.companion() ? 3 : 0;
  bool closed = false;
  const uint64_t deadline = vrost::monotonicNs() + 20000000000ULL;
  while (!closed && vrost::monotonicNs() < deadline) {
    switch (pipe.read(text)) {
    case vrost::windows::CommandPipe::Read::Closed: closed = true; break;
    case vrost::windows::CommandPipe::Read::Data: break;
    default: Sleep(1);
    }
  }
  size_t end;
  while ((end = text.find('\n')) != std::string::npos) {
    std::string line = text.substr(0, end);
    text.erase(0, end + 1);
    if (!line.empty() && line.back() == '\r') line.pop_back();
    vrost::diagnosticLine("CHILD line=" + line);
  }
  vrost::diagnosticLine(longLine);
  vrost::diagnosticLine(closed ? "CHILD closed" : "CHILD timed out");
  vrost::diagnosticFlush();
  return closed ? 0 : 4;
}

struct Child {
  PROCESS_INFORMATION process{};
  HANDLE input = nullptr, output = nullptr; // our ends
  std::string received;
  bool start(const std::wstring &program, const wchar_t *mode, bool nulInput) {
    SECURITY_ATTRIBUTES inherit{sizeof(inherit), nullptr, TRUE};
    HANDLE childInput = nullptr, childOutput = nullptr;
    if (nulInput) {
      childInput = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &inherit, OPEN_EXISTING, 0, nullptr);
    } else if (!CreatePipe(&childInput, &input, &inherit, 0) || !SetHandleInformation(input, HANDLE_FLAG_INHERIT, 0))
      return false;
    if (!CreatePipe(&output, &childOutput, &inherit, 0) || !SetHandleInformation(output, HANDLE_FLAG_INHERIT, 0))
      return false;
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = childInput;
    startup.hStdOutput = childOutput;
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    std::wstring command = L"\"" + program + L"\" " + mode;
    const BOOL started = CreateProcessW(program.c_str(), command.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr,
                                        nullptr, &startup, &process);
    CloseHandle(childInput);
    CloseHandle(childOutput);
    return started != 0;
  }
  // Reads until `marker` has arrived (or the child's output ends).
  bool readUntil(const std::string &marker) {
    char buffer[4096];
    while (received.find(marker) == std::string::npos) {
      DWORD got = 0;
      if (!ReadFile(output, buffer, sizeof(buffer), &got, nullptr) || !got) return false;
      received.append(buffer, got);
    }
    return true;
  }
  void readAll() {
    char buffer[4096];
    DWORD got = 0;
    while (ReadFile(output, buffer, sizeof(buffer), &got, nullptr) && got) received.append(buffer, got);
  }
  DWORD finish() {
    if (input) CloseHandle(input);
    input = nullptr;
    readAll();
    WaitForSingleObject(process.hProcess, 30000);
    DWORD code = 99;
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hProcess);
    CloseHandle(process.hThread);
    CloseHandle(output);
    return code;
  }
};
} // namespace

int main(int argc, char **argv) {
  if (argc == 2 && std::string(argv[1]) == "--child") return child(true);
  if (argc == 2 && std::string(argv[1]) == "--child-nul") return child(false);
  vrost::windows::binaryStdio();
  std::wstring program(32768, L'\0');
  program.resize(GetModuleFileNameW(nullptr, program.data(), DWORD(program.size())));
  CHECK(vrost::windows::executableDirectory() == std::filesystem::path(program).parent_path(), "program folder");

  // 1. The companion's pipes.
  {
    Child c;
    CHECK(c.start(program, L"--child", false), "child with pipes");
    CHECK(c.readUntil("CHILD waiting\n"), "the child's first lines: %s", c.received.c_str());
    const char commands[] = "menu\r\nstatus Hello from the companion\nquit\n";
    DWORD written = 0;
    CHECK(WriteFile(c.input, commands, DWORD(sizeof(commands) - 1), &written, nullptr) && written == sizeof(commands) - 1,
          "commands written");
    Sleep(50);
    const DWORD code = c.finish(); // closes our end of its stdin
    const std::string expected = "CHILD companion=1\nCHILD first=nothing\nCHILD waiting\nCHILD line=menu\n"
                                 "CHILD line=status Hello from the companion\nCHILD line=quit\n" + longLine + "\nCHILD closed\n";
    CHECK(code == 0, "child exit %lu", static_cast<unsigned long>(code));
    CHECK(c.received == expected, "child output (%zu bytes): %.200s", c.received.size(), c.received.c_str());
    CHECK(c.received.find('\r') == std::string::npos, "no CR in the diagnostic lines");
  }
  // 2. NUL is not the companion's pipe.
  {
    Child c;
    CHECK(c.start(program, L"--child-nul", true), "child with NUL on stdin");
    const DWORD code = c.finish();
    CHECK(code == 0 && c.received.rfind("CHILD companion=0\nCHILD first=nothing\nCHILD waiting\n", 0) == 0,
          "exit %lu, output %s", static_cast<unsigned long>(code), c.received.c_str());
  }
  // 3. Names, scheduling and sleep.
  CHECK(std::string(vrost::windows::ConsoleStop::name(CTRL_C_EVENT + 1)) == "ctrl-c", "ctrl-c");
  CHECK(std::string(vrost::windows::ConsoleStop::name(CTRL_BREAK_EVENT + 1)) == "ctrl-break", "ctrl-break");
  CHECK(std::string(vrost::windows::ConsoleStop::name(CTRL_CLOSE_EVENT + 1)) == "close", "close");
  CHECK(std::string(vrost::windows::ConsoleStop::name(CTRL_SHUTDOWN_EVENT + 1)) == "shutdown", "shutdown");
  CHECK(vrost::windows::ConsoleStop::install(), "console handler");
  {
    vrost::windows::SchedulingBasics scheduling;
    const std::string line = scheduling.apply();
    std::printf("%s\n", line.c_str());
    CHECK(line.rfind("STATUS Windows scheduling: timer resolution ", 0) == 0, "%s", line.c_str());
  }
  double worstLate = 0;
  for (int i = 0; i < 20; ++i) {
    const uint64_t target = vrost::monotonicNs() + 2500000;
    vrost::windows::sleepUntil(target);
    const uint64_t woke = vrost::monotonicNs();
    CHECK(woke >= target, "woke %lld ns early", static_cast<long long>(target - woke));
    worstLate = std::max(worstLate, double(woke - target) / 1e6);
  }
  std::printf("High-resolution sleep: at most %.3f ms late\n", worstLate);
  CHECK(worstLate < 50, "sleep overshoot %.3f ms", worstLate);
  if (failures) {
    std::printf("%d Windows process check(s) failed\n", failures);
    return 1;
  }
  std::printf("Windows process contracts passed\n");
  return 0;
}
