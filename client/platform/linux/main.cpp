#include "client/platform/linux/video.hpp"
#include <csignal>
#ifdef _WIN32
#include "client/platform/windows/process.hpp"
#else
#include <unistd.h>
#include <poll.h>
#include <sys/stat.h>
#endif
#include <iomanip>
#include <sstream>
#include "client/platform/linux/runtime_settings.hpp"
#include "client/platform/linux/status_overlay.hpp"
#include "client/video_handshake.hpp"
#include "client/presentation_trace.hpp"
#include "core/version.hpp"
#include "core/reaim.hpp"
#include "client/device/fovea_view.hpp"
namespace {
#ifdef _WIN32
// 1.0.0.7: console events (Ctrl+C, Ctrl+Break, closing, logoff, shutdown) stop the loop as SIGINT and
// SIGTERM do on Linux (client/platform/windows/process.hpp).
std::atomic<int> &interrupted = vrost::windows::ConsoleStop::event();
#else
volatile std::sig_atomic_t interrupted = 0;
void signalHandler(int signal) { interrupted = signal; }
#endif
void timingFields(std::ostream &out, const char *name, const vrost::TimingSummary &s) {
  out << ' ' << name << "_samples=" << s.samples
      << ' ' << name << "_mean_ms=" << s.meanMs
      << ' ' << name << "_p95_ms=" << s.p95Ms
      << ' ' << name << "_max_ms=" << s.maxMs;
}
} // namespace
int main(int argc, char **argv) {
#ifndef _WIN32
  // RC4.4: a closed pipe or socket (SteamVR's compositor IPC after a compositor crash, the host's
  // control connection, the companion's pipe) must be an error return, never a silent kill by
  // SIGPIPE: RC4.3 died that way 9 s after SteamVR's compositor on the client crashed.
  std::signal(SIGPIPE, SIG_IGN);
  // The companion starts this client with its commands on a pipe it holds open for the session:
  // that pipe closing means the companion is gone (RC4.3's client then died of SIGPIPE on its next
  // log line; now it stops). A terminal or /dev/null on stdin is not a companion and is ignored.
  const bool companionPipe = [] { struct stat st{}; return ::fstat(STDIN_FILENO, &st) == 0 && S_ISFIFO(st.st_mode); }();
  if(argc==2 && std::string(argv[1])=="--verify-runtime")
    return vrost::runtimeSettings(std::filesystem::canonical("/proc/self/exe").parent_path(), {}, false, true, true);
  std::string gazeSocket;
#else
  // 1.0.0.7: the native Windows client. Windows has no SIGPIPE: a closed pipe is a write error there.
  vrost::windows::ConsoleStopScope consoleStopScope; // lets a console close wait for the cleanup below
  vrost::windows::binaryStdio();
  // The companion's command pipe (stdin): its closing means the companion is gone, as on Linux.
  vrost::windows::CommandPipe commandPipe;
  if(argc==2 && std::string(argv[1])=="--verify-runtime")
    return vrost::runtimeSettings(vrost::windows::executableDirectory(), {}, false, true, true);
#endif
  uint16_t gazeUdp = 0; // 1.0.0.7: gaze samples on a loopback UDP port (Windows; also Linux)
  bool compatibility = false;
#ifdef _WIN32
  // 1.0.0.7: Windows decodes with D3D11VA or on the CPU; both upload the decoded planes to the GPU, so
  // CPU decoding there keeps foveation, the fused pass and re-aim (it is not Linux's compatibility mode).
  bool cpuDecode = false;
#endif
  bool lateLatch = true;
  bool prediction = true;
  bool acceleration = true;  // 1.0.0.14: the acceleration folded into the poses the host extrapolates
  bool allowFoveation = true, allowTenBit = true, allowAv1 = true, gradualRecovery = false;
  bool reaim = true;
  int sharpening = 0; // RC4.3: percent of the unsharp amount on luma in the fused pass
  bool twoPass = false; // RC4.3: keep RC4.2's two passes (the setting One-pass frame path off)
  bool fullFrame = false; // 1.0.0.11: compute every pixel, also those the lenses never show
  uint32_t shownScale = 100; // 1.0.0.11: each eye shown at this percent of its size (the companion's 'scale N')
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
#ifdef _WIN32
    if (arg == "--gaze-socket") {
      std::cerr << "--gaze-socket is not available on Windows; use --gaze-udp PORT\n";
      return 2;
    } else if (arg == "--software-decode")
      cpuDecode = true;
    else if (arg == "--decoder" && i + 1 < argc) {
      const std::string decoder = argv[++i];
      if (decoder == "auto" || decoder == "d3d11va")
        cpuDecode = false;
      else if (decoder == "cpu")
        cpuDecode = true;
      else {
        std::cerr << "--decoder takes auto, d3d11va or cpu\n";
        return 2;
      }
    }
#else
    if (arg == "--gaze-socket" && i + 1 < argc) {
      gazeSocket = argv[++i];
    } else if (arg == "--software-decode")
      compatibility = true;
#endif
    else if (arg == "--gaze-udp" && i + 1 < argc) {
      int port = 0;
      try {
        port = std::stoi(argv[++i]);
      } catch (const std::exception &) {
      }
      if (port < 1 || port > 65535) {
        std::cerr << "--gaze-udp takes a port from 1 to 65535\n";
        return 2;
      }
      gazeUdp = uint16_t(port);
    }
    else if (arg == "--no-late-frame-catchup")
      lateLatch = false;
    else if (arg == "--no-prediction")
      prediction = false;
    else if (arg == "--no-acceleration")
      acceleration = false;
    else if (arg == "--no-foveation")
      allowFoveation = false;
    else if (arg == "--no-10bit")
      allowTenBit = false;
    else if (arg == "--no-av1")
      allowAv1 = false;
    else if (arg == "--gradual-recovery")
      gradualRecovery = true;
    else if (arg == "--no-reaim")
      reaim = false;
    else if (arg == "--two-pass")
      twoPass = true;
    else if (arg == "--full-frame")
      fullFrame = true;
    else if (arg == "--scale" && i + 1 < argc) {
      int percent = 0;
      try {
        percent = std::stoi(argv[++i]);
      } catch (const std::exception &) {
      }
      if (percent < 50 || percent > 100) {
        std::cerr << "--scale takes a percentage from 50 to 100\n";
        return 2;
      }
      shownScale = uint32_t(percent);
    }
    else if (arg == "--sharpening" && i + 1 < argc) {
      try {
        sharpening = std::stoi(argv[++i]);
      } catch (const std::exception &) {
        std::cerr << "--sharpening takes a number from 0 to 200\n";
        return 2;
      }
    } else if (arg == "--help") {
#ifdef _WIN32
      std::cout << "VROSTBridge " VROSTBRIDGE_CLIENT_VERSION " native Windows client\n"
                   "--decoder auto|d3d11va|cpu  Decode with D3D11VA on SteamVR's GPU (auto) or on the CPU\n"
                   "--software-decode  Same as --decoder cpu\n"
                   "--gaze-udp PORT  Receive the companion's gaze samples on 127.0.0.1:PORT\n"
                   "--no-late-frame-catchup  Disable bounded late-frame selection for comparison\n"
                   "--no-prediction  Timestamp poses at sampling time; with an RC4.1 host, also turns host prediction off\n"
                   "--no-acceleration  Send measured velocities only: the host predicts hands at constant speed (1.0.0.14 folds in acceleration)\n"
                   "--no-foveation / --no-10bit / --no-av1  Do not offer these stream features to the host\n"
                   "--gradual-recovery  The host uses intra refresh: keep decoding after an isolated loss\n"
                   "--no-reaim  Submit frames as rendered instead of re-aiming them to the displayed head pose\n"
                   "--sharpening N  Sharpen the picture's detail when it is shown (0 off, 35 low, 70 medium, 120 high)\n"
                   "--two-pass  Convert frames when decoded and re-aim them when shown (RC4.2's path, no sharpening)\n"
                   "--full-frame  Compute every pixel, also those the headset's lenses never show\n"
                   "--scale N  Show each eye at N percent (50-100) of its size\n"
                   "Commands on stdin (one per line): menu, playspace, status TEXT, hud TEXT, reaim 0|1, sharpen N,\n"
                   "scale N, align-check, gradual 0|1, quit\n";
      return 0;
#endif
      std::cout << "VROSTBridge " VROSTBRIDGE_CLIENT_VERSION " native Linux client\n--software-decode  "
                   "Explicit CPU compatibility mode (not a performance path)\n"
                   "--no-late-frame-catchup  Disable bounded late-frame selection for comparison\n"
                   "--no-prediction  Timestamp poses at sampling time; with an RC4.1 host, also turns host prediction off\n"
                   "--no-acceleration  Send measured velocities only: the host predicts hands at constant speed (1.0.0.14 folds in acceleration)\n"
                   "--no-foveation / --no-10bit / --no-av1  Do not offer these stream features to the host\n"
                   "--gradual-recovery  The host uses intra refresh: keep decoding after an isolated loss\n"
                   "--no-reaim  Submit frames as rendered instead of re-aiming them to the displayed head pose\n"
                   "--sharpening N  Sharpen the picture's detail when it is shown (0 off, 35 low, 70 medium, 120 high)\n"
                   "--two-pass  Convert frames when decoded and re-aim them when shown (RC4.2's path, no sharpening)\n"
                   "--full-frame  Compute every pixel, also those the headset's lenses never show\n"
                   "--scale N  Show each eye at N percent (50-100) of its size\n"
                   "Commands on stdin (one per line): menu, playspace, status TEXT, hud TEXT, reaim 0|1, sharpen N,\n"
                   "scale N, align-check, gradual 0|1, quit\n";
      return 0;
    } else {
      std::cerr << "Unknown option: " << arg << '\n';
      return 2;
    }
  }
