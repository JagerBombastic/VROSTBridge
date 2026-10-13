#pragma once
#include <float.h>
#include <stdint.h>

namespace vrostbridge {
// Coordinates are already in the physical client's standing universe. Remote
// chaperone must use that identity origin and the *half* extents of its rectangle.
inline bool finiteChaperone(float value) { return value >= -FLT_MAX && value <= FLT_MAX; }
inline bool nearChaperone(float actual, float expected) {
    const float difference = actual - expected;
    return finiteChaperone(actual) && difference >= -0.001f && difference <= 0.001f;
}
template<class Matrix> bool identityChaperone(const Matrix& value) {
    for (int row = 0; row < 3; ++row)
        for (int column = 0; column < 4; ++column)
            if (!nearChaperone(value.m[row][column], row == column ? 1.f : 0.f)) return false;
    return true;
}
template<class Setup> bool matchingChaperone(Setup* setup, float width, float depth) {
    // Defined on both OpenVR's C++ interface and generated C function table.
    float actualWidth = 0, actualDepth = 0;
    if (!setup->GetWorkingPlayAreaSize(&actualWidth, &actualDepth) ||
        !nearChaperone(actualWidth, width) || !nearChaperone(actualDepth, depth)) return false;
    return true;
}
template<class Quad, class Matrix>
bool matchingChaperoneGeometry(float width, float depth, const Quad* walls, uint32_t count,
                              const Matrix& standing, const Matrix& seated) {
    if (!finiteChaperone(width) || !finiteChaperone(depth) || width <= 0 || depth <= 0 ||
        width > 100 || depth > 100 || count != 4 || !walls ||
        !identityChaperone(standing) || !identityChaperone(seated)) return false;
    unsigned edges = 0;
    for (uint32_t wall = 0; wall < count; ++wall) {
        unsigned corners = 0;
        for (const auto& vertex : walls[wall].vCorners) {
            if (!finiteChaperone(vertex.v[1])) return false;
            int x = nearChaperone(vertex.v[0], -width / 2) ? 0 :
                    nearChaperone(vertex.v[0], width / 2) ? 1 : -1;
            int z = nearChaperone(vertex.v[2], -depth / 2) ? 0 :
                    nearChaperone(vertex.v[2], depth / 2) ? 1 : -1;
            if (x < 0 || z < 0) return false;
            corners |= 1u << (x * 2 + z);
        }
        unsigned edge = corners == 3 ? 1 : corners == 12 ? 2 :
                        corners == 5 ? 4 : corners == 10 ? 8 : 0;
        if (!edge || (edges & edge)) return false;
        edges |= edge;
    }
    return edges == 15;
}
}
