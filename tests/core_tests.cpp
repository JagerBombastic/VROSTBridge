#include "core/contracts.hpp"
#include "core/gaze.hpp"
#include <iostream>
#include <stdexcept>
using namespace vrost;
int checks=0;
void require(bool b,const char* why) { ++checks; if(!b) throw std::runtime_error(why); }
bool near(float a,float b) { return std::abs(a-b)<1e-5f; }
int main() {
 try {
    Pose p{{{0,0,1,2},{0,1,0,3},{-1,0,0,4}}};
    auto r=transform(inverseRigid(p),transform(p,{.2f,-.8f,1.4f}));
    require(near(r.x,.2f)&&near(r.y,-.8f)&&near(r.z,1.4f),"rigid round trip");
    Pose eye; eye.m[0][3]=-.032f;
    auto c=compose(p,eye);
    require(near(c.m[2][3],4.032f),"eye offset must rotate with head");
    auto a=viewRay({-1.2f,.8f,-.9f,1.1f},0,0);
    auto b=viewRay({-1.2f,.8f,-.9f,1.1f},1,1);
    require(a.x<0&&a.y>0&&a.z<0&&b.x>0&&b.y<0,"projection orientation");
    require(viewRay({-1.2f,.8f,-1,1},.5f,.5f).x<0,"asymmetric projection retained");
    std::array<FrameMeta,4> frames{};
    for(int i=0;i<4;++i) { frames[i].valid=true; frames[i].ready=true;frames[i].sequence=i+1;frames[i].epoch=7;frames[i].captureMs=10*i;frames[i].sourcePose.m[0][3]=float(i); }
    require(selectFrame(frames,35,0,7)==3,"latest completed");
    require(selectFrame(frames,35,11,7)==2,"11 ms delay");
    require(selectFrame(frames,35,22,7)==1,"22 ms delay");
    require(selectFrame(frames,35,33,7)==0,"33 ms delay");
    frames[3].ready=false;
    require(selectFrame(frames,35,0,7)==2,"pending GPU frame skipped");
    require(selectFrame(frames,35,0,8)==-1,"old epoch rejected");
    require(selectFrame(frames,130,0,7)==-1,"100 ms stale cutoff");
    require(selectFrame(frames,-1,0,7)==-1,"future frames rejected");
    require(selectFrame(frames,35,-1,7)==-1,"negative delay rejected");
    require(selectFrame(frames,std::numeric_limits<double>::quiet_NaN(),0,7)==-1,"invalid clock rejected");
    require(frames[2].sourcePose.m[0][3]==2,"source pose unchanged by selection");
    require(viewState(false,true,1)==ViewState::Recovery,"tracking loss");
    require(viewState(true,false,1)==ViewState::Recovery,"focus loss");
    require(viewState(true,true,-1)==ViewState::Waiting,"no usable frame");
    require(viewState(true,true,1)==ViewState::Scene,"valid scene");
    SharedGaze gaze{};gaze.combinedZ=-1;gaze.confidence=.8f;gaze.isValid=1;gaze.timestamp=100;
    GazeFreshness fresh;
    require(!fresh.accept(gaze,0),"first gaze sample cannot establish freshness");
    gaze.timestamp=101;require(fresh.accept(gaze,10),"advancing gaze timestamp");
    require(fresh.accept(gaze,59),"recent unchanged gaze sample");
    require(!fresh.accept(gaze,60),"frozen gaze rejected after 50 ms");
    gaze.timestamp=99;require(!fresh.accept(gaze,61),"provider timestamp reset");
    gaze.confidence=std::numeric_limits<float>::quiet_NaN();require(!validGaze(gaze),"nonfinite gaze rejected");
    gaze.confidence=.8f;gaze.isValid=0;require(!validGaze(gaze),"provider-invalid gaze rejected");
    std::cout<<checks<<" core checks passed\n";
    return 0;
 } catch(const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