#ifdef _WIN32
  vrost::windows::ConsoleStop::install();
  vrost::diagnosticLine("STATUS VROSTBridge " VROSTBRIDGE_CLIENT_VERSION " native Windows client");
  // For the whole run: timer resolution, priority, and the MMCSS task of this thread (the frame loop's).
  vrost::windows::SchedulingBasics scheduling;
  vrost::diagnosticLine(scheduling.apply());
#else
  std::signal(SIGINT, signalHandler);
  std::signal(SIGTERM, signalHandler);
  vrost::diagnosticLine("STATUS VROSTBridge " VROSTBRIDGE_CLIENT_VERSION " native Linux client");
#endif
  vrost::diagnosticFlush(); // runtimeSettings below writes directly to stdout.
  void *transport = nullptr;
  try {
#ifdef _WIN32
    const auto dir = vrost::windows::executableDirectory();
#else
    auto dir = std::filesystem::canonical("/proc/self/exe").parent_path();
#endif
    vrost::VerifiedDisplayTiming displayTiming;
    if(vrost::runtimeSettings(dir,{},false,true,true,&displayTiming)!=0) throw std::runtime_error("Start the stream-only session from VROSTBridge.");
    vrost::OpenVRDevice runtime;
    runtime.open(dir,displayTiming);
#ifndef _WIN32
    runtime.gaze(gazeSocket);
#endif
    runtime.gazeUdp(gazeUdp);
#ifdef _WIN32
    vrost::windows::requireVulkanLoader();
#endif
    vrost::VulkanVideo graphics;
#ifdef _WIN32
    graphics.cpuDecode = cpuDecode;
    graphics.allowFused = !twoPass; // CPU-decoded frames are uploaded as planes too (planar_upload.hpp)
    graphics.open(runtime, dir / "assets" / "convert.spv");
#else
    graphics.allowFused = !twoPass && !compatibility;
    graphics.open(runtime, dir / "assets/convert.spv");
#endif
    graphics.sharpening = vrost::sharpeningAmount(sharpening);
    // 1.0.0.11: the part of each eye the lenses show; the fused pass skips the rest unless --full-frame.
    graphics.setVisibleArea(runtime.visibleArea(), !fullFrame);
    vrost::diagnosticLine(std::string("STATUS Frame pass: ") +
                       (compatibility ? "CPU conversion (compatibility decoding)"
                        : graphics.fusedAvailable() ? "fused (one pass per shown frame)"
                                                    : "two passes (conversion when decoded, re-aim when shown)") +
                       "; sharpening " + (graphics.fusedAvailable() && !compatibility
                                              ? std::to_string(std::clamp(sharpening, 0, 200)) + "%"
                                              : std::string("not applied (needs the fused pass)")));
    vrost::diagnosticLine("STATUS Local SteamVR ready: " + runtime.model + " at " +
                       std::to_string(runtime.refresh) + " Hz");
#ifdef _WIN32
    if (cpuDecode)
      vrost::diagnosticLine("STATUS Video decoding: CPU, uploaded to Vulkan");
    else if (graphics.adapter.found && graphics.adapter.video)
      vrost::diagnosticLine("STATUS Video decoding: D3D11VA on " + graphics.adapter.name + ", copied to Vulkan");
    else
      vrost::diagnosticLine("STATUS Video decoding: D3D11VA unavailable (" + graphics.adapter.problem +
                            "); the stream will stop with an error: choose CPU decoding");
#else
    vrost::diagnosticLine(std::string("STATUS ") + (compatibility ? "CPU compatibility decoding"
                                                              : "VA-API / Vulkan GPU decoding"));
#endif
    auto neutral = graphics.neutral();
    graphics.finishFrame();
    // Offer only what this client can show: foveation and AV1 need the GPU path, and AV1
    // also needs an FFmpeg build with its decoder. The host still decides what to use.
    uint32_t caps = 0;
    if (!compatibility && allowFoveation) caps |= VROST_CAP_FOVEATED;
#ifdef _WIN32
    // 1.0.0.7: what the D3D11 video decoder of SteamVR's GPU accepts (HEVC Main10; AV1 Main, also in
    // 10-bit when 10-bit is offered). FFmpeg's AV1 decoder needs the hardware, so CPU decoding has none.
    if (allowTenBit && (cpuDecode || graphics.adapter.hevcMain10)) caps |= VROST_CAP_10_BIT;
    if (!cpuDecode && allowAv1 && graphics.adapter.av1 && (!(caps & VROST_CAP_10_BIT) || graphics.adapter.av1TenBit) &&
        avcodec_find_decoder(AV_CODEC_ID_AV1))
      caps |= VROST_CAP_AV1;
#else
    if (allowTenBit) caps |= VROST_CAP_10_BIT;
    if (!compatibility && allowAv1 && avcodec_find_decoder(AV_CODEC_ID_AV1)) caps |= VROST_CAP_AV1;
#endif
    transport = vrost_create2(runtime.width, runtime.height, runtime.refresh, caps);
    if (!transport)
      throw std::runtime_error("Cannot initialize transport capabilities");
    if (gradualRecovery) {
      vrost_set_gradual_recovery(transport, 1);
      vrost::diagnosticLine("STATUS Gradual loss recovery: keyframes only after 3 consecutive lost frames");
    }
    vrost::diagnosticLine(std::string("STATUS Stream features offered: foveated=") + ((caps & VROST_CAP_FOVEATED) ? "1" : "0") +
                       " ten_bit=" + ((caps & VROST_CAP_10_BIT) ? "1" : "0") + " av1=" + ((caps & VROST_CAP_AV1) ? "1" : "0"));
    // Destroy context while callback targets still exist: ALVR owns callback
    // thread lifetime.
    std::unique_ptr<vrost::Decoder> decoder;
    vrost::DecoderRouter router;
    vrost::VideoHandshake videoHandshake(transport, &router, vrost::DecoderRouter::callback);
    struct Session {
      void *&p;
      vrost::OpenVRDevice &runtime;
      std::unique_ptr<vrost::Decoder> &decoder;
      vrost::DecoderRouter &router;
      ~Session() {
        runtime.stream(false);
        runtime.stopTracking();
        if (p) {
          router.target(nullptr);
          vrost_pause(p);
          if (decoder)
            decoder->stop();
          vrost_destroy(p);
          p = nullptr;
        }
        runtime.close();
      }
    } session{transport, runtime, decoder, router};
    // Declared after `session` so the overlay is destroyed while SteamVR is still open.
    vrost::StatusOverlay statusOverlay;
    statusOverlay.open(runtime.overlay, dir / "assets");
    runtime.setPrediction(prediction);
    runtime.acceleration(acceleration);
    runtime.startTracking(transport);
    vrost_resume(transport);
    uint32_t width = 0, height = 0;
    bool streaming = false, configured = false, quit = false, fatal=false;
    uint64_t fatalAt=0;
    std::shared_ptr<vrost::Surface> displayed;
    std::deque<std::pair<uint64_t, std::shared_ptr<vrost::Surface>>> retired;
    uint64_t tick = 0, lastReport = 0, lastNewTimestamp = 0;
    uint64_t repeated=0, submits=0, failed=0, fallback=0;
    uint64_t newFrames=0, windowNewFrames=0, windowSubmits=0;
    uint64_t lateCatches=0, lateTimeouts=0, windowReused=0;
    uint64_t previousNewSubmit=0, previousLoop=0, windowStart=vrost::monotonicNs();
    lastReport=windowStart;
    vrost::TimingWindow arrivalToSubmit, readyWaitObserved, readyToSubmit;
    vrost::TimingWindow newFrameInterval, compositorInterval;
    vrost::TimingWindow lateFrameWait;
    // RC4.2: frames re-aimed to the displayed head pose, the frame alignment check and
    // how evenly the poses behind consecutive new frames advance.
    vrost::TimingWindow poseStep;
    uint64_t previousPickSample = 0, reaimedFrames = 0, reaimFallbacks = 0, windowReaimed = 0;
    uint64_t windowFused = 0, fusedSkipped = 0, lastFovea = 0; // RC4.3: fused pass frames and skips; FOVEA lines
    uint64_t compositorFrameSeen = 0, compositorFrameAt = 0; bool compositorStalled = false; // RC4.4
    uint64_t alignmentCheckStart = 0;
    float reaimDegrees = 0, reaimDegreesMax = 0;
    const uint64_t refreshPeriodNs = uint64_t(1e9 / std::max(30.f, runtime.refresh));
    // Hardware-decoded frames re-aim in the fused pass (or the RC4.2 pass); CPU-decoded ones only in the latter.
    const bool reaimPossible = compatibility ? graphics.reaimPassAvailable() : graphics.reaimAvailable();
    vrost::diagnosticLine(std::string("STATUS Frame re-aim: ") + (reaim ? "on" : "off") +
                       (reaimPossible ? "" : " (unavailable on this PC)"));
    vrost::TimingWindow runtimeTick, submitCpu, handoffCpu;
    vrost::TimingWindow runtimeEvents, runtimeGeometry, runtimeWaitPoses, runtimeFocus, runtimeFrameQuery;
    uint64_t runtimeEventCount=0, runtimeEventBudgets=0, runtimeExitedProcesses=0, runtimeGeometryReads=0;
    vrost::PresentationTrace presentationTrace;
    vrost::CadenceTracker cadenceTracker;
    uint64_t windowPacketsStart=0;
    uint32_t windowThrottleMax=0, windowReprojected=0, windowFrameSamples=0;
    bool previousFocus=true;
    std::string command;
    bool stoppedByCompanion = false; // 1.0.0.7: the "quit" command
    std::string companionStatus, hudLine, lastError;
    bool reconnecting = false;
    VrostStreamCounters previousCounters{};
    VrostAudioStats previousAudio{};
    uint64_t previousTenBit = 0, previousFoveated = 0, previousGaze = 0, previousGazePredicted = 0;
    // Mid-session decoder changes and reconnect timing.
    std::vector<uint8_t> activeConfig;
    uint32_t activeCodec = UINT32_MAX;
    uint64_t decoderReconfigurations = 0;
    const uint64_t processStart = vrost::monotonicNs();
    uint64_t disconnectedAt = 0;
    bool firstFrameLogged = false;
    auto stop = [&] {
      videoHandshake.stop();
      runtime.stream(false);
      streaming = false;
      configured = false;
      displayed.reset();
      retired.clear();
      lastNewTimestamp=previousNewSubmit=previousLoop=0;
      router.target(nullptr);
      if (decoder) {
        decoder->stop();
        decoder.reset();
      }
      activeConfig.clear();
      activeCodec = UINT32_MAX;
    };
    // The companion's commands, one per line.
    auto handleCommands = [&] {
        size_t end;
        while((end=command.find('\n'))!=std::string::npos) {
          auto value=command.substr(0,end); command.erase(0,end+1);
#ifdef _WIN32
          if(!value.empty() && value.back()=='\r') value.pop_back(); // a text-mode pipe ends lines in CRLF
#endif
          if(value=="menu") runtime.requestMenu();
          else if(value=="playspace") runtime.requestPlayspace();
          else if(value.rfind("status ",0)==0 || value=="status") companionStatus=value.size()>7 ? value.substr(7,160) : "";
          else if(value.rfind("hud ",0)==0 || value=="hud") hudLine=value.size()>4 ? value.substr(4,200) : "";
          else if(value=="reaim 1" || value=="reaim 0") {
            reaim = value=="reaim 1";
            vrost::diagnosticLine(std::string("STATUS Frame re-aim: ") + (reaim ? "on" : "off"));
          }
          else if(value.rfind("sharpen ",0)==0 && value.size()<=12) {
            try {
              sharpening = std::clamp(std::stoi(value.substr(8)), 0, 200);
              graphics.sharpening = vrost::sharpeningAmount(sharpening);
              vrost::diagnosticLine("STATUS Sharpening: " + std::to_string(sharpening) + "%" +
                                 (graphics.fusedAvailable() && !compatibility ? "" : " (needs the fused frame pass)"));
            } catch (const std::exception &) {}
          }
          else if(value.rfind("scale ",0)==0 && value.size()<=10) {
            // 1.0.0.11: the companion shows the picture smaller while this PC's graphics fall behind.
            try {
              const int percent = std::stoi(value.substr(6));
              if (percent >= 50 && percent <= 100) {
                shownScale = uint32_t(percent);
                if (decoder) decoder->setShownScale(shownScale);
                const auto eye = decoder ? vrost::Decoder::scaledEye({decoder->shownWidth(), decoder->shownHeight()}, shownScale)
                                         : vrost::EyeSize{};
                vrost::diagnosticLine("STATUS Shown at " + std::to_string(shownScale) + "%" +
                                      (eye.width ? " (" + std::to_string(eye.width) + " x " + std::to_string(eye.height) +
                                                       " per eye)" : std::string()));
              }
            } catch (const std::exception &) {}
          }
          else if(value=="align-check") {
            alignmentCheckStart = vrost::monotonicNs();
            vrost::diagnosticLine("ALIGNMENT_CHECK started: frames alternate between their own pose and one turned 3 degrees left, once a second, for 10 s");
          }
          else if(value=="gradual 1" || value=="gradual 0") {
            vrost_set_gradual_recovery(transport, value=="gradual 1");
            vrost::diagnosticLine(std::string("STATUS Loss recovery: ")+(value=="gradual 1" ? "gradual (host intra refresh)" : "keyframe"));
          }
          else if(value=="quit") {
            // 1.0.0.7: the companion's stop request. It cannot signal a Windows client (Popen.terminate()
            // is TerminateProcess there): leave the loop and clean up as SIGTERM does.
            stoppedByCompanion = true;
            quit = true;
          }
        }
    };
    while (!quit && !interrupted) {
      try {
#ifdef _WIN32
      switch (commandPipe.read(command)) {
      case vrost::windows::CommandPipe::Read::Closed:
        vrost::diagnosticLine(vrost::windows::CommandPipe::closedLine());
        quit = true;
        break;
      case vrost::windows::CommandPipe::Read::Data:
        if(command.size()>1024) command.clear();
        handleCommands();
        break;
      default:
        break;
      }
#else
      pollfd inputPoll{STDIN_FILENO, POLLIN, 0};
      const bool inputEvent = ::poll(&inputPoll,1,0)>0;
      if (inputEvent && companionPipe && !(inputPoll.revents&POLLIN) &&
          (inputPoll.revents&(POLLHUP|POLLERR|POLLNVAL))) {
        vrost::diagnosticLine("STATUS The VROSTBridge companion closed its command pipe; stopping");
        quit = true;
      } else if (inputEvent && (inputPoll.revents&POLLIN)) {
        char buf[128]; auto n=::read(STDIN_FILENO,buf,sizeof(buf));
        if (n==0 && companionPipe) {
          vrost::diagnosticLine("STATUS The VROSTBridge companion closed its command pipe; stopping");
          quit = true;
        }
        if(n>0) command.append(buf,size_t(n));
        if(command.size()>1024) command.clear();
        handleCommands();
      }
#endif
      VrostEvent event{};
      while (!fatal && vrost_poll(transport, &event)) {
        switch (event.kind) {
        case 1: {
          char message[4096]{};
          vrost_message(transport, message, sizeof(message));
          vrost::diagnosticLine(std::string("STATUS ") + message);
          break;
        }
        case 2:
          stop();
          width = event.width;
          height = event.height;
          streaming = true;
          reconnecting = false;
          runtime.stream(true);
          vrost::diagnosticLine("STATUS Stream negotiated " + std::to_string(width) + " x " +
                             std::to_string(height) + " per eye");
          videoHandshake.start(vrost::monotonicNs());
          vrost::diagnosticLine("STATUS Requesting host codec configuration and first keyframe");
          break;
        case 3:
          stop();
          disconnectedAt = vrost::monotonicNs();
          reconnecting = true;

          vrost::diagnosticLine("STATUS Disconnected; waiting for host");
          break;
        case 4:
          runtime.haptic(event);
          break;
        case 5: {
          if (!streaming)
            break;
          uint64_t size = vrost_config(transport, nullptr, 0);
          if (size > 1024 * 1024)
            throw std::runtime_error("Oversized codec configuration");
          std::vector<uint8_t> config(size);
          vrost_config(transport, config.data(), config.size());
          if (configured && event.codec == activeCodec && config == activeConfig)
            break; // A repeated configuration needs no decoder change.
          if (configured) {
            // The host encoder restarted with new parameters during the session.
            // Replace only the decoder; keep the session, tracking and the last
            // displayed frame (held/reprojected) until the new keyframe arrives.
            router.target(nullptr);
            if (decoder) {
              decoder->stop();
              decoder.reset();
            }
            ++decoderReconfigurations;
            vrost::diagnosticLine("STATUS Host video configuration changed; restarting the decoder in-session (count=" +
                               std::to_string(decoderReconfigurations) + ")");
            videoHandshake.start(vrost::monotonicNs());
          }
          vrost::FoveationParams foveation;
          VrostFoveation negotiated{};
          if (vrost_foveation(transport, &negotiated)) {
            if (compatibility)
              throw std::runtime_error("CPU decoding cannot undo foveated encoding; turn foveated encoding off");
            foveation.enabled = true;
            for (int axis = 0; axis < 2; ++axis) {
              foveation.encoded[axis] = negotiated.encoded[axis];
              foveation.viewRatio[axis] = negotiated.view_ratio[axis];
              foveation.centerSize[axis] = negotiated.center_size[axis];
              foveation.edgeRatio[axis] = negotiated.edge_ratio[axis];
              for (int eye = 0; eye < 2; ++eye) foveation.shifts[eye][axis] = negotiated.center_shifts[eye][axis];
            }
          }
          decoder = std::make_unique<vrost::Decoder>(
              transport, graphics, runtime.geometry, compatibility);
          // 1.0.0.5: a stream larger than SteamVR's recommended size is shown at that size.
          const vrost::EyeSize shown = vrost::shownEyeSize(width, height, runtime.width, runtime.height);
          decoder->setShownScale(shownScale);
          decoder->start(event.codec, width, height, config, foveation, shown);
          if (shown.width != width || shown.height != height)
            vrost::diagnosticLine("STATUS Stream " + std::to_string(width) + " x " + std::to_string(height) +
                                  " per eye is shown at SteamVR's recommended " + std::to_string(shown.width) +
                                  " x " + std::to_string(shown.height));
          router.target(decoder.get());
          videoHandshake.configured(vrost::monotonicNs());
          configured = true;
          activeConfig = std::move(config);
          activeCodec = event.codec;
          {
            std::ostringstream video;
            video<<"VIDEO {\"width\":"<<width<<",\"height\":"<<height
                 <<",\"stereo_width\":"<<width*2<<",\"codec\":\""
                 <<(event.codec==0?"H264":event.codec==1?"Hevc":"AV1")<<"\",\"refresh_hz\":"<<runtime.refresh
                 <<",\"motion_prediction\":"<<(runtime.prediction()?"true":"false")
                 <<",\"acceleration\":"<<(runtime.prediction()&&runtime.acceleration()?"true":"false")
                 <<",\"foveated\":"<<(foveation.enabled?"true":"false")
                 <<",\"encoded_width\":"<<decoder->decodedWidth()/2<<",\"encoded_height\":"<<decoder->decodedHeight()
                 <<",\"shown_width\":"<<decoder->shownWidth()<<",\"shown_height\":"<<decoder->shownHeight()
                 <<",\"shown_scale\":"<<shownScale
#ifdef _WIN32
                 <<",\"decoder\":\""<<(cpuDecode ? "cpu" : "d3d11va")<<"\"";
#else
                 <<",\"decoder\":\""<<(compatibility ? "cpu" : "vaapi")<<"\"";
#endif
            if (foveation.enabled)
              video<<",\"foveation\":{\"center_size\":["<<foveation.centerSize[0]<<","<<foveation.centerSize[1]
                   <<"],\"edge_ratio\":["<<foveation.edgeRatio[0]<<","<<foveation.edgeRatio[1]<<"]}";
            video<<"}";
            vrost::diagnosticLine(video.str());
          }
          vrost::diagnosticLine("STATUS Decoder configured; requesting a fresh keyframe (codec=" +
                             std::to_string(event.codec) + ", config_bytes=" + std::to_string(size) + ")");
          break;
        }
        case 6: {
          char message[4096]{};
          vrost_message(transport, message, sizeof(message));
          throw std::runtime_error(message);
        }
        default:
          break;
        }
      }
      if (decoder) {
        auto error = decoder->error();
        if (!error.empty())
          throw std::runtime_error(error);
      }
      vrost::Pose head;
      bool geometry = false;
      const auto tickStart=vrost::monotonicNs();
      vrost::PresentationFrame frameTrace;
      bool tracked = runtime.tick(head, geometry, quit,frameTrace.runtime);
      {
        // RC4.4: SteamVR's compositor frame counter stops when the compositor does (a crash, or
        // standby), and its frame timing may stop being available at all; one line each way lets
        // the companion tell the user what happened.
        const auto observed=vrost::monotonicNs();
        if (frameTrace.runtime.frameTimingValid && frameTrace.runtime.frameIndex!=compositorFrameSeen) {
          if (compositorStalled) vrost::diagnosticLine("STATUS SteamVR compositor frames are advancing again");
          compositorFrameSeen=frameTrace.runtime.frameIndex;compositorFrameAt=observed;compositorStalled=false;
        } else if (!compositorStalled && compositorFrameAt && observed-compositorFrameAt>2000000000ULL) {
          compositorStalled=true;
          vrost::diagnosticLine("STATUS SteamVR compositor frames stopped advancing (2 s): its compositor closed or the headset is in standby");
        }
      }
      if (frameTrace.runtime.frameTimingValid) {
        const uint32_t flags=frameTrace.runtime.reprojectionFlags;
        windowThrottleMax=std::max(windowThrottleMax,(flags&vr::VRCompositor_ThrottleMask)>>8);
        if (flags&(vr::VRCompositor_ReprojectionReason_Cpu|vr::VRCompositor_ReprojectionReason_Gpu)) ++windowReprojected;
        ++windowFrameSamples;
      }
      frameTrace.tickNs=vrost::monotonicNs()-tickStart;
      runtimeTick.add(frameTrace.tickNs);
      runtimeEvents.add(frameTrace.runtime.eventPollNs);
      runtimeGeometry.add(frameTrace.runtime.geometryNs);
      runtimeWaitPoses.add(frameTrace.runtime.waitPosesNs);
      runtimeFocus.add(frameTrace.runtime.inputFocusNs);
      runtimeFrameQuery.add(frameTrace.runtime.frameQueryNs);
      runtimeEventCount+=frameTrace.runtime.events;
      runtimeEventBudgets+=frameTrace.runtime.eventBudgetExhausted;
      runtimeExitedProcesses+=frameTrace.runtime.exitedProcesses;
      runtimeGeometryReads+=frameTrace.runtime.geometryAttempted;
      ++tick;
      if(runtime.inputFocus()!=previousFocus) {
        previousFocus=runtime.inputFocus();
        vrost::diagnosticLine("DEBUG input_focus=" + std::to_string(previousFocus) +
                           " tracked=" + std::to_string(tracked));
      }
      if (quit) break; // A real runtime quit is logged by the device event handler.
      graphics.beginFrame(); // Explicit timing keeps WaitGetPoses off the queue.
      auto now = vrost::monotonicNs();
      if (!fatal && videoHandshake.update(now, decoder ? decoder->packets.load() : 0)) {
        std::ostringstream recovery;
        recovery << "DEBUG Video recovery: phase=" << videoHandshake.phase()
                 << " keyframe_requests=" << videoHandshake.requests()
                 << " video_silence_ms=" << videoHandshake.silenceMs(now);
        vrost::diagnosticLine(recovery.str());
      }
      if(fatal && now-fatalAt>30000000000ULL) quit=true;
      if (previousLoop) {
        frameTrace.intervalNs=now-previousLoop;
        compositorInterval.add(frameTrace.intervalNs);
      }
      previousLoop=now;
      auto candidate = decoder ? decoder->latestReady(displayed ? displayed->timestamp : 0) : nullptr;
      if (!candidate && decoder && displayed && streaming && tracked && lateLatch && !fatal) {
        auto budget=vrost::lateFrameBudget(runtime.compositor->GetFrameTimeRemaining(),runtime.refresh);
        if (budget) {
          const auto start=vrost::monotonicNs();
          candidate=decoder->latestReady(displayed->timestamp,budget);
          lateFrameWait.add(vrost::monotonicNs()-start);
          if (candidate) ++lateCatches; else ++lateTimeouts;
        }
      }
      if (candidate) {
        if (displayed)
          retired.emplace_back(tick, displayed);
        // Frame timestamps are prediction targets; record when the pose behind
        // this frame was actually sampled for hold windows and age metrics.
        if (!candidate->sampledAt)
          candidate->sampledAt = runtime.sampleTime(candidate->timestamp, vrost::monotonicNs());
        displayed = candidate;
      }
      while (!retired.empty() && tick - retired.front().first > 4)
        retired.pop_front();
      now=vrost::monotonicNs(); // Selection may have waited; do not use stale time.
      auto continuity=vrost::remoteFrameState(tracked,streaming,bool(displayed),now,
                                           displayed ? displayed->sampledAt : 0);
      bool fresh=continuity.fresh;
      // Continue reprojection during a brief network hiccup. Never replace a
      // good remote frame solely because the local dashboard has input focus.
      bool hold=continuity.hold;
      // Frame alignment check: for 10 s, every other second, frames go to SteamVR
      // labelled with a pose turned 3 degrees left (and are not re-aimed here). If
      // SteamVR re-aims frames itself, the picture jumps sideways once a second.
      bool checking=false, turned=false;
      if (alignmentCheckStart) {
        const uint64_t elapsed=now-alignmentCheckStart;
        if (elapsed>=10000000000ULL) {
          alignmentCheckStart=0;
          vrost::diagnosticLine("ALIGNMENT_CHECK finished");
        } else {
          checking=true;
          turned=(elapsed/1000000000ULL)%2==1;
        }
      }
      if (hold) {
        const auto submitStart=vrost::monotonicNs();
        bool submitted=false, reaimed=false;
        // RC4.3: a planar frame (fused path) is always shown through its one pass; without
        // re-aim it keeps the rendered pose (the alignment check labels it turned). An RGBA
        // frame (CPU decoding, or the two-pass fallback) needs the separate re-aim pass.
        const bool aim = reaim && !checking && tracked &&
                         (displayed->planar ? graphics.fusedAvailable() : graphics.reaimPassAvailable());
        const vrost::Pose label = turned ? vrost::yawed(displayed->source, 3.f) : displayed->source;
        if (aim || displayed->planar) {
          // -1: the pass could not run and nothing was submitted, so an RGBA frame goes out
          // unchanged below (a planar one waits for the next refresh). 0: submitted but not
          // accepted (no focus); never submit twice.
          const int result = aim ? graphics.submitReaimed(runtime, displayed, displayed->source, head, runtime.views())
                                 : graphics.submitReaimed(runtime, displayed, label, label, runtime.views());
          reaimed=result>=0;
          submitted=result>0;
          if (aim && submitted) {
            ++reaimedFrames; ++windowReaimed;
            reaimDegrees=vrost::rotationDegrees(displayed->source, head);
            reaimDegreesMax=std::max(reaimDegreesMax, reaimDegrees);
          } else if (aim && !reaimed && !displayed->planar) ++reaimFallbacks;
          if (displayed->planar) {
            if (reaimed) ++windowFused;
            else ++fusedSkipped; // no free output image this refresh: SteamVR reprojects the last one
          }
        }
        if (!reaimed)
          submitted=graphics.submit(runtime, displayed, label);
        frameTrace.submitNs=vrost::monotonicNs()-submitStart;
        submitCpu.add(frameTrace.submitNs);
        if(submitted) { ++submits; ++windowSubmits; } else ++failed;
        if (submitted && displayed->timestamp != lastNewTimestamp) {
          const auto submittedAt=vrost::monotonicNs();
          ++newFrames; ++windowNewFrames;
          frameTrace.freshSubmission=true;
          if (previousNewSubmit) {
            frameTrace.freshGapNs=submittedAt-previousNewSubmit;
            newFrameInterval.add(frameTrace.freshGapNs);
          }
          previousNewSubmit=submittedAt;
          if (displayed->sampledAt) {
            runtime.observePick(displayed->sampledAt, submittedAt);
            if (previousPickSample && displayed->sampledAt > previousPickSample) {
              const uint64_t step=displayed->sampledAt-previousPickSample;
              // Deviation from one refresh, for consecutive frames only.
              if (step < 2*refreshPeriodNs)
                poseStep.add(step>refreshPeriodNs ? step-refreshPeriodNs : refreshPeriodNs-step);
            }
            previousPickSample=displayed->sampledAt;
          }
          if (displayed->receivedAt) arrivalToSubmit.add(submittedAt-displayed->receivedAt);
          if (!firstFrameLogged) {
            firstFrameLogged = true;
            vrost::diagnosticLine("STATUS First remote frame shown " + std::to_string((submittedAt-processStart)/1000000) +
                               " ms after the native client started");
          }
          if (disconnectedAt) {
            vrost::diagnosticLine("STATUS Stream resumed: first new frame " + std::to_string((submittedAt-disconnectedAt)/1000000) +
                               " ms after the disconnect");
            disconnectedAt = 0;
          }
          if (displayed->firstReadyObservedAt && displayed->conversionQueuedAt) {
            // Fence readiness is sampled at compositor cadence. This includes
            // observation delay; it is deliberately not labelled GPU duration.
            readyWaitObserved.add(displayed->firstReadyObservedAt-displayed->conversionQueuedAt);
            readyToSubmit.add(submittedAt-displayed->firstReadyObservedAt);
          }
          float remaining=runtime.compositor->GetFrameTimeRemaining();
          uint64_t vsync=std::isfinite(remaining)&&remaining>0&&remaining<0.1f ? uint64_t(remaining*1e9) : 0;
          vrost_submit(transport, displayed->timestamp, vsync);
          lastNewTimestamp = displayed->timestamp;
        } else if (submitted) { ++repeated; ++windowReused; }
      } else if (graphics.ready(neutral)) {
        ++fallback;
        const auto submitStart=vrost::monotonicNs();
        graphics.submit(runtime, neutral, head);
        frameTrace.submitNs=vrost::monotonicNs()-submitStart;
        submitCpu.add(frameTrace.submitNs);
      }
      {
        // In-headset status: only while there is no fresh video, or when the quality line is on.
        vrost::StatusState statusState;
        statusState.streaming=streaming; statusState.configured=configured; statusState.fresh=fresh;
        statusState.fatal=fatal; statusState.reconnecting=reconnecting && !configured;
        statusState.staleMs=previousNewSubmit && now>previousNewSubmit ? (now-previousNewSubmit)/1000000 : 0;
        statusState.companion=companionStatus; statusState.error=lastError; statusState.hud=hudLine;
        statusState.untrackedMs=runtime.headUntrackedMs(now);
        if (checking) {
          statusState.check=true;
          statusState.checkRemainingMs=(10000000000ULL-(now-alignmentCheckStart))/1000000;
        }
        statusOverlay.update(statusState);
      }
      const auto handoffStart=vrost::monotonicNs();
      graphics.finishFrame();
      frameTrace.handoffNs=vrost::monotonicNs()-handoffStart;
      handoffCpu.add(frameTrace.handoffNs);
      frameTrace.at=vrost::monotonicNs();
      frameTrace.source=displayed ? displayed->timestamp : 0;
      frameTrace.packets=decoder ? decoder->packets.load() : 0;
      if(auto incident=presentationTrace.record(frameTrace,runtime.refresh))
        vrost::diagnosticLine(vrost::presentationIncidentJson(*incident));
      // RC4.3 eye-tracking panel (local only; the companion never logs or reports these):
      // thirty times a second while a foveated stream plays (1.0.0.3; ten before), so the
      // pages can show the eyes moving.
      if (decoder && decoder->foveated() && displayed && fresh && now - lastFovea >= 33000000ULL) {
        lastFovea = now;
        VrostQuat gaze{};
        uint64_t gazeAgeMs = 0;
        const bool haveGaze = runtime.shownGaze(gaze, now, gazeAgeMs);
        vrost::diagnosticLine(vrost::foveaLine(displayed->conversion, decoder->decodedWidth() / 2, decoder->decodedHeight(),
                                         width, height, haveGaze ? &gaze : nullptr, runtime.views(), gazeAgeMs));
      }
      if (now - lastReport > 1000000000ULL) {
        std::ostringstream metrics;
        metrics << "METRICS state=" << (fatal ? "error" : fresh ? "streaming" : "recovery")
                  << " decoded=" << (decoder ? decoder->decoded.load() : 0)
                  << " dropped=" << (decoder ? decoder->dropped.load() : 0)
                  << " frame_age_ms="
                  << (displayed && now>=displayed->sampledAt ? (now - displayed->sampledAt) / 1000000 : 0)
                  << " prediction_ms=" << runtime.predictionNs.load() / 1000000.0
                  << " tracked=" << tracked << " input_focus=" << runtime.inputFocus()
                  << " submitted=" << submits << " submit_failed=" << failed
                  << " reused=" << repeated << " fallback=" << fallback
                  << " new_frames=" << newFrames
                  << " video_phase=" << videoHandshake.phase()
                  << " keyframe_requests=" << videoHandshake.requests()
                  << " video_silence_ms=" << videoHandshake.silenceMs(now)
                  << " video_packets=" << (decoder ? decoder->packets.load() : 0)
                  << " tracking_samples=" << runtime.trackingSamples.exchange(0)
                  << " tracking_gap_max_us=" << runtime.trackingGapMaxUs.exchange(0)
                  << " head_tracked=" << (runtime.headUntrackedSince.load() ? 0 : 1)
                  << " head_untracked_ms=" << runtime.headUntrackedMs(now)
                  << " head_tracking_result=" << runtime.headTrackingResult.load()
                  << " head_poses=" << runtime.headPoses.exchange(0)
                  << " head_poses_missing=" << runtime.headPosesMissing.exchange(0);
        vrost::diagnosticLine(metrics.str());
        const auto ds=decoder ? decoder->statistics() : vrost::Decoder::Statistics{};
        std::ostringstream timing;
        timing << std::fixed << std::setprecision(3) << "TIMING"
          << " refresh_hz=" << runtime.refresh
          << " unique_fps=" << (now>windowStart ? windowNewFrames*1e9/double(now-windowStart) : 0)
          << " submitted_fps=" << (now>windowStart ? windowSubmits*1e9/double(now-windowStart) : 0)
          << " reused_percent=" << (windowSubmits ? 100.0*windowReused/windowSubmits : 0)
          << " late_frame_catchup=" << lateLatch << " late_frame_catches=" << lateCatches
          << " late_frame_timeouts=" << lateTimeouts
          << " timing_marker_errors=" << graphics.timingMarkerErrors.load()
          << " runtime_events=" << runtimeEventCount << " runtime_event_budgets=" << runtimeEventBudgets
          << " runtime_process_exits=" << runtimeExitedProcesses << " runtime_geometry_reads=" << runtimeGeometryReads
          << " presentation_incidents=" << presentationTrace.incidents
          << " presentation_incidents_suppressed=" << presentationTrace.suppressed
          << " encoded_queued=" << ds.encodedQueued << " encoded_queue_peak_observed=" << ds.queueHighWater
          << " pending_surfaces=" << ds.pendingFrames << " decoded_superseded=" << ds.superseded
          << " decoded_mailbox_queued=" << ds.decodedQueued
          << " decoded_mailbox_peak=" << ds.decodedQueueHighWater
          << " unconverted_superseded=" << ds.beforeConversionSuperseded
          << " decode_outputs=" << (decoder ? decoder->decodeOutputs.load() : 0)
          << " packets=" << (decoder ? decoder->packets.load() : 0)
          << " packet_rejected=" << (decoder ? decoder->rejected.load() : 0)
          << " timing_unmatched=" << (decoder ? decoder->missingTiming.load() : 0);
        {
          // Network health (patch 0008) and stream format per window.
          VrostStreamCounters counters{};
          vrost_stream_counters(transport, &counters);
          auto delta=[](uint64_t now, uint64_t before) { return now>=before ? now-before : now; };
          const uint64_t tenBit=decoder ? decoder->tenBitFrames.load() : 0;
          const uint64_t foveatedNow=decoder ? decoder->foveatedFrames.load() : 0;
          const uint64_t gazeNow=decoder ? decoder->gazeCentredFrames.load() : 0;
          timing << " net_frames=" << delta(counters.frames, previousCounters.frames)
                 << " net_lost_frames=" << delta(counters.network_losses, previousCounters.network_losses)
                 << " net_keyframe_requests=" << delta(counters.idr_requests, previousCounters.idr_requests)
                 << " net_decoder_saturation=" << delta(counters.decoder_saturation, previousCounters.decoder_saturation)
                 << " net_skipped_until_keyframe=" << delta(counters.skipped_until_idr, previousCounters.skipped_until_idr)
                 << " net_lost_frames_total=" << counters.network_losses
                 << " net_fec_video=" << delta(counters.fec_video_recovered, previousCounters.fec_video_recovered)
                 << " net_fec_audio=" << delta(counters.fec_audio_recovered, previousCounters.fec_audio_recovered)
                 << " net_fec_video_total=" << counters.fec_video_recovered
                 << " ten_bit_frames=" << delta(tenBit, previousTenBit)
                 << " foveated_frames=" << delta(foveatedNow, previousFoveated)
                 << " gaze_centred_frames=" << delta(gazeNow, previousGaze)
                 // 1.0.0.14: gaze samples sent turned against the head's motion, and the largest turn (degrees).
                 << " gaze_predicted=" << delta(runtime.gazePredicted.load(), previousGazePredicted)
                 << " gaze_turn_max_deg=" << runtime.gazeTurnMaxMrad.exchange(0) * 0.0572958  // the line is already fixed, 3 decimals
                 // 1.0.0.14: hand samples sent with their acceleration folded in, and the largest acceleration seen (m/s^2).
                 << " acceleration_samples=" << runtime.accelerationSamples.exchange(0)
                 << " acceleration_max=" << runtime.accelerationMaxMm.exchange(0) / 1000.0
                 << " status_overlay=" << statusOverlay.available() << " status_card=" << statusOverlay.styled();
          previousCounters=counters; previousTenBit=tenBit; previousFoveated=foveatedNow; previousGaze=gazeNow;
          previousGazePredicted=runtime.gazePredicted.load();
          // Game-audio playback (1.0.0.9): this window's events and the jitter buffer now.
          VrostAudioStats audio{};
          vrost_audio_stats(transport, &audio);
          timing << " audio_active=" << audio.active << " audio_stream_hz=" << audio.stream_rate
                 << " audio_device_hz=" << audio.device_rate << " audio_channels=" << audio.device_channels
                 << " audio_device_buffer_frames=" << audio.device_buffer_frames
                 << " audio_buffer_ms=" << audio.buffer_ms << " audio_target_ms=" << audio.target_ms
                 << " audio_correction_ppm=" << audio.correction_ppm
                 << " audio_packets=" << delta(audio.packets, previousAudio.packets)
                 << " audio_losses=" << delta(audio.losses, previousAudio.losses)
                 << " audio_underruns=" << delta(audio.underruns, previousAudio.underruns)
                 << " audio_pauses=" << delta(audio.pauses, previousAudio.pauses)
                 << " audio_trims=" << delta(audio.trims, previousAudio.trims)
                 << " audio_underruns_total=" << audio.underruns;
          previousAudio=audio;
        }
        timingFields(timing,"packet_interval",ds.arrivalInterval);
        timingFields(timing,"decode_queue",ds.queue);
        timingFields(timing,"decode_output",ds.decodeOutput);
        timingFields(timing,"decoded_wait",ds.decodedQueue);
        timingFields(timing,"decode_sync_complete",ds.decodeComplete);
        timingFields(timing,"convert_enqueue",ds.conversionEnqueue);
        timingFields(timing,"map_sync_import",ds.mapWait);
        timingFields(timing,"convert_cpu",ds.conversionCpu);
        timingFields(timing,"late_frame_wait",lateFrameWait.take());
        timingFields(timing,"runtime_tick",runtimeTick.take());
        timingFields(timing,"runtime_events",runtimeEvents.take());
        timingFields(timing,"runtime_geometry",runtimeGeometry.take());
        timingFields(timing,"runtime_wait_poses",runtimeWaitPoses.take());
        timingFields(timing,"runtime_focus",runtimeFocus.take());
        timingFields(timing,"runtime_frame_query",runtimeFrameQuery.take());
        timingFields(timing,"submit_cpu",submitCpu.take());
        timingFields(timing,"handoff_cpu",handoffCpu.take());
        timingFields(timing,"ready_observed",readyWaitObserved.take());
        timingFields(timing,"ready_to_submit",readyToSubmit.take());
        timingFields(timing,"arrival_to_submit",arrivalToSubmit.take());
        timingFields(timing,"new_frame_interval",newFrameInterval.take());
        timingFields(timing,"compositor_interval",compositorInterval.take());
        // 1.0.0.11: GPU time of the fused pass (per shown frame) and of the decode-time plane copy.
        timingFields(timing,"frame_pass_gpu",graphics.framePassGpu());
        timingFields(timing,"plane_copy_gpu",graphics.planeCopyGpu());
        const double windowSeconds = now>windowStart ? double(now-windowStart)/1e9 : 0;
        const uint64_t packetsNow = decoder ? decoder->packets.load() : 0;
        if (packetsNow < windowPacketsStart) windowPacketsStart = 0; // new decoder
        vrost::CadenceSample cadence;
        cadence.refresh=runtime.refresh; cadence.streaming=streaming && configured && fresh && !fatal;
        cadence.uniqueFps=windowSeconds>0 ? windowNewFrames/windowSeconds : 0;
        cadence.submittedFps=windowSeconds>0 ? windowSubmits/windowSeconds : 0;
        cadence.packetFps=windowSeconds>0 ? (packetsNow-windowPacketsStart)/windowSeconds : 0;
        cadence.throttledFrames=windowThrottleMax;
        const auto cadenceClass=vrost::classifyCadence(cadence);
        timing << " packet_fps=" << cadence.packetFps
               << " cadence=" << vrost::cadenceName(cadenceClass)
               << " compositor_throttled_frames=" << windowThrottleMax
               << " compositor_reprojected_percent=" << (windowFrameSamples ? 100.0*windowReprojected/windowFrameSamples : 0)
               << " prediction_ms=" << runtime.predictionNs.load()/1e6;
        {
          const auto schedule=runtime.trackingSchedule();
          timing << " reaim=" << (reaim && reaimPossible ? 1 : 0)
                 << " frame_pass=" << (compatibility ? "cpu" : graphics.fusedAvailable() ? "fused" : "two-pass")
                 << " fused_frames=" << windowFused << " fused_skipped=" << fusedSkipped
                 << " sharpening=" << std::clamp(sharpening, 0, 200)
                 << " shown_scale=" << shownScale
                 << " shown_eye_width=" << (decoder ? vrost::Decoder::scaledEye({decoder->shownWidth(), decoder->shownHeight()}, shownScale).width : 0)
                 << " visible_share=" << graphics.visibleShare()
                 << " reaimed_frames=" << windowReaimed << " reaim_fallbacks=" << reaimFallbacks
                 << " reaim_degrees=" << reaimDegrees << " reaim_degrees_max=" << reaimDegreesMax
                 << " tracking_per_frame=" << schedule.perFrame << " tracking_spacing_ms=" << schedule.spacingMs
                 << " tracking_phase_ms=" << schedule.phaseMs << " tracking_shifts=" << schedule.shifts
                 << " tracking_anchored=" << (schedule.anchored ? 1 : 0) << " pose_slot_changes=" << schedule.changes;
          timingFields(timing,"pose_step",poseStep.take());
          windowReaimed=0; reaimDegreesMax=0; windowFused=0;
        }
        if (auto change=cadenceTracker.update(cadenceClass)) {
          std::ostringstream line;
          line << std::fixed << std::setprecision(1)
               << "PRESENTATION_CADENCE {\"from\":\"" << vrost::cadenceName(change->first)
               << "\",\"to\":\"" << vrost::cadenceName(change->second)
               << "\",\"refresh_hz\":" << runtime.refresh << ",\"unique_fps\":" << cadence.uniqueFps
               << ",\"submitted_fps\":" << cadence.submittedFps << ",\"packet_fps\":" << cadence.packetFps
               << ",\"compositor_throttled_frames\":" << windowThrottleMax << "}";
          vrost::diagnosticLine(line.str());
        }
        windowPacketsStart=packetsNow; windowThrottleMax=windowReprojected=windowFrameSamples=0;
        vr::Compositor_FrameTiming ft{}; ft.m_nSize=sizeof(ft);
        if(runtime.compositor->GetFrameTiming(&ft))
          timing << " frame=" << ft.m_nFrameIndex << " dropped=" << ft.m_nNumDroppedFrames
            << " mispresented=" << ft.m_nNumMisPresented << " reprojection_flags=" << ft.m_nReprojectionFlags
            << " gpu_ms=" << ft.m_flTotalRenderGpuMs << " compositor_gpu_ms=" << ft.m_flCompositorRenderGpuMs
            << " interval_ms=" << ft.m_flClientFrameIntervalMs << " submit_ms=" << ft.m_flSubmitFrameMs;
        vrost::diagnosticLine(timing.str());
        windowStart=now; windowNewFrames=windowSubmits=windowReused=0;
        runtimeEventCount=runtimeEventBudgets=runtimeExitedProcesses=runtimeGeometryReads=0;
        lastReport = now;
      }
      } catch(const std::exception& e) {
        // Retire a partially submitted frame before releasing its references.
        try { graphics.finishFrame(); } catch (...) {}
        if(!fatal) {
          fatal=true;fatalAt=vrost::monotonicNs();
          lastError=std::string(e.what()).substr(0,72);
          vrost::diagnosticLine(std::string("FATAL ")+e.what());
          stop();vrost_pause(transport);
        } else {
          if(vrost::monotonicNs()-fatalAt>30000000000ULL) quit=true;
#ifdef _WIN32
          Sleep(10);
#else
          usleep(10000);
#endif
        }
      }
    }
    if(auto incident=presentationTrace.finish())
      vrost::diagnosticLine(vrost::presentationIncidentJson(*incident));
    if (stoppedByCompanion) vrost::diagnosticLine("STATUS Client stopped by the companion");
#ifdef _WIN32
    else if (interrupted)
      vrost::diagnosticLine(std::string("STATUS Client stopped by the Windows console event ") +
                            vrost::windows::ConsoleStop::name(interrupted));
#else
    else if (interrupted) vrost::diagnosticLine("STATUS Client stopped by signal=" + std::to_string(interrupted));
#endif
    runtime.stream(false);
    vrost::diagnosticFlush();
    return fatal ? 1 : 0;
  } catch (const std::exception &e) {
    vrost::diagnosticFlush();
    std::cerr << "ERROR " << e.what() << std::endl;
    return 1;
  }
}
