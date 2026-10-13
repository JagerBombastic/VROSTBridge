"""Compile the production AMF configuration method against strict codec test doubles.

The real SDK property names/enums are used. Only pointer interface types are
substituted; MakeEncoder's statements and branches are compiled unmodified.
Run directly with --alvr CHECKOUT [--before OLD_VIDEOENCODERAMF_CPP].
"""
import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path


PREAMBLE = r'''
#include <cstdio>
#include <cwchar>
#include <memory>
#include <stdexcept>
#include <set>
#include <string>
#include <type_traits>
#include "components/VideoEncoderVCE.h"
#include "components/VideoEncoderHEVC.h"
#include "components/VideoEncoderAV1.h"
#include "components/VideoConverter.h"
#include "ALVR-common/packet_types.h"
static bool query_capability = true;
struct MockCaps {
 template<class T> AMF_RESULT GetProperty(const wchar_t* name, T* out) {
   *out = std::wstring(name).find(L"QueryTimeout") != std::wstring::npos ? query_capability : true;
   return AMF_OK;
 }
};
static std::set<std::wstring> properties[3];
struct MockComponent {
 int codec, rejected=0, timeout_calls=0, init_calls=0;
 long timeout_value=-1;
 explicit MockComponent(int c): codec(c) {}
 template<class T> AMF_RESULT SetProperty(const wchar_t* name, T value) {
   if (!properties[codec].count(name)) { ++rejected; return AMF_NOT_FOUND; }
   if (std::wstring(name).find(L"QueryTimeout") != std::wstring::npos) {
     ++timeout_calls;
     if constexpr (std::is_integral<T>::value) timeout_value = value;
   }
   return AMF_OK;
 }
 AMF_RESULT GetCaps(std::shared_ptr<MockCaps>* out) {
   *out = std::make_shared<MockCaps>(); return AMF_OK;
 }
 AMF_RESULT Init(amf::AMF_SURFACE_FORMAT, int, int) { ++init_calls; return AMF_OK; }
};
namespace amf {
 using MockComponentPtr=std::shared_ptr<MockComponent>;
 using MockCapsPtr=std::shared_ptr<MockCaps>;
}
#define AMFComponentPtr MockComponentPtr
#define AMFCapsPtr MockCapsPtr
struct MockFactory {
 MockFactory* GetFactory() { return this; }
 AMF_RESULT CreateComponent(int, const wchar_t* codec, std::shared_ptr<MockComponent>* out) {
   int id = !wcscmp(codec, AMFVideoEncoderVCE_AVC) ? ALVR_CODEC_H264 :
            !wcscmp(codec, AMFVideoEncoder_HEVC) ? ALVR_CODEC_HEVC : ALVR_CODEC_AV1;
   *out=std::make_shared<MockComponent>(id); return AMF_OK;
 }
} g_AMFFactory;
Settings settings{};
const Settings* Settings_Instance() { return &settings; }
template<class... T> std::runtime_error MakeException(const char* message,T...) { return std::runtime_error(message); }
template<class... T> void Warn(const char*,T...) {}
template<class... T> void Debug(const char*,T...) {}
#define AMF_THROW_IF(expr) { AMF_RESULT result = expr; if(result != AMF_OK) throw MakeException("AMF rejected property: " #expr); }
class VideoEncoderAMF {
public:
 bool m_use10bit=false, m_hasPreAnalysis=false, m_hasQueryTimeout=false;
 int m_amfContext=0;
 unsigned m_queryWaitBudgetMs=11;
 amf::AMFComponentPtr MakeEncoder(amf::AMF_SURFACE_FORMAT,int,int,int,int,int);
};
'''

