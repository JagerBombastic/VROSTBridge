#pragma once
#include <array>
#include <cmath>
#include <cstdint>
#include <limits>

namespace vrost {
struct Vec3 { float x=0,y=0,z=0; };
struct Pose {
    float m[3][4] = {{1,0,0,0},{0,1,0,0},{0,0,1,0}};
};
inline Vec3 rotate(const Pose& p, Vec3 v) {
    return {p.m[0][0]*v.x+p.m[0][1]*v.y+p.m[0][2]*v.z,
            p.m[1][0]*v.x+p.m[1][1]*v.y+p.m[1][2]*v.z,
            p.m[2][0]*v.x+p.m[2][1]*v.y+p.m[2][2]*v.z};
}
inline Vec3 transform(const Pose& p, Vec3 v) {
    auto r=rotate(p,v); return {r.x+p.m[0][3],r.y+p.m[1][3],r.z+p.m[2][3]};
}
inline Pose compose(const Pose& a,const Pose& b) {
    Pose r{};
    for(int i=0;i<3;++i) {
        for(int j=0;j<3;++j) {
            r.m[i][j]=0;
            for(int k=0;k<3;++k) r.m[i][j]+=a.m[i][k]*b.m[k][j];
        }
        r.m[i][3]=a.m[i][3];
        for(int k=0;k<3;++k) r.m[i][3]+=a.m[i][k]*b.m[k][3];
    }
    return r;
}
inline Pose inverseRigid(const Pose& p) {
    Pose r{};
    for(int i=0;i<3;++i) {
        for(int j=0;j<3;++j) r.m[i][j]=p.m[j][i];
        r.m[i][3]=0;
        for(int j=0;j<3;++j) r.m[i][3]-=r.m[i][j]*p.m[j][3];
    }
    return r;
}
struct Fov { float left=-1,right=1,top=-1,bottom=1; };
inline Vec3 viewRay(Fov f,float u,float v) {
    Vec3 r{f.left+(f.right-f.left)*u,-(f.top+(f.bottom-f.top)*v),-1};
    const float len=std::sqrt(r.x*r.x+r.y*r.y+r.z*r.z);
    return {r.x/len,r.y/len,r.z/len};
}
struct FrameMeta {
    bool valid=false,ready=false;
    std::uint64_t sequence=0,epoch=0;
    double captureMs=0;
    Pose sourcePose{};
};
// Only completed images are candidates. Never change the selected source pose.
// captureMs is local query time; it is NOT a measured sensor/display timestamp.
template<std::size_t N>
int selectFrame(const std::array<FrameMeta,N>& frames,double now,double delay,std::uint64_t epoch) {
    int best=-1;
    if(!std::isfinite(now)||!std::isfinite(delay)||delay<0) return best;
    for(std::size_t i=0;i<N;++i) {
        const auto& f=frames[i];
        const double age=now-f.captureMs;
        if(!f.valid||!f.ready||f.epoch!=epoch||!std::isfinite(age)||age<delay||age>=100) continue;
        if(best<0||f.sequence>frames[static_cast<std::size_t>(best)].sequence) best=static_cast<int>(i);
    }
    return best;
}
enum class ViewState { Waiting, Scene, Recovery };
inline ViewState viewState(bool tracking,bool focus,int frame) {
    if(!tracking||!focus) return ViewState::Recovery;
    return frame<0?ViewState::Waiting:ViewState::Scene;
}
}
