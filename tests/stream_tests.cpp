#include "client/device/prediction.hpp"
#include "core/stream.hpp"
#include <iostream>
#include <memory>
#include <stdexcept>
void check(bool v) {
  if (!v)
    throw std::runtime_error("stream contract failed");
}
int main() {
  static_assert(sizeof(VrostQuat) == 16 && sizeof(VrostPose) == 28 &&
                sizeof(VrostView) == 44 && sizeof(VrostMotion) == 64 &&
                sizeof(VrostEvent) == 48);
  for (float yaw : {0.f, .6f, 3.14159265f, -1.7f}) {
    VrostPose p{{0, std::sin(yaw / 2), 0, std::cos(yaw / 2)}, {1, 2, -3}};
    auto m = vrost::matrix(p);
    auto round = vrost::matrix(vrost::quaternion(m));
    for (int i = 0; i < 3; ++i)
      for (int j = 0; j < 4; ++j)
        check(std::abs(m.m[i][j] - round.m[i][j]) < 1e-5f);
  }
  auto fresh=vrost::remoteFrameState(true,true,true,1020000000,1000000000);
  check(fresh.fresh && fresh.hold);
  auto hiccup=vrost::remoteFrameState(true,true,true,1120000000,1000000000);
  check(!hiccup.fresh && hiccup.hold);
  // 1.0.0.4: a stall of a quarter second keeps the last frame (re-aimed); a whole second does not.
  auto stall=vrost::remoteFrameState(true,true,true,1250000000,1000000000);
  check(!stall.fresh && stall.hold);
  check(vrost::remoteFrameState(true,true,true,1999999999,1000000000).hold);
  check(!vrost::remoteFrameState(true,true,true,2000000000,1000000000).hold);
  check(!vrost::remoteFrameState(false,true,true,1020000000,1000000000).hold);
  check(!vrost::remoteFrameState(true,false,true,1020000000,1000000000).hold);
  check(!vrost::remoteFrameState(true,true,false,1020000000,1000000000).hold);
  check(!vrost::remoteFrameState(true,true,true,999999999,1000000000).hold);
  VrostView local[2]{};
  for (int e = 0; e < 2; ++e)
    local[e] = {
        {{0, 0, 0, 1}, {e ? .032f : -.032f, 0, 0}}, -.8f, .9f, .85f, -.75f};
  VrostPose hp{{0, .5f, 0, .8660254f}, {1, 2, 3}};
  VrostView source[2];
  for (int e = 0; e < 2; ++e) {
    source[e] = local[e];
    source[e].pose =
        vrost::quaternion(vrost::compose(vrost::matrix(hp), vrost::matrix(local[e].pose)));
  }
  vrost::Pose head;
  check(vrost::sourceHead(source, local, head));
  check(std::abs(head.m[0][3] - 1) < 1e-5f);
  source[1].pose.position[0] += .02f;
  check(!vrost::sourceHead(source, local, head));
  source[1] = source[0];
  source[0].left = 1;
  check(!vrost::sourceHead(source, local, head));
  vrost::PacketQueue q;
  uint8_t bytes[] = {1, 2, 3};
  for (int i = 0; i < 8; ++i)
    check(q.push(i, bytes, 3));
  check(!q.push(9, bytes, 3));
  bytes[0] = 7;
  vrost::Encoded p;
  check(q.pop(p) && p.bytes[0] == 1 && p.timestamp == 0);
  check(p.receivedAt>0 && q.size()==7);
  auto epoch = q.epoch();
  q.reset();
  check(q.epoch() == epoch + 1);
  check(q.push(10, bytes, 3));
  check(q.pop(p) && p.epoch == epoch + 1 && p.timestamp == 10);
  q.stop();
  check(!q.push(11, bytes, 3));
  check(!q.pop(p));
  // Regression: frame 2 is still being converted when frame 1 is ready. The
  // old latest-only slot hid frame 1 and repeated a still older presentation.
  struct Frame { uint64_t timestamp; bool ready; };
  vrost::ReadyFrames<std::shared_ptr<Frame>> frames;
  auto first=std::make_shared<Frame>(Frame{1,true});
  auto second=std::make_shared<Frame>(Frame{2,false});
  frames.push(first); frames.push(second);
  auto ready=[](const auto &frame) { return frame->ready; };
  check(frames.takeLatest(0,ready)==first && frames.size()==1);
  check(!frames.takeLatest(1,ready));
  second->ready=true;
  check(frames.takeLatest(1,ready)==second && frames.size()==0);
  frames.push(std::make_shared<Frame>(Frame{3,true}));
  frames.push(std::make_shared<Frame>(Frame{4,true}));
  auto fifth=std::make_shared<Frame>(Frame{5,false});
  frames.push(fifth);
  check(frames.takeLatest(2,ready)->timestamp==4 && frames.superseded==1);
  check(frames.size()==1); // Newest unfinished conversion remains eligible.
  fifth->ready=true;
  check(frames.takeLatest(4,ready)==fifth);
  // Completed older frames may never replace a newer displayed frame.
  frames.push(std::make_shared<Frame>(Frame{3,true}));
  check(!frames.takeLatest(5,ready) && frames.size()==0);
  for (uint64_t i=6; i<=10; ++i)
    frames.push(std::make_shared<Frame>(Frame{i,true}));
  check(frames.size()==4 && frames.takeLatest(5,ready)->timestamp==10);
  vrost::TimingWindow samples;
  for (uint64_t i=1;i<=100;++i) samples.add(i*1000000);
  auto summary=samples.take();
  check(summary.samples==100 && summary.p50Ms==50 && summary.p95Ms==95 &&
        summary.maxMs==100 && summary.meanMs==50.5);
  check(samples.take().samples==0);
  for (uint64_t i=0;i<600;++i) samples.add(i*1000000);
  summary=samples.take();
  check(summary.samples==512 && summary.maxMs==599);
  {
    // RC2 client prediction: targets are future times, strictly increasing,
    // and map back to their exact acquisition time for ages/hold windows.
    vrost::PredictionClock clock;
    const uint64_t a=clock.target(1000000000ULL,40000000ULL);
    check(a==1040000000ULL && clock.acquiredAt(a,2000000000ULL)==1000000000ULL);
    // A falling latency estimate must never reorder frames.
    const uint64_t b=clock.target(1004000000ULL,10000000ULL);
    check(b==1040000001ULL && b>a && clock.acquiredAt(b,2000000000ULL)==1004000000ULL);
    check(clock.target(1008000000ULL,0)>b);
    // Over-long offsets are capped at the host's 100 ms maximum.
    const uint64_t c=clock.target(2000000000ULL,900000000ULL);
    check(c==2100000000ULL && clock.lastOffset()==100000000ULL);
    // Unknown timestamps fall back to the latest offset and never the future.
    check(clock.acquiredAt(2100000005ULL,2050000000ULL)==2000000005ULL);
    check(clock.acquiredAt(5000000000ULL,2050000000ULL)==2050000000ULL);
    // The RC1.3 hold rule applied to a predicted frame: a 60 ms target ahead of
    // sampling would read as "from the future" and drop to the neutral scene
    // if the target were used. Acquisition time keeps it fresh.
    const uint64_t target=clock.target(3000000000ULL,60000000ULL);
    check(!vrost::remoteFrameState(true,true,true,3030000000ULL,target).hold);
    const auto state=vrost::remoteFrameState(true,true,true,3030000000ULL,clock.acquiredAt(target,3030000000ULL));
    check(state.fresh && state.hold);
    // Prediction off: target equals acquisition time (RC1.3 timestamps).
    vrost::PredictionClock off;
    check(off.target(4000000000ULL,0)==4000000000ULL);
    // RC4.1: poses are sampled for now whatever the offset, so the host's
    // extrapolation is the only prediction; the offset only moves the label.
    vrost::PredictionClock single;
    const auto predicted=vrost::planPoseSample(single,5000000000ULL,37000000ULL);
    check(predicted.secondsFromNow==0.f && predicted.timestamp==5037000000ULL);
    check(single.acquiredAt(predicted.timestamp,5100000000ULL)==5000000000ULL);
    const auto unpredicted=vrost::planPoseSample(off,6000000000ULL,0);
    check(unpredicted.secondsFromNow==0.f && unpredicted.timestamp==6000000000ULL);
  }
  {
    // Bounded, non-blocking diagnostics: telemetry drops when the companion
    // stops reading; lifecycle/error lines and ordering are retained.
    vrost::DiagnosticLog paused(false);
    for (size_t i=0;i<vrost::DiagnosticLog::maxLines;++i) check(paused.push("METRICS x"));
    check(!paused.push("METRICS overflow") && !paused.push("TIMING overflow"));
    check(paused.push("FATAL essential line kept"));
    check(paused.push("STATUS essential line kept"));
    check(paused.droppedPending()==2);
    vrost::DiagnosticLog bytes(false);
    check(bytes.push(std::string(vrost::DiagnosticLog::maxBytes-10,'d')));
    check(!bytes.push("DEBUG beyond byte bound") && bytes.push("ERROR kept"));
    vrost::DiagnosticLog live;
    check(live.push("DEBUG diagnostic writer contract"));
    live.flush();
    check(live.droppedPending()==0);
  }
  std::cout << "Stream ABI, rotations, pose provenance, geometry mismatch, "
               "queue ownership, bounds, epoch, newest-ready selection and timing tests passed\n";
}