MAIN = r'''
int main(int argc,char**) {
 __PROPERTIES__
 int cases=0, failures=0;
 for(int codec : {ALVR_CODEC_H264,ALVR_CODEC_HEVC,ALVR_CODEC_AV1}) {
  for(bool cap : {false,true}) for(bool hdr : {false,true})
  for(int rate : {ALVR_CBR,ALVR_VBR}) for(int quality : {ALVR_SPEED,ALVR_BALANCED,ALVR_QUALITY})
  for(bool pre : {false,true}) for(bool vbaq : {false,true}) {
   query_capability=cap;
   settings.m_rateControlMode=rate;
   settings.m_encoderQualityPreset=quality;
   settings.m_enableHdr=hdr;
   settings.m_enableAmfPreAnalysis=pre;
   settings.m_useAmfPreproc=pre;
   settings.m_enableVbaq=vbaq;
   settings.m_h264Profile=ALVR_H264_PROFILE_HIGH;
   settings.m_entropyCoding=ALVR_CABAC;
   VideoEncoderAMF encoder;
   ++cases;
   try {
    auto component=encoder.MakeEncoder(amf::AMF_SURFACE_RGBA,4032,2240,codec,120,80);
    int want_timeout = codec==ALVR_CODEC_AV1 || cap;
    if(component->rejected || component->init_calls!=1 || component->codec!=codec ||
       component->timeout_calls!=want_timeout || (want_timeout && component->timeout_value!=11)) {
      ++failures;
      if(failures<4) printf("Incorrect codec=%d rejected=%d init=%d timeout=%d/%d\n",codec,component->rejected,component->init_calls,component->timeout_calls,want_timeout);
    }
   } catch(const std::exception& e) {
    ++failures; if(failures<4) printf("Initialization rejected codec=%d: %s\n",codec,e.what());
   }
  }
 }
 printf("AMF actual MakeEncoder: cases=%d failures=%d\n",cases,failures);
 return failures ? 1 : 0;
}
'''


def method(source):
    start = source.index('amf::AMFComponentPtr VideoEncoderAMF::MakeEncoder(')
    end = source.index('amf::AMFComponentPtr VideoEncoderAMF::MakeConverter(', start)
    return source[start:end]


def harness(source):
    body = method(source)
    preamble = PREAMBLE
    props = sorted(set(re.findall(r'->SetProperty\(\s*(AMF_\w+)\s*,', body)))
    groups = [[], [], []]
    for name in props:
        index = 2 if name.startswith('AMF_VIDEO_ENCODER_AV1_') else 1 if name.startswith('AMF_VIDEO_ENCODER_HEVC_') else 0
        groups[index].append(name)
    props_cpp = '\n'.join(f'properties[{i}]={{'+','.join(names)+'};' for i, names in enumerate(groups))
    return preamble + '\n' + body + '\n' + MAIN.replace('__PROPERTIES__', props_cpp)


def compile_and_run(alvr, source, work, label):
    cpp = work / f'{label}.cpp'
    binary = work / label
    cpp.write_text(harness(source))
    root = alvr / 'alvr/server_openvr/cpp'
    build = subprocess.run(['g++', '-std=c++17', '-O0', '-I', str(root), '-I', str(root / 'shared/amf/public/include'), str(cpp), '-o', str(binary)], capture_output=True, text=True)
    if build.returncode:
        raise RuntimeError(build.stderr)
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    return {'returncode': result.returncode, 'output': result.stdout.strip()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alvr', type=Path, required=True)
    parser.add_argument('--before', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    source = args.alvr / 'alvr/server_openvr/cpp/platform/win32/VideoEncoderAMF.cpp'
    with tempfile.TemporaryDirectory(prefix='vrostbridge-amf-codec-') as tmp:
        work = Path(tmp)
        result = {'method': 'Production MakeEncoder compiled against strict codec property test doubles using real AMF SDK constants; no GPU execution.'}
        if args.before:
            result['before'] = compile_and_run(args.alvr, args.before.read_text(), work, 'before')
            assert result['before']['returncode'] != 0, 'Baseline should demonstrate codec property fallthrough'
        result['after'] = compile_and_run(args.alvr, source.read_text(), work, 'after')
        assert result['after']['returncode'] == 0, result['after']['output']
        text = json.dumps(result, indent=2)
        print(text)
        if args.output:
            args.output.write_text(text + '\n')


if __name__ == '__main__':
    main()
