# Controller recovery review for 1.0.0-rc1

The supplied 0.2.14.1 host / 0.2.14 client report contains an actual local
Lighthouse tracking interruption. The right controller explicitly resets after
two seconds without optical samples at 02:44:01.954 and bootstraps again at
02:44:20.267. The left controller has a shorter reset/recovery earlier. These
events occur in the physical client's SteamVR driver. They do not establish why
the controller lost optical visibility, and a bridge update cannot recreate
missing position measurements. The end snapshot shows both controllers connected,
16 active mapped channels per hand and no action API errors.

The review found a separate, concrete recovery defect in VROSTBridge: the host
discarded input updates whenever the controller pose was invalid, while the client
normally sent only changed button values. A release or press during a tracking
interruption could therefore be lost. The host now accepts input for activated,
registered components independently of optical pose validity. Unknown handles,
unactivated devices and invalid scalar values are rejected.

The client now reports physical connection and optical tracking separately. It
continues reading valid physical controller actions while Lighthouse re-acquires a
pose, and immediately re-sends the complete state on recovery, device-index changes
and action availability changes. A small complete-state refresh every 250 ms
repairs transient missed state without delaying immediate button changes. Physical
disconnects, input-focus loss and failed action updates still release controls.
No synthetic pose is sent during optical loss.

The report also exposes two incorrectly described binding channels per hand:
grip capacitance was bound as `force_sensor/value`, and trackpad pressure as
`trackpad/force`. They now use `trigger/pull` and `force_sensor/force`, respectively.
Grip pressure remains a separate force channel. This preserves physical finger
bones/curls and restores the two missing scalar channels.

Lighthouse also logged repeated haptic-queue overflow. The client previously
replayed many overlapping vibration commands in the same tracking tick. It now
mixes a bounded envelope separately for each hand and submits non-overlapping
slices up to 8 ms. Envelopes expire at their original end time (the existing
100 ms per-command limit remains); stalled, disconnected or unfocused commands
are not replayed later. A flood on one hand cannot consume the other's capacity.

The INPUT diagnostics contain `connected`, `tracked`, `device_index`,
`tracking_result`, `tracking_losses`, `full_updates` and `haptic_errors` per hand.
The haptics counters show received, submitted, merged, expired and replaced work.
Controller connection/tracking changes also emit `CONTROLLER_STATE` log lines.

Tests reproduce optical loss/release/recovery, physical disconnect, focus loss,
action errors, missed input state, device-index changes, haptic overlap and expiry,
per-hand flood isolation, invalid values and binding mode/channel validation.
The actual host input helper is compiled and exercised without SteamVR. Full
Windows SteamVR, controller and haptic behavior still needs a physical test.
