// Contracts for client/device/tracking_schedule.hpp (even pose timing): samples sit on
// a grid locked to the display refresh, the host therefore receives poses of the same
// age for every frame, and a pick point that falls on a sample is moved between two.
#include "client/device/tracking_schedule.hpp"
#include <cstdio>
#include <random>
#include <vector>

namespace {
int failures = 0;
#define CHECK(cond, ...)                                                                           \
  do {                                                                                             \
    if (!(cond)) {                                                                                 \
      ++failures;                                                                                  \
      std::printf("FAIL %s:%d: %s ", __FILE__, __LINE__, #cond);                                   \
      std::printf(__VA_ARGS__);                                                                    \
      std::printf("\n");                                                                           \
    }                                                                                              \
  } while (0)

constexpr uint64_t ms = 1000000;

void rates() {
  CHECK(vrost::TrackingSchedule::samplesPerFrame(75) == 4, "75 Hz: %u", vrost::TrackingSchedule::samplesPerFrame(75));
  CHECK(vrost::TrackingSchedule::samplesPerFrame(90) == 4, "90 Hz: %u", vrost::TrackingSchedule::samplesPerFrame(90));
  CHECK(vrost::TrackingSchedule::samplesPerFrame(120) == 3, "120 Hz: %u", vrost::TrackingSchedule::samplesPerFrame(120));
  CHECK(vrost::TrackingSchedule::samplesPerFrame(144) == 2, "144 Hz: %u", vrost::TrackingSchedule::samplesPerFrame(144));
  CHECK(vrost::TrackingSchedule::samplesPerFrame(0) == 4, "invalid falls back to 90 Hz");
}

void grid() {
  vrost::TrackingSchedule s;
  s.configure(75, 0);
  const uint64_t vsync = 1000 * ms;
  s.anchor(vsync);
  const uint64_t spacing = 13333333 / 4;
  uint64_t t = s.next(vsync);
  CHECK(t == vsync + spacing / 2, "first sample half a spacing after vsync: %llu", (unsigned long long)(t - vsync));
  for (int i = 0; i < 12; ++i) {
    const uint64_t following = s.next(t);
    CHECK(following - t == spacing, "spacing %llu", (unsigned long long)(following - t));
    t = following;
  }
  // A late wake never samples twice within the minimum gap.
  CHECK(s.next(t + spacing - 100000) >= t + spacing - 100000 + 250000, "minimum gap honoured");
  // Before the anchor works too.
  CHECK(s.next(vsync - 20 * ms) < vsync, "times before the anchor map onto the grid");
}

struct Result {
  double stepP95Ms = 0;
  uint32_t shifts = 0;
  uint32_t lastChanges = 0;
};

// The host picks the newest sample that has arrived at a fixed point of every refresh
// (plus jitter). Returns how evenly the picked samples advance from frame to frame.
Result simulate(bool locked, double pickOffsetMs, double jitterMs, int frames) {
  std::mt19937 rng(3);
  std::uniform_real_distribution<double> jitter(-jitterMs, jitterMs);
  const uint64_t period = 13333333, uplink = 2 * ms;
  vrost::TrackingSchedule s;
  s.configure(75, 0);
  s.anchor(0);
  std::vector<uint64_t> samples;
  uint64_t t = 0;
  const uint64_t end = uint64_t(frames + 2) * period;
  // Free-running 4.06 ms timer (RC4.1) or the locked grid. Re-generate after each
  // window because the schedule may shift its phase.
  uint64_t lastPick = 0;
  std::vector<double> steps;
  uint64_t frameStart = period;
  while (frameStart < end) {
    // Produce samples up to this frame's pick point.
    const double pick = double(frameStart) + pickOffsetMs * 1e6 + jitter(rng) * 1e6;
    while (true) {
      const uint64_t following = locked ? s.next(t) : t + 4060000;
      if (double(following + uplink) > pick) break;
      t = following;
      samples.push_back(t);
    }
    const uint64_t picked = samples.empty() ? 0 : samples.back();
    if (lastPick) steps.push_back(std::abs(double(picked - lastPick) - double(period)) / 1e6);
    lastPick = picked;
    if (locked) s.observePick(picked, frameStart);
    frameStart += period;
  }
  std::sort(steps.begin(), steps.end());
  Result r;
  r.stepP95Ms = steps.empty() ? 0 : steps[size_t(steps.size() * .95)];
  const auto stats = s.stats();
  r.shifts = stats.shifts;
  r.lastChanges = stats.changes;
  return r;
}

void evenness() {
  // RC4.1: free-running 4.06 ms sampling gives 12.2/16.2 ms steps (error up to 2.9 ms).
  const auto old = simulate(false, 5.0, .2, 1500);
  CHECK(old.stepP95Ms > 2.0, "free-running sampling should be uneven: p95 %.3f ms", old.stepP95Ms);
  // Locked grid, pick point between samples: every frame gets a sample of the same age.
  const auto locked = simulate(true, 5.0 + 1.0, .3, 1500);
  CHECK(locked.stepP95Ms < .05, "locked sampling should be even: p95 %.3f ms", locked.stepP95Ms);
  CHECK(locked.shifts == 0, "a well-placed grid must not move (%u shifts)", locked.shifts);
}

void centring() {
  // Pick point right on a sample arrival: picks alternate until the grid moves half a spacing.
  // Grid samples sit at 1.667 + k*3.333 ms after vsync; with a 2 ms uplink one arrives
  // at 3.667 + 3.333 = 7.0 ms.
  const auto r = simulate(true, 7.0, .4, 3000);
  CHECK(r.shifts >= 1 && r.shifts <= 2, "expected the grid to move once or twice, moved %u times", r.shifts);
  CHECK(r.lastChanges * 20 <= vrost::TrackingSchedule::windowPicks, "still alternating after centring: %u changes", r.lastChanges);
  CHECK(r.stepP95Ms < .05, "centred picks should be even: p95 %.3f ms", r.stepP95Ms);
}
} // namespace

int main() {
  rates();
  grid();
  evenness();
  centring();
  if (failures) {
    std::printf("%d tracking schedule failures\n", failures);
    return 1;
  }
  std::printf("tracking schedule contracts passed\n");
  return 0;
}
