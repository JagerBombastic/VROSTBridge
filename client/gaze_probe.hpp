#pragma once
#include "client/common.hpp"
#include "core/gaze.hpp"
#include <cstring>
#include <iomanip>
namespace vrost {
// Read-only opt-in diagnostic. No shared-memory writes, forwarding or recording.
class GazeProbe {
    HANDLE mapping_=nullptr;const void* view_=nullptr;double retryAt_=0;
    GazeFreshness freshness_;
public:
    bool enabled=false;
    std::string status="Gaze probe off";
    ~GazeProbe(){close();}
    void close(){if(view_)UnmapViewOfFile(view_);if(mapping_)CloseHandle(mapping_);view_=nullptr;mapping_=nullptr;freshness_.reset();}
    void poll(double now){
        if(!enabled){close();status="Gaze probe off";return;}
        if(!view_&&now>=retryAt_){
            retryAt_=now+1000;
            mapping_=OpenFileMappingW(FILE_MAP_READ,FALSE,L"VRCFTMemmapData");
            if(mapping_)view_=MapViewOfFile(mapping_,FILE_MAP_READ,0,0,sizeof(SharedGaze));
            if(!view_){close();status="Gaze: waiting for Beyond vendor shared memory";return;}
        }
        if(!view_)return;
        SharedGaze a{},b{};std::memcpy(&a,view_,sizeof(a));MemoryBarrier();std::memcpy(&b,view_,sizeof(b));
        // Best-effort torn-read rejection. The public layout supplies no seqlock.
        if(std::memcmp(&a,&b,offsetof(SharedGaze,rightClosed)+sizeof(float))!=0){status="Gaze: inconsistent snapshot discarded";return;}
        if(!freshness_.accept(b,now)){status="Gaze: invalid, unchanging or awaiting two fresh timestamps";
            if(now>=retryAt_){close();retryAt_=now+1000;}return;}
        std::ostringstream s;s<<std::fixed<<std::setprecision(2)<<"Gaze raw XYZ "<<b.combinedX<<", "<<b.combinedY<<", "<<b.combinedZ<<" | confidence "<<b.confidence<<" | open L/R "<<1-b.leftClosed<<"/"<<1-b.rightClosed;
        status=s.str();
    }
};
}
