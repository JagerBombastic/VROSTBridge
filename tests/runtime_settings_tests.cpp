#include "client/platform/linux/runtime_settings.hpp"
#include <cassert>
#include <map>
#include <sstream>
#ifdef _WIN32
#include <process.h>
#define getpid _getpid
#else
#include <unistd.h>
#endif

int main() {
  assert(vrost::jsonString("Index \"quoted\"\\line\n") == "\"Index \\\"quoted\\\"\\\\line\\u000a\"");
  std::ostringstream metadata;
  assert(!vrost::printHeadsetMetadata(nullptr,metadata) && metadata.str().empty());
  // The exact reported startup race: real 120 Hz scanout with a stale 90 Hz
  // property must not pass readiness, regardless of repeated identical reads.
  vrost::DisplayReadiness display;
  for(uint64_t i=0;i<40;++i)
    assert(!display.observe(10+i/120.0+.001,true,.001f,100+i,90,1364,1516));
  bool ready=false;
  for(uint64_t i=40;i<60;++i)
    ready=display.observe(10+i/120.0+.001,true,.001f,100+i,120,1364,1516)||ready;
  assert(ready && std::abs(display.measuredHz-120)<.01);
  // Frozen counters, missing timestamps, changed dimensions and counter resets
  // cannot reuse old readiness; all require new display progress.
  display.reset();
  for(int i=0;i<30;++i) assert(!display.observe(20+i*.01,true,.001f,500,120,1364,1516));
  assert(!display.observe(21,false,0,600,120,1364,1516));
  assert(!display.observe(21,true,.001f,600,120,1364,1516));
  assert(!display.observe(21.01,true,.001f,601,120,2016,2240));
  assert(!display.observe(21.02,true,.001f,2,120,2016,2240));
  assert(!display.observe(21.03,true,.1f,3,120,2016,2240));
  for(float hz:{75.f,90.f,120.f,144.f}) {
    display.reset();ready=false;
    for(uint64_t i=0;i<32;++i)
      ready=display.observe(30+i/double(hz)+.001,true,.001f,100+i,hz,2560,2560)||ready;
    assert(ready && std::abs(display.measuredHz-hz)<.02);
  }
  // Beyond Linux report: scanout mode 0 is 75 Hz while the property stays 90.
  // Resolve only this known pair, after sustained fresh vsync measurements.
  vrost::BeyondDisplayReadiness beyond;
  for(uint64_t i=0;i<113;++i)
    assert(!beyond.observe("Beyond",40+i/75.0+.001,true,.001f,200+i,90,1424,1424));
  assert(beyond.observe("Beyond",40+113/75.0+.001,true,.001f,313,90,1424,1424));
  assert(std::abs(beyond.measurement().measuredHz-75)<.01);
  vrost::VerifiedDisplayTiming resolved{"Beyond",90,75,beyond.measurement().measuredHz};
  assert(resolved.matches("Beyond",90));
  assert(!resolved.matches("Index",90));
  assert(!resolved.matches("Beyond",75)); // A mode change requires a fresh probe.
  resolved.measuredHz=90; assert(!resolved.matches("Beyond",90));
  resolved.measuredHz=std::nan(""); assert(!resolved.matches("Beyond",90));
  // Index, unrelated hardware, unsupported rates and incomplete observations
  // never qualify for this exception, even when values look plausible.
  for(const char* model:{"Index","Other",""}) {
    beyond={};
    for(uint64_t i=0;i<200;++i)
      assert(!beyond.observe(model,50+i/75.0+.001,true,.001f,400+i,90,1424,1424));
  }
  beyond={};
  for(uint64_t i=0;i<200;++i)
    assert(!beyond.observe("Bigscreen Beyond 2e",60+i/90.0+.001,true,.001f,400+i,75,1424,1424));
  beyond={};
  for(uint64_t i=0;i<200;++i)
    assert(!beyond.observe("Beyond",70+i/60.0+.001,true,.001f,400+i,90,1424,1424));
  beyond={};
  for(uint64_t i=0;i<200;++i)
    assert(!beyond.observe("Beyond",80+i/75.0+.001,true,.001f,400,90,1424,1424));
  for(uint64_t i=0;i<200;++i)
    assert(!beyond.observe("Beyond",90+i/75.0+.001,false,.001f,400+i,90,1424,1424));
  // A reset, invalid sample or geometry change breaks accumulated evidence.
  for(int kind=0;kind<3;++kind) {
    beyond={};
    for(uint64_t i=0;i<100;++i)
      assert(!beyond.observe("Beyond",100+i/75.0+.001,true,.001f,400+i,90,1424,1424));
    assert(!beyond.observe("Beyond",100+100/75.0+.001,kind!=0,.001f,kind==1?1:500,90,kind==2?1500:1424,1424));
  }
  const auto receipt=std::filesystem::temp_directory_path()/("vrost-runtime-test-"+std::to_string(getpid()));
  std::map<std::string,bool> values{{"enableHomeApp",true},{"enableDashboard",true},{"sendSystemButtonToAllApps",false}};
  auto get=[&](const char*,const char* key){return values.at(key);};
  auto set=[&](const char*,const char* key,bool value){values.at(key)=value;};
  vrost::RuntimePreferences original;
  vrost::writeRuntimePreferences(receipt,original);
  auto loaded=vrost::readRuntimePreferences(receipt);
  assert(loaded.home && loaded.dashboard && !loaded.system && loaded.quiet && !loaded.legacy);
  vrost::applyRuntimePreferences(loaded,false,get,set);
  assert(!values["enableHomeApp"] && !values["enableDashboard"] && values["sendSystemButtonToAllApps"]);
  vrost::applyRuntimePreferences(loaded,true,get,set);
  assert(values["enableHomeApp"] && values["enableDashboard"] && !values["sendSystemButtonToAllApps"]);

  // Full mode makes System events available without changing Home/dashboard.
  loaded.quiet=false;
  vrost::applyRuntimePreferences(loaded,false,get,set);
  assert(values["enableHomeApp"] && values["enableDashboard"] && values["sendSystemButtonToAllApps"]);
  vrost::applyRuntimePreferences(loaded,true,get,set);
  assert(!values["sendSystemButtonToAllApps"]);

  // User changes take precedence over our restore operation.
  loaded.quiet=true; loaded.home=false; loaded.dashboard=false; loaded.system=true;
  vrost::applyRuntimePreferences(loaded,false,get,set);
  values["enableHomeApp"]=true; values["enableDashboard"]=true; values["sendSystemButtonToAllApps"]=false;
  vrost::applyRuntimePreferences(loaded,true,get,set);
  assert(values["enableHomeApp"] && values["enableDashboard"] && !values["sendSystemButtonToAllApps"]);

  // Old receipts restore Home and (when quiet) dashboard, leaving System alone.
  {std::ofstream out(receipt);out<<"VROSTBridgeRuntime1 1 1 1\n";}
  loaded=vrost::readRuntimePreferences(receipt);
  values["enableHomeApp"]=false;values["enableDashboard"]=false;values["sendSystemButtonToAllApps"]=true;
  vrost::applyRuntimePreferences(loaded,true,get,set);
  assert(values["enableHomeApp"] && values["enableDashboard"] && values["sendSystemButtonToAllApps"]);
  {std::ofstream out(receipt);out<<"VROSTBridgeRuntime1 1 1 0\n";}
  loaded=vrost::readRuntimePreferences(receipt);
  values["enableHomeApp"]=false;values["enableDashboard"]=false;
  vrost::applyRuntimePreferences(loaded,true,get,set);
  assert(values["enableHomeApp"] && !values["enableDashboard"] && values["sendSystemButtonToAllApps"]);

  for(const char* bad:{"VROSTBridgeRuntime2 1 1 2 1", "VROSTBridgeRuntime2 1 1 0", "VROSTBridgeRuntime1 1 1 1 rubbish", "unknown 1 1 1"}) {
    {std::ofstream out(receipt);out<<bad;}
    bool rejected=false;
    try{vrost::readRuntimePreferences(receipt);}catch(const std::runtime_error&){rejected=true;}
    assert(rejected);
  }
  bool rejected=false;
  loaded=vrost::RuntimePreferences{};
  try {vrost::applyRuntimePreferences(loaded,false,get,[](const char*,const char*,bool){});}
  catch(const std::runtime_error&){rejected=true;}
  assert(rejected); // Runtime readback must reflect the requested write.
  std::filesystem::remove(receipt);
  std::cout<<"Runtime preference migration, readback and restoration tests passed\n";
}
