#include "client/device/input_protocol.hpp"
#include "client/device/haptic_scheduler.hpp"
#include <limits>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>

void check(bool value) {
  if (!value) throw std::runtime_error("controller input contract failed");
}
int main() {
  std::array<std::vector<uint64_t>, 2> paths;
  for (size_t i = 0; i < std::size(vrost::indexMappings); ++i) {
    paths[0].push_back(100 + i);
    paths[1].push_back(200 + i);
  }
  // Model the pinned server's actual replace-on-profile behavior. In 0.2.4
  // the second right-only packet wiped out every left input from this map.
  std::set<uint64_t> serverMapping;
  unsigned registrations = 0;
  vrost::registerControllerProfiles(paths, {1, 2},
      [&](uint64_t hand, const uint64_t *inputs, uint32_t count) {
        check(hand == ++registrations);
        serverMapping = {inputs, inputs + count};
        check(count == 2 * std::size(vrost::indexMappings));
        for (const auto &side : paths)
          for (auto input : side) check(serverMapping.count(input) == 1);
      });
  check(registrations == 2);
  check(serverMapping.count(paths[0][0]) == 1); // left trigger remains mapped
  check(serverMapping.count(paths[1][4]) == 1); // right stick remains mapped
  std::set<std::string> suffixes;
  for (const auto &mapping : vrost::indexMappings)
    check(suffixes.insert(mapping.path).second);
  check(suffixes.count("system/click") == 1);
  check(suffixes.count("a/click") == 1); // Index has A/B on both hands, not X/Y.
  for (int hand : {0, 1}) {
    check(vrost::remoteMenuValue(true, false, false, hand));
    check(!vrost::remoteMenuValue(false, false, false, hand));
    check(vrost::remoteMenuValue(true, true, true, hand));
  }
  check(vrost::remoteMenuValue(false, true, false, 0));
  check(vrost::remoteMenuValue(false, false, true, 0));
  check(!vrost::remoteMenuValue(false, true, true, 1)); // never duplicate one pulse

  // 1.0.0.10: the system button as one clean press (SystemButtonLatch).
  {
    constexpr uint64_t ms = 1000000ULL;
    vrost::SystemButtonLatch latch;
    // A normal press with focus: forwarded while held, released with the button.
    check(!latch.update(0, true, false));
    check(latch.update(10 * ms, true, true));
    check(latch.update(300 * ms, true, true));
    check(!latch.update(310 * ms, true, false));
    check(latch.presses() == 1 && latch.lastHeldNs() == 300 * ms && !latch.lastUnseen());
    // A very short tap still reaches the host for the minimum hold.
    check(latch.update(400 * ms, true, true));
    check(latch.update(420 * ms, true, false));            // released after 20 ms...
    check(latch.update(519 * ms, true, false));            // ...still forwarded...
    check(!latch.update(520 * ms, true, false));           // ...until 120 ms have passed
    // The 1.0.0.9 failure: the press is read, then SteamVR takes focus 5 ms later for the length
    // of the press. 1.0.0.9 forwarded 5 ms of it; now it is one tap of the minimum hold.
    check(latch.update(1000 * ms, true, true));
    check(latch.update(1005 * ms, false, false));          // focus taken: the action reads released
    check(latch.update(1119 * ms, false, false));
    check(!latch.update(1120 * ms, false, false));         // ends unseen, as a tap
    check(latch.unseenEnds() == 1 && latch.lastUnseen());
    check(!latch.update(1213 * ms, true, false));          // focus back, button up: no second press
    check(latch.presses() == 3);
    // Focus back while the same press is still held: it is not forwarded twice.
    check(latch.update(2000 * ms, true, true));
    check(!latch.update(2200 * ms, false, false));
    check(!latch.update(2300 * ms, true, true));           // the same press, seen again
    check(latch.presses() == 4);
    check(!latch.update(2400 * ms, true, false));          // its release arms the next press
    check(latch.update(2500 * ms, true, true) && latch.presses() == 5);
    latch.reset();
    check(!latch.held() && latch.update(3000 * ms, true, true));

    // SteamVR's own button events read the button while focus hides the action.
    vrost::SystemButtonEvents events;
    vrost::SystemButtonLatch tap;
    bool known = false, pressed = false;
    events.down = true; events.at = 5000 * ms;             // ButtonPress, focus already taken
    events.reading(5001 * ms, false, false, known, pressed);
    check(known && pressed && tap.update(5001 * ms, known, pressed));
    events.reading(5150 * ms, false, false, known, pressed);
    check(tap.update(5150 * ms, known, pressed));
    events.down = false; events.at = 5200 * ms;            // ButtonUnpress
    events.reading(5201 * ms, false, false, known, pressed);
    check(known && !pressed && !tap.update(5201 * ms, known, pressed));
    check(tap.lastHeldNs() == 200 * ms && !tap.lastUnseen());
    // A lost release event never leaves the button held: with focus, the action wins after 250 ms...
    events.down = true; events.at = 6000 * ms;
    events.reading(6100 * ms, true, false, known, pressed);
    check(known && pressed);
    events.reading(6251 * ms, true, false, known, pressed);
    check(known && !pressed && !events.down);
    // ...and without focus a press event is believed for 10 s at most.
    events.down = true; events.at = 7000 * ms;
    events.reading(16999 * ms, false, false, known, pressed);
    check(pressed);
    events.reading(17001 * ms, false, false, known, pressed);
    check(!pressed && !known);
    // No events at all: only the action counts.
    vrost::SystemButtonEvents none;
    none.reading(1 * ms, false, false, known, pressed);
    check(!known && !pressed);
    none.reading(2 * ms, true, true, known, pressed);
    check(known && pressed);

    // A short loss of focus keeps the controls; a long one, or a lost controller, releases them.
    check(vrost::holdThroughFocusLoss(true, false, 1000 * ms, 1000 * ms));
    check(vrost::holdThroughFocusLoss(true, false, 1000 * ms, 1499 * ms));
    check(!vrost::holdThroughFocusLoss(true, false, 1000 * ms, 1500 * ms));
    check(!vrost::holdThroughFocusLoss(false, false, 1000 * ms, 1100 * ms));
    check(!vrost::holdThroughFocusLoss(true, true, 1000 * ms, 1100 * ms));
    check(!vrost::holdThroughFocusLoss(true, false, 0, 1100 * ms));
  }

  // Reproduce the Alyx failure sequence: optical loss while holding a control,
  // release during invalid pose, re-acquisition, then unchanged button state.
  vrost::ControllerInputRefresh refresh;
  float previous = -1, hostValue = 0;
  auto frame = [&](uint64_t ns, bool connected, bool tracked, bool focused,
                   bool actionOkay, float value, bool deliver = true, uint32_t device = 3) {
    const bool available = vrost::controllerActionsAvailable(connected, focused, actionOkay);
    if (!available) value = 0;
    const bool full = refresh.due(ns, device, available, tracked);
    if (full || value != previous) {
      if (deliver) hostValue = value; // Activated host components accept invalid-pose releases.
      previous = value;
    }
    return full;
  };
  check(frame(1, true, true, true, true, 1)); check(hostValue == 1);
  frame(4000001, true, false, true, true, 1); check(hostValue == 1); // No optical-loss release.
  frame(8000001, true, false, true, true, 0); check(hostValue == 0);
  frame(12000001, true, false, true, true, 1); check(hostValue == 1); // Actions still work.
  check(frame(16000001, true, true, true, true, 1)); // Replay on tracking recovery.
  check(frame(20000001, false, false, true, true, 1)); check(hostValue == 0); // Radio loss releases.
  check(frame(24000001, true, true, true, true, 1)); check(hostValue == 1);
  check(frame(28000001, true, true, false, true, 1)); check(hostValue == 0); // Dashboard focus.
  check(frame(32000001, true, true, true, true, 1)); check(hostValue == 1);
  check(frame(36000001, true, true, true, false, 1)); check(hostValue == 0); // Action API failure.
  check(frame(40000001, true, true, true, true, 1)); check(hostValue == 1);
  frame(44000001, true, true, true, true, 0, false); // Lost state during host component reactivation.
  check(hostValue == 1);
  check(!frame(48000001, true, true, true, true, 0));
  check(frame(290000001, true, true, true, true, 0)); check(hostValue == 0); // Bounded repair.
  check(frame(294000001, true, true, true, true, 0, true, 7)); // Device-index reassignment.

  vrost::HapticScheduler haptics;
  haptics.add(0, {.1f, 150, .4f}, 1);
  haptics.add(0, {.020f, 220, .9f}, 1);
  auto pulse = haptics.poll(0, 1, true);
  check(pulse && pulse->amplitude == .9f && pulse->frequency == 220);
  check(pulse->duration <= .008001f);
  check(!haptics.poll(0, 4000001, true)); // Do not queue overlapping Lighthouse commands.
  pulse = haptics.poll(0, 8000001, true); check(pulse && pulse->amplitude == .9f);
  pulse = haptics.poll(0, 20000001, true); check(pulse && pulse->amplitude == .4f); // Strong pulse ends.
  check(!haptics.poll(0, 100000002, true)); // Never extend the original envelope.
  haptics.add(1, {.05f, 100, .5f}, 100000002);
  check(!haptics.poll(1, 150000003, true)); // Delayed commands expire, never replay after a stall.
  haptics.add(1, {.1f, 100, .5f}, 160000003);
  check(!haptics.poll(1, 160000003, false));
  check(!haptics.poll(1, 164000003, true)); // Focus loss clears pending vibrations.
  for (int i=0; i<100; ++i) haptics.add(0, {.1f, 100, .7f}, 200000000 + i);
  haptics.add(1, {.02f, 80, .5f}, 200000100);
  check(haptics.replaced == 84);
  check(haptics.poll(0, 200000100, true).has_value());
  check(haptics.poll(1, 200000100, true).has_value()); // Flooding one hand cannot starve the other.
  haptics.clear();
  haptics.add(0, {.1f, 100, std::numeric_limits<float>::quiet_NaN()}, 300000000);
  check(!haptics.poll(0, 300000000, true));
  {
    // RC2: long rumbles keep their length (bounded at 1 s), zero amplitude
    // stops, and a zero-duration "minimal" pulse is short rather than dropped.
    vrost::HapticScheduler h;
    h.add(0, {.5f, 160, .8f}, 1000);
    check(h.poll(0, 1000, true).has_value());
    check(h.poll(0, 400001000, true).has_value());     // RC1.3 ended this at 100 ms
    check(!h.poll(0, 500001001, true));                // ends at its own end time
    h.add(1, {5.f, 160, .8f}, 0);
    check(h.poll(1, 999000000, true).has_value() && !h.poll(1, 1000000001, true)); // 1 s bound
    h.add(0, {.5f, 160, .8f}, 2000000000);
    check(h.poll(0, 2000000000, true).has_value());
    h.add(0, {.5f, 160, 0.f}, 2004000000);              // explicit stop
    check(!h.poll(0, 2010000000, true) && h.stopped == 1);
    h.add(0, {0.f, 160, .6f}, 3000000000);              // minimal pulse
    auto minimal = h.poll(0, 3000000000, true);          // 10 ms, sent as 8 + 2 ms slices
    check(minimal && minimal->duration > .0079f && minimal->duration <= .0081f);
    minimal = h.poll(0, 3008000000, true);
    check(minimal && minimal->duration > .0019f && minimal->duration <= .0021f);
    check(!h.poll(0, 3011000000, true));
  }
  std::cout << "Controller profile, optical-loss recovery, action release, resync, system-button latch, focus grace and haptic contracts passed\n";
}
