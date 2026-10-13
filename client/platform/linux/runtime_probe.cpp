// Separate background-only readiness probe. No video/transport or Scene app.
// This lets startup diagnostics evolve without rebuilding the tested streamer.
// 1.0.0.7: also built for Windows (VROSTBridgeRuntimeProbe.exe), with the same options and output.
#include "client/platform/linux/runtime_settings.hpp"
#include <csignal>
#include "core/version.hpp"
#ifdef _WIN32
#include "client/platform/windows/process.hpp"
#endif
int main(int argc,char** argv) {
#ifdef _WIN32
  vrost::windows::binaryStdio(); // lines end in "\n" as on Linux; Windows has no SIGPIPE
#else
  std::signal(SIGPIPE, SIG_IGN); // RC4.4: report a closed SteamVR pipe instead of dying silently
#endif
  if(argc==2 && std::string(argv[1])=="--help") {
    std::cout<<"VROSTBridge " VROSTBRIDGE_CLIENT_VERSION " runtime probe\n--verify-runtime  Verify stream-only preferences and measure headset display progress\n";
    return 0;
  }
  if(argc!=2 || std::string(argv[1])!="--verify-runtime") return 2;
  try {
#ifdef _WIN32
    return vrost::runtimeSettings(vrost::windows::executableDirectory(),{},false,true,true);
#else
    return vrost::runtimeSettings(std::filesystem::canonical("/proc/self/exe").parent_path(),{},false,true,true);
#endif
  } catch(const std::exception& error) {
    std::cerr<<"ERROR Runtime probe: "<<error.what()<<std::endl;
    return 1;
  }
}
