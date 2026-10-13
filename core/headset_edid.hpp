#pragma once
// Which head-mounted display an EDID describes (RC4.3), shared by the Game Mode display
// lease helper and mirrored in launcher/game_mode.py (identity), whose tests use the same cases.
//
// RC4.2 recognised only the Valve Index's product codes and one Bigscreen code (BIG 0x1234).
// A Bigscreen Beyond 2e on the RC4.2 test PC was not recognised, so Game Mode never reserved
// it and Start stayed unavailable there. The Linux kernel marks a display "non-desktop" (and
// compositors such as KWin offer it for leasing) from its quirk list or from the EDID itself:
//  - a Microsoft "HMD or specialized display" vendor-specific data block (IEEE OUI CA125C,
//    21-byte payload) in a CTA-861 extension, version 1 or 2, or version 3 without the
//    desktop-usage bit (drm_parse_microsoft_vsdb);
//  - a DisplayID 2.0 section whose primary use case is a head-mounted VR (7) or AR (8)
//    display (update_displayid_info).
// Both are checked here as the kernel reads them, with the Valve Index's codes from the kernel's quirk
// list. The lease helper also accepts the kernel's own non-desktop flag on the leased connector, which
// covers headsets only the quirk list knows. Any other display (a desktop monitor or TV) is never reserved.
#include <cstddef>
#include <cstdint>

namespace vrost {

inline bool edidBaseValid(const uint8_t *edid, size_t size) {
  static const uint8_t header[8] = {0, 255, 255, 255, 255, 255, 255, 0};
  if (!edid || size < 128) return false;
  unsigned sum = 0;
  for (size_t i = 0; i < 128; ++i) sum += edid[i];
  for (size_t i = 0; i < 8; ++i)
    if (edid[i] != header[i]) return false;
  return sum % 256 == 0;
}

// Extension blocks present in the buffer (the count the base block announces, bounded by size).
inline size_t edidExtensions(const uint8_t *edid, size_t size) {
  const size_t present = size / 128 - 1, announced = edid[126];
  return announced < present ? announced : present;
}

inline bool edidMicrosoftHmd(const uint8_t *edid, size_t size) {
  if (!edidBaseValid(edid, size)) return false;
  for (size_t block = 1; block <= edidExtensions(edid, size); ++block) {
    const uint8_t *ext = edid + block * 128;
    if (ext[0] != 0x02 || ext[1] < 3) continue;  // CTA-861 revision 3 or later has a data block collection
    const size_t end = ext[2];                   // data blocks lie in bytes 4 .. d-1
    if (end < 4 || end > 127) continue;
    for (size_t at = 4; at < end;) {
      const unsigned tag = ext[at] >> 5, length = ext[at] & 0x1f;
      if (at + 1 + length > end) break;
      if (tag == 3 && length == 21 && ext[at + 1] == 0x5c && ext[at + 2] == 0x12 && ext[at + 3] == 0xca) {
        const unsigned version = ext[at + 4];
        const bool desktop = ext[at + 5] & 0x40;
        if (version == 1 || version == 2 || (version == 3 && !desktop)) return true;
      }
      at += 1 + length;
    }
  }
  return false;
}

// As update_displayid_info reads it: the first DisplayID extension whose section checksum is valid
// and that holds at least one complete data block decides; DisplayID 2.0 with primary use 7 or 8.
inline bool edidDisplayIdHmd(const uint8_t *edid, size_t size) {
  if (!edidBaseValid(edid, size)) return false;
  for (size_t block = 1; block <= edidExtensions(edid, size); ++block) {
    const uint8_t *ext = edid + block * 128;
    // Section header at byte 1: version, payload bytes, primary use case (2.0), extension count;
    // then the data blocks and one checksum byte.
    const unsigned bytes = ext[2];
    if (ext[0] != 0x70 || bytes > 121) continue;
    unsigned sum = 0;
    for (unsigned i = 1; i < 6 + bytes; ++i) sum += ext[i];
    if (sum % 256) continue;
    if (bytes < 3 || 3u + ext[7] > bytes) continue;  // one complete data block: tag, revision, length, payload
    return ext[1] == 0x20 && (ext[3] == 7 || ext[3] == 8);
  }
  return false;
}

// "Valve Index", "Bigscreen Beyond", "VR headset" (another display marked head-mounted), or nullptr.
inline const char *headsetModel(const uint8_t *edid, size_t size) {
  if (!edidBaseValid(edid, size)) return nullptr;
  const unsigned maker = unsigned(edid[8]) << 8 | edid[9], product = edid[10] | unsigned(edid[11]) << 8;
  const auto code = [](char a, char b, char c) { return unsigned((a - '@') << 10 | (b - '@') << 5 | (c - '@')); };
  if (maker == code('V', 'L', 'V') && (product == 0x91a8 || (product >= 0x91b0 && product <= 0x91bf)))
    return "Valve Index";
  if (maker == code('B', 'I', 'G') && product == 0x1234) return "Bigscreen Beyond";
  if (!edidMicrosoftHmd(edid, size) && !edidDisplayIdHmd(edid, size)) return nullptr;
  if (maker == code('V', 'L', 'V')) return "Valve Index";
  if (maker == code('B', 'I', 'G')) return "Bigscreen Beyond";
  return "VR headset";
}

}  // namespace vrost
