#include "core/presentation.hpp"
#include "core/stream.hpp"
#include "client/presentation_trace.hpp"
#include <atomic>
#include <limits>
#include <memory>
#include <stdexcept>
#include <thread>
#include <future>

void check(bool value) { if (!value) throw std::runtime_error("presentation regression"); }
int main() {
  check(vrost::runtimeEventBudgetAvailable(0,0));
  check(!vrost::runtimeEventBudgetAvailable(64,100));
  check(!vrost::runtimeEventBudgetAvailable(1,500000));
  check(vrost::presentationStallThreshold(120)==25000000);
  check(vrost::presentationStallThreshold(75)==40000000);
  vrost::PresentationTrace trace;
  vrost::PresentationFrame observed;
  for(uint64_t i=0;i<20;++i) {
    observed.at=1000000000+i*8333333;observed.runtime.frameIndex=uint32_t(i);
    check(!trace.record(observed,120));
  }
  observed.at+=8333333;observed.tickNs=81922000;observed.runtime.frameIndex=20;
  check(!trace.record(observed,120));
  observed.tickNs=0;
  for(int i=0;i<3;++i) { observed.at+=8333333;observed.runtime.frameIndex++;check(!trace.record(observed,120)); }
  observed.at+=8333333;observed.runtime.frameIndex++;
  auto incident=trace.record(observed,120);
  check(incident && incident->size==13 && incident->trigger==8);
  check(incident->frames[0].runtime.frameIndex==12 && incident->frames[8].runtime.frameIndex==20);
  check(incident->frames[8].tickNs==81922000 && incident->frames[12].runtime.frameIndex==24);
  check(vrost::presentationIncidentJson(*incident).find("\"wait_poses_ms\"")!=std::string::npos);
  observed.at+=8333333;observed.tickNs=81000000;
  check(!trace.record(observed,120) && trace.suppressed==1);
  observed.at+=1000000000;
  check(!trace.record(observed,120) && trace.incidents==2);
  incident=trace.finish();
  check(incident && incident->size==9 && !trace.finish()); // Preserve incomplete shutdown incident.

  std::mutex queueMutex;
  bool locked=false;
  std::vector<int> order;
  vrost::PresentationQueue queue([&] { queueMutex.lock();locked=true; },
      [&] { locked=false;queueMutex.unlock(); },
      [&] { check(locked);order.push_back(1); },
      [&] { check(locked);order.push_back(4); });
  queue.beginFrame();
  queue.submit([&] { check(locked);order.push_back(2); });
  queue.submit([&] { check(locked);order.push_back(3); });
  queue.finishFrame([&] { check(locked);order.push_back(5); });
  check((order==std::vector<int>{1,2,3,4,5})); // timing once; retire AFTER handoff
  check(!locked);
  queue.beginFrame();
  try { queue.submit([] { throw std::runtime_error("failed submission"); }); }
  catch (const std::runtime_error &) {}
  check(queueMutex.try_lock());queueMutex.unlock(); // never strand the GPU lock
  queue.finishFrame([] {});
  // Stress both threads using the SAME external queue synchronization.
  std::atomic<int> completed{0};
  std::thread worker([&] { for(int i=0;i<10000;++i) queue.submit([&] { check(locked);++completed; }); });
  for(int i=0;i<500;++i) { queue.beginFrame();queue.submit([&] { check(locked); });queue.finishFrame([&] { check(locked); }); }
  worker.join();check(completed==10000);
  check(vrost::lateFrameBudget(.015f,75)==3000000);           // 3 ms cap
  check(vrost::lateFrameBudget(.015f,90)==2777777);           // a quarter frame at 90 Hz
  check(vrost::lateFrameBudget(.015f,120)==2083333);
  check(vrost::lateFrameBudget(.001f,90)==0);
  check(vrost::lateFrameBudget(std::numeric_limits<float>::quiet_NaN(),90)==0);
  check(vrost::lateFrameBudget(.015f,0)==0);
  check(vrost::lateFrameBudget(.0025f,90)<=500000);           // the 2 ms submission margin holds
  check(vrost::lateFrameBudget(.015f,144)==1736111);          // a quarter frame at 144 Hz
  struct Frame { uint64_t timestamp;bool ready; };
  vrost::ReadyFrames<std::shared_ptr<Frame>> frames;
  uint64_t now=0,waited=0;
  auto take=[&] {return frames.takeLatest(0,[](auto f) {return f->ready;});};
  auto older=std::make_shared<Frame>(Frame{1,true});
  auto newer=std::make_shared<Frame>(Frame{2,false});
  frames.push(older);frames.push(newer);
  auto result=vrost::selectUntilReady(1500000,[&] {return now;},take,[&](auto) {++waited;});
  check(result==older && waited==0); // ready image is never delayed by an unfinished one
  result=vrost::selectUntilReady(1500000,[&] {return now;},take,[&](auto remaining) {
    check(remaining==1500000);now+=400000;newer->ready=true;++waited;
  });
  check(result==newer && waited==1 && now==400000);
  result=vrost::selectUntilReady(1500000,[&] {return now;},take,[&](auto remaining) {now+=remaining;++waited;});
  check(!result && now==1900000 && waited==2); // no frames cannot extend deadline
  result=vrost::selectUntilReady(0,[&] {return now;},take,[&](auto) {throw std::runtime_error("zero budget waited");});
  check(!result);
  // Decoder output ownership remains bounded while conversion is stalled.
  // Replacing a decoded image must release its reference, but must not release
  // the image that a consumer is currently synchronizing/reading.
  vrost::DecodedMailbox<std::shared_ptr<Frame>> mailbox;
  auto one = std::make_shared<Frame>(Frame{1, false});
  std::weak_ptr<Frame> firstReference = one;
  check(mailbox.push(std::move(one)));
  auto two = std::make_shared<Frame>(Frame{2, false});
  std::weak_ptr<Frame> secondReference = two;
  check(mailbox.push(std::move(two)));
  auto three = std::make_shared<Frame>(Frame{3, false});
  std::weak_ptr<Frame> thirdReference = three;
  check(mailbox.push(std::move(three)) && firstReference.expired());
  auto stats = mailbox.statistics();
  check(stats.queued == 2 && stats.highWater == 2 && stats.superseded == 1);
  std::shared_ptr<Frame> converting;
  check(mailbox.popLatest(converting) && converting->timestamp == 3);
  check(secondReference.expired() && !thirdReference.expired());
  stats = mailbox.statistics();
  check(stats.queued == 0 && stats.superseded == 2);
  auto four = std::make_shared<Frame>(Frame{4, false});
  std::weak_ptr<Frame> fourthReference = four;
  check(mailbox.push(std::move(four)));
  mailbox.stop();
  check(fourthReference.expired() && !thirdReference.expired());
  check(!mailbox.push(std::make_shared<Frame>(Frame{5, false})));
  check(!mailbox.popLatest(result));
  converting.reset();
  check(thirdReference.expired());
  // An idle conversion worker must also wake at shutdown; it cannot wait for
  // a new packet after a stream/configuration epoch has ended.
  vrost::DecodedMailbox<std::shared_ptr<Frame>> idle;
  std::promise<void> entering;
  std::thread stoppedConsumer([&] {
    entering.set_value();
    std::shared_ptr<Frame> frame;
    check(!idle.popLatest(frame));
  });
  entering.get_future().wait();
  idle.stop();
  stoppedConsumer.join();
  // Race producer and consumer across many queue overflows. Delivered output
  // is strictly increasing; missing decoded pictures are allowed, compressed
  // packet dropping is not part of this queue's contract.
  vrost::DecodedMailbox<std::shared_ptr<Frame>> concurrent;
  std::promise<void> lastReceived;
  auto receivedLast = lastReceived.get_future();
  std::thread consumer([&] {
    uint64_t previous = 0;
    std::shared_ptr<Frame> frame;
    while (concurrent.popLatest(frame)) {
      check(frame->timestamp > previous);
      previous = frame->timestamp;
      if (previous == 10000) { lastReceived.set_value(); break; }
    }
  });
  for (uint64_t i = 1; i <= 10000; ++i)
    check(concurrent.push(std::make_shared<Frame>(Frame{i, false})));
  check(receivedLast.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
  concurrent.stop();
  consumer.join();
  check(concurrent.statistics().highWater <= 2);
  {
    // Half-rate presentation is classified per window, not by stall threshold.
    check(vrost::presentationStallThreshold(75)>27000000ULL); // why the trace missed 37 fps
    vrost::CadenceSample s; s.refresh=75; s.streaming=true;
    s.uniqueFps=74; s.submittedFps=75; s.packetFps=75;
    check(vrost::classifyCadence(s)==vrost::Cadence::Full);
    s.uniqueFps=37.5; s.submittedFps=37.5; s.packetFps=75; // SteamVR throttling the client
    check(vrost::classifyCadence(s)==vrost::Cadence::ClientThrottled);
    s.submittedFps=75; s.throttledFrames=1;
    check(vrost::classifyCadence(s)==vrost::Cadence::ClientThrottled);
    s.throttledFrames=0; s.packetFps=37.5;                  // host or network halving
    check(vrost::classifyCadence(s)==vrost::Cadence::SourceLimited);
    s.packetFps=75;                                         // arrive but miss deadline / pairs
    check(vrost::classifyCadence(s)==vrost::Cadence::LateOrPaired);
    s.streaming=false; check(vrost::classifyCadence(s)==vrost::Cadence::Idle);
    check(std::string(vrost::cadenceName(vrost::Cadence::ClientThrottled))=="client_throttled");
    vrost::CadenceTracker tracker;
    check(!tracker.update(vrost::Cadence::Full));              // one window is not a change
    auto change=tracker.update(vrost::Cadence::Full);
    check(change && change->first==vrost::Cadence::Idle && change->second==vrost::Cadence::Full);
    check(!tracker.update(vrost::Cadence::ClientThrottled));   // a single hitch does not flap
    check(!tracker.update(vrost::Cadence::Full));
    check(!tracker.update(vrost::Cadence::ClientThrottled));
    change=tracker.update(vrost::Cadence::ClientThrottled);
    check(change && change->second==vrost::Cadence::ClientThrottled && tracker.current()==vrost::Cadence::ClientThrottled);
  }
  std::cout << "Queue serialization, explicit timing, post-handoff retirement, exception unlock, "
               "bounded late selection, ready-first, timeout, decoded ownership, "
               "mailbox overflow/order and worker shutdown contracts passed\n";
}
