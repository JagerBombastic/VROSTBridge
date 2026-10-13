#include "host/chaperone_geometry.hpp"
#include <cassert>
#include <limits>
struct Matrix { float m[3][4]; };
struct Vertex { float v[3]; };
struct Quad { Vertex vCorners[4]; };
int main() {
    Matrix identity{{{1,0,0,0},{0,1,0,0},{0,0,1,0}}};
    Quad walls[4]{};
    const float x[4]{-1.5f,-1.5f,1.5f,1.5f}, z[4]{-1.125f,1.125f,1.125f,-1.125f};
    for(int edge=0;edge<4;++edge) {
        int next=(edge+1)%4;
        walls[edge]={{{{x[edge],0,z[edge]}},{{x[next],0,z[next]}},{{x[next],2,z[next]}},{{x[edge],2,z[edge]}}}};
    }
    auto matches=[&] {return vrostbridge::matchingChaperoneGeometry(3,2.25f,walls,4,identity,identity);};
    assert(matches());
    // Regression: upstream native code used full instead of half extents.
    for(auto& wall:walls)for(auto& v:wall.vCorners){v.v[0]*=2;v.v[2]*=2;}
    assert(!matches());
    for(auto& wall:walls)for(auto& v:wall.vCorners){v.v[0]/=2;v.v[2]/=2;}
    assert(matches());
    auto first=walls[0];walls[0]=walls[1];assert(!matches());walls[0]=first;
    assert(!vrostbridge::matchingChaperoneGeometry(3,2.25f,walls,3,identity,identity));
    Matrix shifted=identity;shifted.m[0][3]=.01f;
    assert(!vrostbridge::matchingChaperoneGeometry(3,2.25f,walls,4,shifted,identity));
    assert(!vrostbridge::matchingChaperoneGeometry(3,2.25f,walls,4,identity,shifted));
    walls[0].vCorners[0].v[1]=std::numeric_limits<float>::quiet_NaN();assert(!matches());
}
