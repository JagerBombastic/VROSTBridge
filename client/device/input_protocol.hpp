#pragma once
#include <array>
#include <cstdint>
#include <vector>

namespace vrost {
struct InputMapping {
  const char *action;
  const char *path;
  int kind, axis;
};
inline constexpr InputMapping indexMappings[] = {
    {"trigger", "trigger/value", 1, 0},
    {"grip", "squeeze/force", 1, 0},
    {"grip_value", "squeeze/value", 1, 0},
    {"pad_force", "trackpad/force", 1, 0},
    {"stick", "thumbstick/x", 1, 0},
    {"stick", "thumbstick/y", 1, 1},
    {"pad", "trackpad/x", 1, 0},
    {"pad", "trackpad/y", 1, 1},
    {"a", "a/click", 0, 0},
    {"b", "b/click", 0, 0},
    {"a_touch", "a/touch", 0, 0},
    {"b_touch", "b/touch", 0, 0},
    {"trigger_touch", "trigger/touch", 0, 0},
    {"trigger_click", "trigger/click", 0, 0},
    {"stick_click", "thumbstick/click", 0, 0},
    {"stick_touch", "thumbstick/touch", 0, 0},
    {"pad_touch", "trackpad/touch", 0, 0},
    {"remote_menu", "system/click", 0, 0}};

// ALVR f5023f8 server_core/src/connection.rs replaces one shared button
// mapping on every ActiveInteractionProfile packet, ignoring device_id.
// Supplying only one hand's inputs silently disables the other hand.
// Include the complete input set in both packets, also preserving per-device
// profile announcements for transports which do use device_id.
template <typename Register>
void registerControllerProfiles(const std::array<std::vector<uint64_t>, 2> &paths,
                                const std::array<uint64_t, 2> &hands,
                                Register send) {
  std::vector<uint64_t> all(paths[0]);
  all.insert(all.end(), paths[1].begin(), paths[1].end());
  for (auto hand : hands)
    send(hand, all.data(), static_cast<uint32_t>(all.size()));
}

inline bool remoteMenuValue(bool physical, bool chord, bool desktopPulse,
                            int hand) {
  return physical || (hand == 0 && (chord || desktopPulse));
}

// 1.0.0.10: the system button reaches the host as one clean press.
// While the system button is held, the headset PC's own SteamVR takes input focus from this
// client (for its long-press recentre), and without focus every action reads released. In the
// 1.0.0.9 test the host therefore saw a menu press for a few milliseconds or not at all, and its
// menu opened only sometimes. A press is now forwarded for at least minHoldNs and ends with a
// release that is seen (with focus, or from SteamVR's own button event). While the button cannot
// be read, the press ends after minHoldNs, as a tap. One physical press is forwarded once: after a
// press that ended unseen, the next one needs a release first.
class SystemButtonLatch {
public:
  static constexpr uint64_t minHoldNs = 120000000ULL;
  // known: whether the button can be read now; down: its state when it can. Returns what to forward.
  bool update(uint64_t now, bool known, bool down) {
    if (known && !down) armed_ = true;
    if (!held_) {
      if (known && down && armed_) {
        held_ = true; armed_ = false; unseen_ = false; since_ = now; ++presses_;
      }
      return held_;
    }
    if (!known) unseen_ = true;
    if ((!known || !down) && now >= since_ && now - since_ >= minHoldNs) {
      held_ = false;
      lastHeldNs_ = now - since_;
      lastUnseen_ = unseen_;
      if (unseen_) ++unseenEnds_;
    }
    return held_;
  }
  void reset() { held_ = false; armed_ = true; unseen_ = false; since_ = 0; }
  bool held() const { return held_; }
  uint64_t presses() const { return presses_; }
  // Presses during which, for a moment, the button could be read neither with focus nor from
  // SteamVR's events (SystemButtonEvents): such a press may have reached the host as a tap.
  uint64_t unseenEnds() const { return unseenEnds_; }
  uint64_t lastHeldNs() const { return lastHeldNs_; }
  bool lastUnseen() const { return lastUnseen_; }
private:
  bool held_ = false, armed_ = true, unseen_ = false, lastUnseen_ = false;
  uint64_t since_ = 0, presses_ = 0, unseenEnds_ = 0, lastHeldNs_ = 0;
};

// 1.0.0.10: a short loss of input focus keeps the controls as they were.
// The focus SteamVR takes for a system-button press lasts about as long as the press. Releasing
// every control on the host then let go of whatever the player held. A loss shorter than this
// keeps the last values; a longer one (a local overlay that wants the controls) releases them.
inline constexpr uint64_t focusGraceNs = 500000000ULL;
inline bool holdThroughFocusLoss(bool connected, bool focused, uint64_t lostAt, uint64_t now) {
  return connected && !focused && lostAt && now >= lostAt && now - lostAt < focusGraceNs;
}

// 1.0.0.10: SteamVR's own system-button event (VREvent_ButtonPress / Unpress) as a second reading
// of the button, for the moments its input focus hides the action. A press event is believed for
// at most pressTrustNs, and not at all once the action, read with focus, has said released for
// releaseWinsNs: a lost release event can never leave the host's system button held.
struct SystemButtonEvents {
  static constexpr uint64_t pressTrustNs = 10000000000ULL;
  static constexpr uint64_t releaseWinsNs = 250000000ULL;
  static constexpr uint64_t recentNs = 2000000000ULL;
  bool down = false;    // the last event was a press
  uint64_t at = 0;      // when the last event came (0: none yet)
  // known/down for SystemButtonLatch, from the action (when readable) and the events.
  void reading(uint64_t now, bool actionKnown, bool actionDown, bool &known, bool &pressed) {
    const uint64_t age = at && now >= at ? now - at : 0;
    if (down && at && (age > pressTrustNs || (actionKnown && !actionDown && age > releaseWinsNs)))
      down = false;
    const bool recent = at && age < recentNs;
    known = actionKnown || down || recent;
    pressed = (actionKnown && actionDown) || down;
  }
};

// Optical tracking and controller connectivity are independent. Buttons remain
// usable while Lighthouse re-acquires a pose; losing focus or the radio link
// must still release them. Never turn an invalid pose into a fabricated pose.
inline bool controllerActionsAvailable(bool connected, bool focused,
                                       bool actionUpdateSucceeded) {
  return connected && focused && actionUpdateSucceeded;
}

class ControllerInputRefresh {
  uint32_t device_ = UINT32_MAX;
  bool available_ = false, tracked_ = false;
  uint64_t next_ = 0;
public:
  static constexpr uint64_t intervalNs = 250000000;
  void reset() { device_ = UINT32_MAX; available_ = tracked_ = false; next_ = 0; }
  bool due(uint64_t now, uint32_t device, bool available, bool tracked) {
    const bool refresh = !next_ || now >= next_ || device != device_ ||
                         available != available_ || (tracked && !tracked_);
    device_ = device; available_ = available; tracked_ = tracked;
    if (refresh) next_ = now + intervalNs;
    return refresh;
  }
};
} // namespace vrost
