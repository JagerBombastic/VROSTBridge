#pragma once
#include "core/contracts.hpp"
#include <cstddef>

namespace vrost {
// Layout observed in Bigscreen's published VRCFT interface at the pinned revision.
// No timestamps are converted to physical capture time until their units are known.
struct alignas(8) SharedGaze {
    float leftX,leftY,leftZ,rightX,rightY,rightZ,combinedX,combinedY,combinedZ,confidence;
    std::int64_t timestamp;
    std::int32_t isValid;
    float leftClosed,rightClosed;
};
static_assert(sizeof(SharedGaze)==64);
static_assert(offsetof(SharedGaze,timestamp)==40);
static_assert(offsetof(SharedGaze,isValid)==48);
inline bool validGaze(const SharedGaze& g){
    const float values[]={g.leftX,g.leftY,g.leftZ,g.rightX,g.rightY,g.rightZ,g.combinedX,g.combinedY,g.combinedZ,g.confidence,g.leftClosed,g.rightClosed};
    for(float v:values)if(!std::isfinite(v))return false;
    auto length2=[](float x,float y,float z){return x*x+y*y+z*z;};
    return g.isValid==1&&g.confidence>=0&&g.confidence<=1&&g.leftClosed>=0&&g.leftClosed<=1&&g.rightClosed>=0&&g.rightClosed<=1&&length2(g.combinedX,g.combinedY,g.combinedZ)>1e-6f;
}
class GazeFreshness {
    std::int64_t timestamp_=0;
    double changedAt_=0;
    bool seen_=false,advanced_=false;
public:
    void reset(){seen_=false;advanced_=false;}
    bool accept(const SharedGaze& sample,double now){
        if(!std::isfinite(now)||!validGaze(sample)){reset();return false;}
        if(!seen_){timestamp_=sample.timestamp;changedAt_=now;seen_=true;return false;}
        if(sample.timestamp<timestamp_){reset();return false;}
        if(sample.timestamp!=timestamp_){timestamp_=sample.timestamp;changedAt_=now;advanced_=true;}
        return advanced_&&now>=changedAt_&&now-changedAt_<50;
    }
};
}
