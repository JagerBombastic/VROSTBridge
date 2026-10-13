// Contracts for core/headset_edid.hpp (RC4.3). tests/test_rc43.py checks the Python mirror with the same cases.
#include "core/headset_edid.hpp"
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

using Bytes = std::vector<uint8_t>;

static void checksum(uint8_t *block) {
  unsigned sum = 0;
  for (int i = 0; i < 127; ++i) sum += block[i];
  block[127] = uint8_t((256 - sum % 256) % 256);
}

static Bytes edid(const char *maker, unsigned product, const std::vector<Bytes> &extensions = {}) {
  Bytes out(128 * (1 + extensions.size()), 0);
  const uint8_t header[8] = {0, 255, 255, 255, 255, 255, 255, 0};
  std::memcpy(out.data(), header, 8);
  const unsigned m = unsigned((maker[0] - '@') << 10 | (maker[1] - '@') << 5 | (maker[2] - '@'));
  out[8] = uint8_t(m >> 8); out[9] = uint8_t(m);
  out[10] = uint8_t(product); out[11] = uint8_t(product >> 8);
  out[18] = 1; out[19] = 4;  // EDID 1.4
  out[126] = uint8_t(extensions.size());
  checksum(out.data());
  for (size_t i = 0; i < extensions.size(); ++i) {
    std::memcpy(out.data() + 128 * (i + 1), extensions[i].data(), 128);
    checksum(out.data() + 128 * (i + 1));
  }
  return out;
}

// A CTA-861 extension holding one Microsoft HMD / specialized display VSDB.
static Bytes cta_microsoft(unsigned version, bool desktop, unsigned payload = 21) {
  Bytes block(128, 0);
  block[0] = 0x02; block[1] = 3;
  block[4] = uint8_t(3 << 5 | payload);
  block[5] = 0x5c; block[6] = 0x12; block[7] = 0xca;  // IEEE OUI CA125C, little-endian
  block[8] = uint8_t(version);
  block[9] = uint8_t(desktop ? 0x40 : 0x00);
  block[2] = uint8_t(4 + 1 + payload);  // DTDs (none) start after the data block collection
  return block;
}

// A DisplayID section with one 4-byte data block (tag, revision, length 1, payload) and a valid checksum.
static Bytes displayid(unsigned version, unsigned use, bool validChecksum = true, bool withBlock = true) {
  Bytes block(128, 0);
  const unsigned bytes = withBlock ? 4 : 0;
  block[0] = 0x70; block[1] = uint8_t(version); block[2] = uint8_t(bytes); block[3] = uint8_t(use); block[4] = 0;
  if (withBlock) { block[5] = 0x7e; block[6] = 0; block[7] = 1; block[8] = 0; }
  unsigned sum = 0;
  for (unsigned i = 1; i < 5 + bytes; ++i) sum += block[i];
  block[5 + bytes] = uint8_t((256 - sum % 256) % 256 + (validChecksum ? 0 : 1));
  return block;
}

static std::string model(const Bytes &value) {
  const char *name = vrost::headsetModel(value.data(), value.size());
  return name ? name : "";
}

int main() {
  // The kernel's Valve Index codes; RC4.2's Bigscreen code still works.
  CHECK(model(edid("VLV", 0x91a8)) == "Valve Index");
  CHECK(model(edid("VLV", 0x91b5)) == "Valve Index");
  CHECK(model(edid("VLV", 0x91c0)).empty());
  CHECK(model(edid("BIG", 0x1234)) == "Bigscreen Beyond");
  // A Bigscreen headset with another product code, marked head-mounted by its EDID.
  CHECK(model(edid("BIG", 0x4a21, {cta_microsoft(3, false)})) == "Bigscreen Beyond");
  CHECK(model(edid("BIG", 0x4a21)).empty());  // never any display only by maker
  // Microsoft VSDB versions as the kernel reads them.
  CHECK(model(edid("ABC", 0x0001, {cta_microsoft(1, false)})) == "VR headset");
  CHECK(model(edid("ABC", 0x0001, {cta_microsoft(2, true)})) == "VR headset");
  CHECK(model(edid("DEL", 0x4321, {cta_microsoft(3, true)})).empty());  // a specialized desktop display
  CHECK(model(edid("DEL", 0x4321, {cta_microsoft(3, false, 20)})).empty());  // wrong payload length
  // DisplayID 2.0 head-mounted VR / AR primary use.
  CHECK(model(edid("ABC", 0x0002, {displayid(0x20, 7)})) == "VR headset");
  CHECK(model(edid("ABC", 0x0002, {displayid(0x20, 8)})) == "VR headset");
  CHECK(model(edid("ABC", 0x0002, {displayid(0x12, 7)})).empty());
  CHECK(model(edid("ABC", 0x0002, {displayid(0x20, 4)})).empty());
  CHECK(model(edid("BIG", 0x4a21, {cta_microsoft(3, true), displayid(0x20, 7)})) == "Bigscreen Beyond");
  // As the kernel reads them: a bad section checksum or no data block, and CTA revisions 1-2, do not count.
  CHECK(model(edid("ABC", 0x0002, {displayid(0x20, 7, false)})).empty());
  CHECK(model(edid("ABC", 0x0002, {displayid(0x20, 7, true, false)})).empty());
  Bytes oldCta = cta_microsoft(1, false);
  oldCta[1] = 2;
  CHECK(model(edid("ABC", 0x0001, {oldCta})).empty());
  // The first valid DisplayID section decides.
  CHECK(model(edid("ABC", 0x0002, {displayid(0x20, 4), displayid(0x20, 7)})).empty());
  CHECK(model(edid("ABC", 0x0002, {displayid(0x20, 7, false), displayid(0x20, 7)})) == "VR headset");
  // Damaged or truncated data.
  Bytes bad = edid("VLV", 0x91a8);
  bad[20] ^= 1;
  CHECK(model(bad).empty());
  Bytes truncated = edid("BIG", 0x4a21, {cta_microsoft(3, false)});
  truncated.resize(128);  // the base block announces one extension that is missing
  CHECK(model(truncated).empty());
  Bytes overrun = cta_microsoft(3, false);
  overrun[2] = 10;  // the data block runs past the collection's end
  CHECK(model(edid("BIG", 0x4a21, {overrun})).empty());
  CHECK(vrost::headsetModel(nullptr, 0) == nullptr);
  if (failures) return 1;
  std::printf("headset EDID contracts passed\n");
  return 0;
}
